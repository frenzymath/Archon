"""`archon branch` — time-travel and forking on the inner git.

The mathematician uses `archon branch` both to switch between existing
timelines and to fork a new one at any past commit. Branches live in the
inner git at `.archon/git-dir/`; the outer (mathematician's) git repo is
never modified.

Two shapes, one verb:

    archon branch <name>                 # switch to existing <name>
    archon branch <name> --from <commit> # fork new <name> at <commit>, switch
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import typer

from archon import log
from archon.commands.tooling.git import Git
from archon.commands.tooling.inner_git import InnerGit, _safe_branch_name
from archon.commands.tooling.version import warn_if_mismatch


# ── helpers ───────────────────────────────────────────────────────────


def _resolve_project(project_path: str) -> tuple[Path, InnerGit]:
    resolved = Path(project_path).resolve()
    if not (resolved / ".archon").is_dir():
        log.error(f"Not an Archon project: {resolved}")
        raise typer.Exit(1)
    inner = InnerGit(resolved)
    if not inner.is_initialized():
        log.error("Inner git not initialized. Run: archon init <path>")
        raise typer.Exit(1)
    # Migrate older projects whose info/exclude still drops .raw.jsonl.
    inner.ensure_excludes()
    return resolved, inner


def _refuse_if_outer_dirty(project_path: Path, force_hint: str | None = None) -> None:
    """Refuse with a clear error if the outer (mathematician's) tree is dirty.

    Switching branches rewrites files on disk — if the mathematician has
    uncommitted work, that work is at risk. Block until they commit or
    stash. If the caller supports a bypass, pass ``force_hint`` to
    surface it in the error message.
    """
    outer = Git(project_path, auto_init=False)
    if not outer.is_repo():
        return
    if outer.is_dirty():
        log.error(
            "The outer git repo is dirty — there are uncommitted changes "
            "in your working tree. Switching branches rewrites files on disk."
        )
        log.step("Commit or stash your work first:")
        log.step("  git add -A && git commit -m '<message>'")
        log.step("  # or:")
        log.step("  git stash")
        if force_hint:
            log.step("")
            log.step("Or bypass this check and overwrite your uncommitted work:")
            log.step(f"  {force_hint}")
        raise typer.Exit(1)


# ── branch ────────────────────────────────────────────────────────────


def branch(
    name: str = typer.Argument(
        ...,
        help="Branch name — existing branch to switch to, or new branch to create.",
    ),
    project_path: str = typer.Argument(".", help="Path to Lean project."),
    from_ref: Optional[str] = typer.Option(
        None, "--from", "-f",
        help="Create a new branch at this commit/ref. Omit to switch to an "
             "existing branch named <name>.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Bypass the outer-dirty check and overwrite any inner-tracked "
             "changes. Dangerous — may overwrite uncommitted work.",
    ),
) -> None:
    """Switch to an existing inner-git branch, or fork a new one.

    [bold]Switch to an existing branch:[/bold]
      [cyan]archon branch main .[/cyan]
      [cyan]archon branch bolzano-weierstrass .[/cyan]

    [bold]Fork a new branch at a past commit (time-travel):[/bold]
      [cyan]archon branch alt-strategy . --from b69ab81[/cyan]
      [cyan]archon branch alt-strategy . --from main[/cyan]

    Forking leaves every other branch untouched — your original timeline
    is still reachable via [cyan]archon branch <its-name> .[/cyan]
    """
    resolved, inner = _resolve_project(project_path)
    warn_if_mismatch(resolved)

    safe = _safe_branch_name(name)
    if safe != name:
        log.info(f"Branch name normalized: {name!r} → {safe!r}")

    existed = inner.has_branch(safe)

    if from_ref is not None and existed:
        log.error(f"Branch already exists: {safe}")
        log.step(f"To switch to it, drop --from: archon branch {safe} {project_path}")
        raise typer.Exit(1)

    if from_ref is None and not existed:
        log.error(f"No branch named {safe!r}.")
        log.step("To create it at the current commit:")
        log.step(f"  archon branch {safe} {project_path} --from HEAD")
        log.step("To create it at a past commit:")
        log.step(f"  archon branch {safe} {project_path} --from <commit>")
        raise typer.Exit(1)

    force_hint = (
        f"archon branch {name} {project_path}"
        + (f" --from {from_ref}" if from_ref else "")
        + " --force"
    )

    if not force:
        _refuse_if_outer_dirty(resolved, force_hint=force_hint)

    if inner.is_dirty() and not force:
        log.warn(
            "Inner git has uncommitted agent work (e.g. a mid-iteration "
            "Ctrl-C). Switching may refuse with a conflict or carry those "
            "changes across."
        )
        log.step(f"To plow through, re-run with --force: {force_hint}")
        if not typer.confirm("Continue anyway?", default=False):
            raise typer.Exit(0)

    if not existed:
        try:
            inner.create_branch(safe, from_ref=from_ref or "HEAD")
        except Exception as e:
            log.error(f"Failed to create branch {safe}: {e}")
            raise typer.Exit(1)
        log.success(f"Created branch {safe} at {from_ref}")

    try:
        inner.checkout(safe, force=force)
    except Exception as e:
        log.error(f"Failed to switch to {safe}: {e}")
        raise typer.Exit(1)

    log.success(f"Switched to: {safe}")
    sha = inner.head_sha(short=True)
    if sha:
        log.step(f"Inner HEAD: {sha}  ({inner.last_commit_subject() or '?'})")

    # Remove state directories on disk that aren't part of the target
    # commit's tree. `git checkout` only removes tracked FILES, not
    # directories, so hollow dirs survive — iter-NNN/ under .archon/logs/
    # linger and bump next_iter_num; session_N/ under proof-journal/
    # lingers and pollutes the Journal view with cross-branch sessions.
    # Strictly scoped to known state-dir patterns so nothing else can be
    # affected.
    _drop_stale_state_dirs(resolved, inner)


# (parent dir under .archon, prefix that identifies state subdirs)
_STALE_STATE_DIRS = [
    ("logs", "iter-"),
    ("proof-journal/sessions", "session_"),
]


def _drop_stale_state_dirs(project_path: Path, inner: InnerGit) -> None:
    """Remove iter-*/session_* dirs on disk that aren't in HEAD's tree.

    Only removes when we have a concrete tracked listing to compare
    against. If `.archon/<parent_rel>` isn't tracked at HEAD at all
    (projects created before archon learned to track .archon/), we
    leave disk untouched and warn — blindly wiping would destroy the
    user's in-flight state.
    """
    removed_total: list[str] = []
    untracked_parents: list[str] = []
    for parent_rel, prefix in _STALE_STATE_DIRS:
        parent = project_path / ".archon" / Path(parent_rel)
        if not parent.is_dir():
            continue
        tree_path = f".archon/{parent_rel}"
        r = inner._run(
            ["ls-tree", "--name-only", f"HEAD:{tree_path}"],
            check=False,
        )
        if r.returncode != 0:
            # Path isn't tracked at HEAD — legacy commit that predates
            # .archon/ being tracked. Can't distinguish "this dir
            # belongs to the target state" from "this dir is a leftover
            # from another branch", so leave everything as-is.
            untracked_parents.append(tree_path)
            continue
        tracked = {
            line.strip().rstrip("/").split("/")[-1]
            for line in r.stdout.splitlines()
            if line.strip()
        }
        for entry in sorted(parent.iterdir()):
            if not entry.is_dir() or not entry.name.startswith(prefix):
                continue
            if entry.name in tracked:
                continue
            try:
                shutil.rmtree(entry)
            except OSError as e:
                log.warn(f"Could not remove stale {entry}: {e}")
                continue
            removed_total.append(f"{parent_rel}/{entry.name}")

    if removed_total:
        log.info(
            f"Removed {len(removed_total)} stale state dir(s) "
            f"not present at this commit: {', '.join(removed_total)}"
        )
    if untracked_parents:
        log.warn(
            "This commit predates archon tracking .archon/ state — "
            "time-travel restores .lean/blueprint files only, not agent "
            "state. Untracked state dirs: " + ", ".join(untracked_parents)
        )
        log.step(
            "On the next `archon loop`, the updated excludes will commit "
            "the current .archon/ state so future forks are lossless."
        )


# ── log (small helper, handy for archon users) ────────────────────────


def inner_log(
    project_path: str = typer.Argument(".", help="Path to Lean project."),
    n: int = typer.Option(20, "-n", help="Number of commits to show."),
) -> None:
    """Show the inner-git commit graph (one-line format)."""
    resolved, inner = _resolve_project(project_path)
    output = inner.log_oneline(n=n)
    if not output.strip():
        log.info("Inner git has no commits yet.")
        return
    log.header(f"Inner git log (last {n})")
    typer.echo(output)

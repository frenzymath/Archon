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


# (parent dir under .archon, prefix that identifies state subdirs)
_STALE_STATE_DIRS = [
    ("logs", "iter-"),
    ("proof-journal/sessions", "session_"),
]


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


# ── command classes ───────────────────────────────────────────────────


class BranchCommand:
    """Switch to an existing inner-git branch, or fork a new one."""

    def __init__(
        self,
        name: str,
        project_path: str,
        *,
        from_ref: str | None = None,
        force: bool = False,
    ) -> None:
        self.name = name
        self.project_path = project_path
        self.from_ref = from_ref
        self.force = force

    def run(self) -> None:
        resolved, inner = _resolve_project(self.project_path)
        warn_if_mismatch(resolved)

        safe = _safe_branch_name(self.name)
        if safe != self.name:
            log.info(f"Branch name normalized: {self.name!r} → {safe!r}")

        existed = inner.has_branch(safe)
        self._validate_args(safe, existed)

        force_hint = self._build_force_hint()

        if not self.force:
            self._refuse_if_outer_dirty(resolved, force_hint)

        if inner.is_dirty():
            self._gate_inner_dirty(force_hint)

        # Capture the pre-fork ref so a failed checkout can be rolled
        # back. ``current_branch`` is None on a detached HEAD; in that
        # case ``head_sha`` is the only fallback. Either way, ``_switch``
        # below restores this state on exception.
        pre_branch = inner.current_branch()
        pre_sha = inner.head_sha(short=False)
        created_this_call = False
        if not existed:
            self._create_branch(inner, safe)
            created_this_call = True

        try:
            self._switch(inner, safe)
        except typer.Exit:
            self._rollback(
                inner, safe,
                created=created_this_call,
                pre_branch=pre_branch, pre_sha=pre_sha,
            )
            raise

        self._drop_stale_state_dirs(resolved, inner)

    # ── private ────────────────────────────────────────────────────────

    def _validate_args(self, safe: str, existed: bool) -> None:
        if self.from_ref is not None and existed:
            log.error(f"Branch already exists: {safe}")
            log.step(
                f"To switch to it, drop --from: archon branch {safe} {self.project_path}",
            )
            raise typer.Exit(1)

        if self.from_ref is None and not existed:
            log.error(f"No branch named {safe!r}.")
            log.step("To create it at the current commit:")
            log.step(f"  archon branch {safe} {self.project_path} --from HEAD")
            log.step("To create it at a past commit:")
            log.step(f"  archon branch {safe} {self.project_path} --from <commit>")
            raise typer.Exit(1)

    def _build_force_hint(self) -> str:
        return (
            f"archon branch {self.name} {self.project_path}"
            + (f" --from {self.from_ref}" if self.from_ref else "")
            + " --force"
        )

    def _gate_inner_dirty(self, force_hint: str) -> None:
        """Block (or confirm-through) a branch switch on a dirty inner git.

        Previously ``--force`` skipped this check entirely. That turned
        out to be exactly the failure path the reviewer hit: an inner
        git left with a partially-written ``index`` (e.g. SIGKILL during
        the plan phase), ``--force`` bypassed every guard, and ``git
        checkout -f`` ran against the corrupt index and trashed the
        repo. Now both modes warn; ``--force`` requires a second
        explicit confirmation rather than silently steamrolling.
        """
        if self.force:
            log.warn(
                "Inner git has uncommitted agent work AND --force was "
                "passed. Forcing a switch onto a dirty inner tree has "
                "been observed to corrupt the inner repo when the index "
                "is in a partially-written state (e.g. SIGKILL during a "
                "phase).",
            )
            log.step("Recommended recovery before retrying:")
            log.step("  git --git-dir=.archon/git-dir status   # inspect")
            log.step("  git --git-dir=.archon/git-dir reset --hard HEAD")
            log.step("Then re-run WITHOUT --force.")
            if not typer.confirm(
                "Proceed with --force anyway (last chance to back out)?",
                default=False,
            ):
                raise typer.Exit(1)
            return

        log.warn(
            "Inner git has uncommitted agent work (e.g. a mid-iteration "
            "Ctrl-C). Switching may refuse with a conflict or carry those "
            "changes across.",
        )
        log.step(f"To plow through, re-run with --force: {force_hint}")
        if not typer.confirm("Continue anyway?", default=False):
            raise typer.Exit(0)

    @staticmethod
    def _rollback(
        inner: InnerGit,
        new_branch: str,
        *,
        created: bool,
        pre_branch: str | None,
        pre_sha: str | None,
    ) -> None:
        """Undo a partial fork after a failed checkout.

        Without this, a failed ``_switch`` left the just-created
        ``new_branch`` pointing at a commit that no other ref reached,
        and HEAD potentially mid-move — the exact "branch alt at <sha>
        but inner repo unreachable through archon" failure the reviewer
        hit. We delete the new branch (if WE created it) and force HEAD
        back to its pre-fork state.

        Best-effort: every step swallows failures rather than masking
        the original checkout error the caller is about to re-raise.
        """
        if created:
            try:
                inner.delete_branch(new_branch, force=True)
                log.step(f"Rolled back: deleted branch {new_branch}")
            except Exception as e:
                log.warn(f"Could not delete partial branch {new_branch}: {e}")

        # Restore HEAD. Prefer the symbolic ref (branch name) over a
        # detached sha, since git users expect a branch name on HEAD.
        if pre_branch:
            try:
                inner._run(
                    ["symbolic-ref", "HEAD", f"refs/heads/{pre_branch}"],
                    check=False,
                )
                log.step(f"Rolled back: HEAD → {pre_branch}")
                return
            except Exception:
                pass
        if pre_sha:
            try:
                inner._run(["update-ref", "--no-deref", "HEAD", pre_sha],
                           check=False)
                log.step(f"Rolled back: HEAD → {pre_sha[:8]}")
            except Exception as e:
                log.warn(f"Could not restore HEAD to {pre_sha[:8]}: {e}")

    @staticmethod
    def _refuse_if_outer_dirty(project_path: Path, force_hint: str) -> None:
        """Refuse with a clear error if the outer (mathematician's) tree is dirty.

        Switching branches rewrites files on disk — if the
        mathematician has uncommitted work, that work is at risk.
        Block until they commit or stash.
        """
        outer = Git(project_path, auto_init=False)
        if not outer.is_repo():
            return
        if outer.is_dirty():
            log.error(
                "The outer git repo is dirty — there are uncommitted changes "
                "in your working tree. Switching branches rewrites files on disk.",
            )
            log.step("Commit or stash your work first:")
            log.step("  git add -A && git commit -m '<message>'")
            log.step("  # or:")
            log.step("  git stash")
            log.step("")
            log.step("Or bypass this check and overwrite your uncommitted work:")
            log.step(f"  {force_hint}")
            raise typer.Exit(1)

    def _create_branch(self, inner: InnerGit, safe: str) -> None:
        try:
            inner.create_branch(safe, from_ref=self.from_ref or "HEAD")
        except Exception as e:
            log.error(f"Failed to create branch {safe}: {e}")
            raise typer.Exit(1)
        log.success(f"Created branch {safe} at {self.from_ref}")

    def _switch(self, inner: InnerGit, safe: str) -> None:
        try:
            inner.checkout(safe, force=self.force)
        except Exception as e:
            log.error(f"Failed to switch to {safe}: {e}")
            raise typer.Exit(1)

        log.success(f"Switched to: {safe}")
        sha = inner.head_sha(short=True)
        if sha:
            log.step(f"Inner HEAD: {sha}  ({inner.last_commit_subject() or '?'})")

    @staticmethod
    def _drop_stale_state_dirs(project_path: Path, inner: InnerGit) -> None:
        """Remove iter-*/session_* dirs on disk that aren't in HEAD's tree.

        Only removes when we have a concrete tracked listing to compare
        against. If `.archon/<parent_rel>` isn't tracked at HEAD at all
        (projects created before archon learned to track .archon/), we
        leave disk untouched and warn — blindly wiping would destroy
        the user's in-flight state.
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
                f"not present at this commit: {', '.join(removed_total)}",
            )
        if untracked_parents:
            log.warn(
                "This commit predates archon tracking .archon/ state — "
                "time-travel restores .lean/blueprint files only, not agent "
                "state. Untracked state dirs: " + ", ".join(untracked_parents),
            )
            log.step(
                "On the next `archon loop`, the updated excludes will commit "
                "the current .archon/ state so future forks are lossless.",
            )


class InnerLogCommand:
    """Show the inner-git commit graph (one-line format)."""

    def __init__(self, project_path: str, *, n: int = 20) -> None:
        self.project_path = project_path
        self.n = n

    def run(self) -> None:
        resolved, inner = _resolve_project(self.project_path)
        output = inner.log_oneline(n=self.n)
        if not output.strip():
            log.info("Inner git has no commits yet.")
            return
        log.header(f"Inner git log (last {self.n})")
        typer.echo(output)


# ── Typer entry points (thin wrappers) ────────────────────────────────


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
    BranchCommand(name, project_path, from_ref=from_ref, force=force).run()


def inner_log(
    project_path: str = typer.Argument(".", help="Path to Lean project."),
    n: int = typer.Option(20, "-n", help="Number of commits to show."),
) -> None:
    """Show the inner-git commit graph (one-line format)."""
    InnerLogCommand(project_path, n=n).run()

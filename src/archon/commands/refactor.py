"""`archon refactor draft` and `archon refactor run`.

Two-phase design:
  - `draft`  : launches Claude interactively to interview the user and
               produce a well-formed REFACTOR_DIRECTIVE.md. The mathematician
               reviews / edits the directive before step two.
  - `run`    : reads the directive, invokes the refactor agent in autonomous
               mode, and commits the result to the inner git.

Splitting the two steps lets the user tweak the directive between them,
which in practice matters a lot because a refactor agent that misreads
the directive can churn for hours.
"""

from __future__ import annotations

import shutil
import time
from importlib import resources
from pathlib import Path
from textwrap import dedent

import typer

from archon import log
from archon.agent import ClaudeAgent, DEFAULT_MODEL
from archon.commands.tooling.inner_git import InnerGit
from archon.commands.tooling.iteration import commit_phase
from archon.commands.tooling.version import warn_if_mismatch
from archon.runner import build_refactor_prompt


app = typer.Typer(
    name="refactor",
    help="Draft or run a refactor directive.",
    no_args_is_help=True,
)


# ── helpers ───────────────────────────────────────────────────────────


def _data_path(sub_path: str = "") -> Path:
    root = resources.files("archon").joinpath(".archon-src")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


def _resolve_project(project_path: str) -> tuple[Path, Path]:
    resolved = Path(project_path).resolve()
    state_dir = resolved / ".archon"
    if not state_dir.is_dir():
        log.error(f"Not an Archon project: {resolved}")
        log.step(f"Run: archon init {resolved}")
        raise typer.Exit(1)
    return resolved, state_dir


def _read_directive(state_dir: Path) -> str | None:
    """Return the directive's content iff it's non-empty and has body text."""
    directive_file = state_dir / "REFACTOR_DIRECTIVE.md"
    if not directive_file.exists():
        return None
    content = directive_file.read_text(encoding="utf-8").strip()
    if not content:
        return None
    body_lines = [
        l.strip() for l in content.splitlines()
        if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("<!--")
    ]
    if not body_lines:
        return None
    return content


# ── draft subcommand ──────────────────────────────────────────────────


@app.command("draft")
def draft(
    project_path: str = typer.Argument(".", help="Path to Lean project"),
    auto_run: bool = typer.Option(
        False, "--auto-run",
        help="Immediately launch `archon refactor run` after the draft is written.",
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-M",
        help="Claude model alias (e.g. 'opus', 'sonnet') or full id.",
    ),
) -> None:
    """Interview the user and write a REFACTOR_DIRECTIVE.md.

    Claude walks the user through the five required sections (problem,
    justification, changes, risk, rollback). The directive is written to
    `.archon/REFACTOR_DIRECTIVE.md`. By default, the refactor agent is NOT
    launched — the user is expected to review the directive first and
    then run `archon refactor run`.
    """
    resolved, state_dir = _resolve_project(project_path)
    warn_if_mismatch(resolved)

    if not shutil.which("claude"):
        log.error("Claude Code is not installed. Run: archon setup")
        raise typer.Exit(1)

    log.header("archon refactor draft")
    log.step("Launching Claude to interview you and write REFACTOR_DIRECTIVE.md.")

    prompt = dedent(f"""\
        You are helping the user draft a refactor directive for Archon. Read
        {state_dir}/prompts/refactor-draft.md for your full instructions. The
        directive file you will write lives at {state_dir}/REFACTOR_DIRECTIVE.md.

        Project path: {resolved}
        State dir:    {state_dir}

        Do NOT launch the refactor agent. Your only job is to interview the
        user and produce the directive file. Ask questions one at a time.
        """)

    ClaudeAgent(model=model, role="refactor-draft").run_interactive(prompt, cwd=resolved)

    directive = _read_directive(state_dir)
    if directive is None:
        log.warn("REFACTOR_DIRECTIVE.md is empty — draft was not saved.")
        raise typer.Exit(1)

    log.success("Directive written to .archon/REFACTOR_DIRECTIVE.md")

    if auto_run:
        log.step("--auto-run: launching refactor agent...")
        run(project_path, model=model)
    else:
        log.step("Review the directive, then run: "
                 f"archon refactor run {project_path}")


# ── run subcommand ────────────────────────────────────────────────────


@app.command("run")
def run(
    project_path: str = typer.Argument(".", help="Path to Lean project"),
    verbose_logs: bool = typer.Option(
        False, "--verbose-logs",
        help="Save raw Claude stream events to .raw.jsonl.",
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-M",
        help="Claude model alias (e.g. 'opus', 'sonnet') or full id.",
    ),
) -> None:
    """Execute REFACTOR_DIRECTIVE.md with the refactor agent.

    Commits the result to the inner git as
    `archon[NNN/refactor]: <summary>` (the agent phase commit). The
    outer (mathematician's) git repo is not touched.
    """
    resolved, state_dir = _resolve_project(project_path)
    warn_if_mismatch(resolved)

    if not shutil.which("claude"):
        log.error("Claude Code is not installed. Run: archon setup")
        raise typer.Exit(1)

    directive = _read_directive(state_dir)
    if directive is None:
        log.error("No directive found at .archon/REFACTOR_DIRECTIVE.md")
        log.step(f"Run first: archon refactor draft {project_path}")
        raise typer.Exit(1)

    project_name = resolved.name

    log.header("archon refactor run")
    log.phase(1, "Refactor agent")
    log.info("Executing .archon/REFACTOR_DIRECTIVE.md")

    # Show a preview of the directive so the user knows what's being run.
    preview_lines = [
        l for l in directive.splitlines()
        if l.strip() and not l.strip().startswith("<!--")
    ][:8]
    for line in preview_lines:
        log.step(line)
    if len(preview_lines) >= 8:
        log.step("... (directive truncated for display)")

    # ── Infer the iteration number from the inner git's HEAD subject.
    # If we can parse `archon[NNN/...]` from it, use NNN+1; else use 1.
    inner = InnerGit(resolved)
    iter_num = _next_iter_from_inner_git(inner)

    prompt = build_refactor_prompt(project_name, resolved, state_dir, directive)

    iter_log_dir = state_dir / "logs" / f"iter-{iter_num:03d}"
    iter_log_dir.mkdir(parents=True, exist_ok=True)
    log_base = iter_log_dir / "refactor"
    start = time.monotonic()
    ok = ClaudeAgent(model=model, role="refactor").run(
        prompt, cwd=resolved, log_base=log_base, verbose_logs=verbose_logs,
    )
    secs = int(time.monotonic() - start)

    if ok:
        log.success(f"Refactor agent finished ({secs}s)")
    else:
        log.error(f"Refactor agent failed ({secs}s)")

    # ── Commit to the inner git (always, even on failure, so the state
    # diff is captured for debugging).
    summary = _summarize_refactor_report(state_dir)
    commit_phase(
        resolved,
        iter_num=iter_num,
        phase="refactor",
        summary=summary or ("refactor completed" if ok else "refactor failed mid-way"),
    )

    # Clear the directive now that it has been executed.
    directive_file = state_dir / "REFACTOR_DIRECTIVE.md"
    directive_file.write_text(
        "# Refactor Directive\n\n"
        "<!-- Plan agent: write your refactoring directive here. -->\n"
        "<!-- The refactor agent will execute it at the start of the next iteration. -->\n"
        "<!-- This file is cleared after each refactor run. -->\n",
        encoding="utf-8",
    )

    if ok:
        log.step("Inspect the diff: "
                 "git --git-dir=.archon/git-dir --work-tree=. show HEAD")
        log.step("If the refactor went badly, fork a new branch at the "
                 "previous commit: archon branch pre-refactor . --from HEAD~1")


# ── helpers ───────────────────────────────────────────────────────────


def _summarize_refactor_report(state_dir: Path) -> str:
    """Extract a short first-line summary from task_results/refactor.md."""
    report = state_dir / "task_results" / "refactor.md"
    if not report.exists():
        return ""
    try:
        for line in report.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("<!--"):
                continue
            return s[:120]
    except OSError:
        return ""
    return ""


def _next_iter_from_inner_git(inner: InnerGit) -> int:
    """Best-effort iteration number inference from last commit subject."""
    subj = inner.last_commit_subject() or ""
    import re
    m = re.search(r"archon\[(\d+)/", subj)
    if m:
        try:
            return int(m.group(1)) + 1
        except ValueError:
            pass
    return 1

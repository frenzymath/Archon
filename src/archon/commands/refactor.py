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

import re
import shutil
from pathlib import Path
from textwrap import dedent
from typing import Optional

import typer

from archon import log
from archon.agent import ClaudeAgent, ClaudeBackend, DEFAULT_MODEL
from archon.commands.tooling.inner_git import InnerGit
from archon.commands.tooling.iteration import commit_phase
from archon.commands.tooling.version import warn_if_mismatch
from archon.commands.tooling.project_config import load_project_config, resolve_claude_backend


app = typer.Typer(
    name="refactor",
    help="Draft or run a refactor directive.",
    no_args_is_help=True,
)


_DIRECTIVE_PLACEHOLDER = (
    "# Refactor Directive\n\n"
    "<!-- Plan agent: write your refactoring directive here. -->\n"
    "<!-- The refactor agent will execute it at the start of the next iteration. -->\n"
    "<!-- This file is cleared after each refactor run. -->\n"
)


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
        line.strip() for line in content.splitlines()
        if line.strip()
        and not line.strip().startswith("#")
        and not line.strip().startswith("<!--")
    ]
    if not body_lines:
        return None
    return content


# ── command classes ───────────────────────────────────────────────────


class RefactorDraftCommand:
    """Interview the user and write a REFACTOR_DIRECTIVE.md."""

    def __init__(
        self,
        project_path: str,
        *,
        auto_run: bool = False,
        model: str = DEFAULT_MODEL,
        backend: ClaudeBackend | None = None,
    ) -> None:
        self.project_path = project_path
        self.auto_run = auto_run
        self.model = model
        self.backend = backend

    def run(self) -> None:
        resolved, state_dir = _resolve_project(self.project_path)
        warn_if_mismatch(resolved)

        if not shutil.which("claude"):
            log.error("Claude Code is not installed. Run: archon setup")
            raise typer.Exit(1)

        log.header("archon refactor draft")
        log.step("Launching Claude to interview you and write REFACTOR_DIRECTIVE.md.")

        prompt = self._build_prompt(resolved, state_dir)
        ClaudeAgent(model=self.model, role="refactor-draft", backend=self.backend or ClaudeBackend()).run_interactive(
            prompt, cwd=resolved,
        )

        directive = _read_directive(state_dir)
        if directive is None:
            log.warn("REFACTOR_DIRECTIVE.md is empty — draft was not saved.")
            raise typer.Exit(1)

        log.success("Directive written to .archon/REFACTOR_DIRECTIVE.md")

        if self.auto_run:
            log.step("--auto-run: launching refactor agent...")
            RefactorRunCommand(self.project_path, model=self.model).run()
        else:
            log.step(
                f"Review the directive, then run: archon refactor run {self.project_path}",
            )

    def _build_prompt(self, resolved: Path, state_dir: Path) -> str:
        return dedent(f"""\
            You are helping the user draft a refactor directive for Archon. Read
            {state_dir}/prompts/refactor-draft.md for your full instructions. The
            directive file you will write lives at {state_dir}/REFACTOR_DIRECTIVE.md.

            Project path: {resolved}
            State dir:    {state_dir}

            Do NOT launch the refactor agent. Your only job is to interview the
            user and produce the directive file. Ask questions one at a time.
            """)


class RefactorRunCommand:
    """Execute REFACTOR_DIRECTIVE.md with the refactor agent."""

    def __init__(
        self,
        project_path: str,
        *,
        verbose_logs: bool = False,
        model: str = DEFAULT_MODEL,
        backend: ClaudeBackend | None = None,
    ) -> None:
        self.project_path = project_path
        self.verbose_logs = verbose_logs
        self.model = model
        self.backend = backend

    def run(self) -> None:
        resolved, state_dir = _resolve_project(self.project_path)
        warn_if_mismatch(resolved)

        if not shutil.which("claude"):
            log.error("Claude Code is not installed. Run: archon setup")
            raise typer.Exit(1)

        directive = _read_directive(state_dir)
        if directive is None:
            log.error("No directive found at .archon/REFACTOR_DIRECTIVE.md")
            log.step(f"Run first: archon refactor draft {self.project_path}")
            raise typer.Exit(1)

        log.header("archon refactor run")
        log.phase(1, "Refactor agent")
        log.info("Executing .archon/REFACTOR_DIRECTIVE.md")

        self._show_preview(directive)

        # Infer the iteration number from the inner git's HEAD subject.
        # If we can parse `archon[NNN/...]` from it, use NNN+1; else 1.
        inner = InnerGit(resolved)
        iter_num = self._next_iter_from_inner_git(inner)

        ok, secs = self._invoke_agent(resolved, state_dir, iter_num, directive)

        # Commit to the inner git (always, even on failure, so the
        # state diff is captured for debugging).
        summary = self._summarize_report(state_dir)
        commit_phase(
            resolved, iter_num=iter_num, phase="refactor",
            summary=summary or (
                "refactor completed" if ok else "refactor failed mid-way"
            ),
        )

        # Clear the directive now that it has been executed.
        (state_dir / "REFACTOR_DIRECTIVE.md").write_text(
            _DIRECTIVE_PLACEHOLDER, encoding="utf-8",
        )

        if ok:
            log.step(
                "Inspect the diff: "
                "git --git-dir=.archon/git-dir --work-tree=. show HEAD",
            )
            log.step(
                "If the refactor went badly, fork a new branch at the "
                "previous commit: archon branch pre-refactor . --from HEAD~1",
            )

    # ── private ────────────────────────────────────────────────────────

    def _show_preview(self, directive: str) -> None:
        preview_lines = [
            line for line in directive.splitlines()
            if line.strip() and not line.strip().startswith("<!--")
        ][:8]
        for line in preview_lines:
            log.step(line)
        if len(preview_lines) >= 8:
            log.step("... (directive truncated for display)")

    def _invoke_agent(
        self, resolved: Path, state_dir: Path, iter_num: int, directive: str,
    ) -> tuple[bool, int]:
        from archon.subagents.base import Subagent
        from archon.subagents.registry import build_registry

        registry = build_registry(resolved)
        descriptor = registry.get("refactor")
        if descriptor is None:
            log.error(
                "No `refactor` subagent descriptor found. Drop a "
                "`.archon/subagents/refactor.md` file (or list it under "
                "`subagents.enabled` in config.json) and try again."
            )
            raise typer.Exit(1)

        iter_log_dir = state_dir / "logs" / f"iter-{iter_num:03d}"
        iter_log_dir.mkdir(parents=True, exist_ok=True)
        # CLI flow uses a fixed slug. The subagent writes to
        # task_results/refactor-cli.md; _summarize_report below reads from
        # there. Older runs may have written task_results/refactor.md;
        # _summarize_report falls back to that for backwards compatibility.
        slug = "cli"
        log_base = iter_log_dir / f"refactor-{slug}"

        sub = Subagent(
            descriptor, resolved,
            model=self.model, verbose_logs=self.verbose_logs,
            backend=self.backend or ClaudeBackend(),
        )
        result = sub.run(
            directive=directive, slug=slug, iter_num=iter_num, log_base=log_base,
        )
        return result.ok, result.duration_s

    @staticmethod
    def _summarize_report(state_dir: Path) -> str:
        candidates = [
            state_dir / "task_results" / "refactor-cli.md",
            state_dir / "task_results" / "refactor.md",
        ]
        for report in candidates:
            if not report.exists():
                continue
            try:
                for line in report.read_text(encoding="utf-8").splitlines():
                    s = line.strip()
                    if not s or s.startswith("#") or s.startswith("<!--"):
                        continue
                    return s[:120]
            except OSError:
                continue
        return ""

    @staticmethod
    def _next_iter_from_inner_git(inner: InnerGit) -> int:
        """Best-effort iteration number from last commit subject."""
        subj = inner.last_commit_subject() or ""
        m = re.search(r"archon\[(\d+)/", subj)
        if m:
            try:
                return int(m.group(1)) + 1
            except ValueError:
                pass
        return 1


# ── Typer subcommands (thin wrappers) ─────────────────────────────────


@app.command("draft")
def draft(
    project_path: str = typer.Argument(".", help="Path to Lean project"),
    auto_run: bool = typer.Option(
        False, "--auto-run",
        help="Immediately launch `archon refactor run` after the draft is written.",
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-M",
        help=(
            "Model alias. Anthropic: 'opus', 'sonnet', 'haiku' or a full id. "
            "Non-Anthropic (uses .archon/.env credentials): 'kimi', 'deepseek'."
        ),
    ),
    claude_backend: Optional[str] = typer.Option(
        None, "--claude-backend",
        help=(
            "How 'claude -p' is invoked for every headless agent run. "
            "'default': plain claude -p. "
            "'vscode': sets CLAUDE_CODE_ENTRYPOINT=claude-vscode. "
            "'desktop': sets CLAUDE_CODE_ENTRYPOINT=claude-desktop. "
            "(default from .archon/config.json loop.claude_backend or 'default')"
        ),
    ),
) -> None:
    """Interview the user and write a REFACTOR_DIRECTIVE.md.

    Claude walks the user through the five required sections (problem,
    justification, changes, risk, rollback). The directive is written to
    `.archon/REFACTOR_DIRECTIVE.md`. By default, the refactor agent is NOT
    launched — the user is expected to review the directive first and
    then run `archon refactor run`.
    """
    project_config = load_project_config(Path(project_path))
    backend = resolve_claude_backend(project_config, cli_value=claude_backend)
    RefactorDraftCommand(project_path, auto_run=auto_run, model=model, backend=backend).run()


@app.command("run")
def run(
    project_path: str = typer.Argument(".", help="Path to Lean project"),
    verbose_logs: bool = typer.Option(
        False, "--verbose-logs",
        help="Save raw Claude stream events to .raw.jsonl.",
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-M",
        help=(
            "Model alias. Anthropic: 'opus', 'sonnet', 'haiku' or a full id. "
            "Non-Anthropic (uses .archon/.env credentials): 'kimi', 'deepseek'."
        ),
    ),
    claude_backend: Optional[str] = typer.Option(
        None, "--claude-backend",
        help=(
            "How 'claude -p' is invoked for every headless agent run. "
            "'default': plain claude -p. "
            "'vscode': sets CLAUDE_CODE_ENTRYPOINT=claude-vscode. "
            "'desktop': sets CLAUDE_CODE_ENTRYPOINT=claude-desktop. "
            "(default from .archon/config.json loop.claude_backend or 'default')"
        ),
    ),
) -> None:
    """Execute REFACTOR_DIRECTIVE.md with the refactor agent.

    Commits the result to the inner git as
    `archon[NNN/refactor]: <summary>` (the agent phase commit). The
    outer (mathematician's) git repo is not touched.
    """
    project_config = load_project_config(Path(project_path))
    backend = resolve_claude_backend(project_config, cli_value=claude_backend)
    RefactorRunCommand(project_path, verbose_logs=verbose_logs, model=model, backend=backend).run()

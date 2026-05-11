"""Internal: invoke a subagent. Used by .claude/tools/ wrapper scripts.

Both the CLI's ``archon refactor run`` and the autonomous loop's
in-session subagent calls go through ``Subagent`` classes, but only the
loop reaches them via this CLI subcommand. The wrapper scripts in
``.claude/tools/archon-<role>-agent.py`` shell out to
``archon subagent <role>`` so the subagent's execution streams through
the Archon JSONL parser like any other phase.

The three subcommands (``refactor``, ``analogy``, ``challenger``) share
the same argument shape — ``--slug``, ``--directive-file``,
``--iter-num`` — and the same orchestration. ``_invoke`` does the work;
the typer commands are thin shells that pick the right Subagent class.
"""

from __future__ import annotations

from pathlib import Path

import typer

from archon import log
from archon.subagents.analogy import AnalogySubagent
from archon.subagents.base import Subagent
from archon.subagents.challenger import ChallengerSubagent
from archon.subagents.refactor import RefactorSubagent


app = typer.Typer(
    name="subagent",
    help="Internal: invoke a subagent by name. Used by .claude/tools/ scripts.",
    no_args_is_help=True,
)


# ── shared invocation ────────────────────────────────────────────────


def _invoke(
    sub_cls: type[Subagent],
    *,
    project_path: str,
    slug: str,
    directive_file: Path,
    iter_num: int,
    verbose_logs: bool,
) -> None:
    """Run a Subagent and exit with its success/failure code.

    Centralized so every subagent subcommand has identical
    error-handling, log-path layout, and stdout shape.
    """
    resolved = Path(project_path).resolve()
    if not directive_file.exists():
        log.error(f"Directive file not found: {directive_file}")
        raise typer.Exit(1)
    directive = directive_file.read_text(encoding="utf-8")

    iter_log_dir = resolved / ".archon" / "logs" / f"iter-{iter_num:03d}"
    iter_log_dir.mkdir(parents=True, exist_ok=True)
    log_base = iter_log_dir / f"{sub_cls.name}-{slug}"

    sub = sub_cls(resolved, verbose_logs=verbose_logs)
    result = sub.run(
        directive=directive, slug=slug, iter_num=iter_num, log_base=log_base,
    )

    # One-line return value — what the calling Claude sees on stdout.
    status = "COMPLETE" if result.ok else "INCOMPLETE"
    if result.report_path:
        print(f"{slug}: {status} — see {result.report_path}")
    else:
        print(f"{slug}: {status} (no report written)")
    raise typer.Exit(0 if result.ok else 1)


# ── shared option declarations ───────────────────────────────────────
#
# Three commands, identical option set. Declared once and re-used so a
# tweak (e.g. adding --timeout) only needs to land in one place.


_PROJECT_PATH = typer.Option(".", "--project-path")
_SLUG = typer.Option(..., "--slug")
_DIRECTIVE_FILE = typer.Option(..., "--directive-file")
_ITER_NUM = typer.Option(..., "--iter-num")
_VERBOSE_LOGS = typer.Option(False, "--verbose-logs")


# ── subcommands ──────────────────────────────────────────────────────


@app.command("refactor")
def refactor(
    project_path: str = _PROJECT_PATH,
    slug: str = _SLUG,
    directive_file: Path = _DIRECTIVE_FILE,
    iter_num: int = _ITER_NUM,
    verbose_logs: bool = _VERBOSE_LOGS,
) -> None:
    """Invoke the refactor subagent on a directive file."""
    _invoke(
        RefactorSubagent,
        project_path=project_path, slug=slug,
        directive_file=directive_file, iter_num=iter_num,
        verbose_logs=verbose_logs,
    )


@app.command("analogy")
def analogy(
    project_path: str = _PROJECT_PATH,
    slug: str = _SLUG,
    directive_file: Path = _DIRECTIVE_FILE,
    iter_num: int = _ITER_NUM,
    verbose_logs: bool = _VERBOSE_LOGS,
) -> None:
    """Invoke the analogy subagent on a directive file."""
    _invoke(
        AnalogySubagent,
        project_path=project_path, slug=slug,
        directive_file=directive_file, iter_num=iter_num,
        verbose_logs=verbose_logs,
    )


@app.command("challenger")
def challenger(
    project_path: str = _PROJECT_PATH,
    slug: str = _SLUG,
    directive_file: Path = _DIRECTIVE_FILE,
    iter_num: int = _ITER_NUM,
    verbose_logs: bool = _VERBOSE_LOGS,
) -> None:
    """Invoke the challenger subagent on a directive file."""
    _invoke(
        ChallengerSubagent,
        project_path=project_path, slug=slug,
        directive_file=directive_file, iter_num=iter_num,
        verbose_logs=verbose_logs,
    )
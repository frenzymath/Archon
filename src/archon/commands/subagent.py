"""Internal: invoke a subagent. Used by .claude/tools/ wrapper scripts.

Both the CLI's ``archon refactor run`` and the autonomous loop's
in-session subagent calls go through ``Subagent`` classes, but only the
loop reaches them via this CLI subcommand. The wrapper scripts in
``.claude/tools/archon-<role>-agent.py`` shell out to
``archon subagent <role>`` so the subagent's execution streams through
the Archon JSONL parser like any other phase.

The four subcommands (``refactor``, ``analogy``, ``challenger``,
``coordinator``) share the same argument shape — ``--slug``,
``--directive-file``, ``--iter-num``, ``--parent-slug``,
``--write-domain`` — and the same orchestration. ``_invoke`` does the
work; the typer commands are thin shells that pick the right Subagent
class.
"""

from __future__ import annotations

from pathlib import Path

import typer

from archon import log
from archon.subagents.analogy import AnalogySubagent
from archon.subagents.base import (
    ROOT_PARENT_SLUG,
    Subagent,
    WriteDomainViolation,
)
from archon.subagents.challenger import ChallengerSubagent
from archon.subagents.coordinator import CoordinatorSubagent
from archon.subagents.refactor import RefactorSubagent
from archon.subagents.review_blueprint_consistency import (
    ReviewBlueprintConsistencySubagent,
)
from archon.subagents.review_comment_hygiene import ReviewCommentHygieneSubagent
from archon.subagents.review_definition_correctness import (
    ReviewDefinitionCorrectnessSubagent,
)
from archon.subagents.review_design_choices import ReviewDesignChoicesSubagent
from archon.subagents.review_mathlib_overlap import ReviewMathlibOverlapSubagent


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
    parent_slug: str,
    write_domain: list[str] | None,
) -> None:
    """Run a Subagent and exit with its success/failure code.

    Centralized so every subagent subcommand has identical
    error-handling, log-path layout, and stdout shape.

    ``parent_slug`` defaults to ``ROOT_PARENT_SLUG`` ("_root") when the
    caller is the plan agent. Child subagents pass the parent's own
    slug, which the wrapper script reads from ``ARCHON_SUBAGENT_SLUG``.

    ``write_domain`` is a list of glob patterns (e.g.
    ``["Algebra/**", "Picard/LineBundle.lean"]``). The base class
    refuses to dispatch when the declared domain isn't a subset of the
    parent's recorded domain — preventing two siblings from racing on
    the same Lean file by accident.
    """
    resolved = Path(project_path).resolve()
    if not directive_file.exists():
        log.error(f"Directive file not found: {directive_file}")
        raise typer.Exit(1)
    directive = directive_file.read_text(encoding="utf-8")

    iter_log_dir = resolved / ".archon" / "logs" / f"iter-{iter_num:03d}"
    iter_log_dir.mkdir(parents=True, exist_ok=True)

    # Hierarchical log-base layout when invoked under a non-root parent:
    # iter-NNN/<parent-slug>/<role>-<slug>.jsonl. This keeps sibling
    # JSONLs from colliding when two coordinators each spawn a child
    # named, say, "split-cohomology".
    if parent_slug == ROOT_PARENT_SLUG:
        log_base = iter_log_dir / f"{sub_cls.name}-{slug}"
    else:
        parent_dir = iter_log_dir / parent_slug
        parent_dir.mkdir(parents=True, exist_ok=True)
        log_base = parent_dir / f"{sub_cls.name}-{slug}"

    sub = sub_cls(resolved, verbose_logs=verbose_logs)
    try:
        result = sub.run(
            directive=directive,
            slug=slug,
            iter_num=iter_num,
            log_base=log_base,
            parent_slug=parent_slug,
            write_domain=write_domain or [],
        )
    except WriteDomainViolation as e:
        # Hard error: refuse to spawn. The plan/coordinator sees a
        # non-zero exit and a clear message, instead of a child that
        # silently wrote outside its lane.
        log.error(f"write-domain violation: {e}")
        print(f"{slug}: REJECTED — {e}")
        raise typer.Exit(2)

    # One-line return value — what the calling Claude sees on stdout.
    status = "COMPLETE" if result.ok else "INCOMPLETE"
    if result.report_path:
        print(f"{slug}: {status} — see {result.report_path}")
    else:
        print(f"{slug}: {status} (no report written)")
    raise typer.Exit(0 if result.ok else 1)


# ── shared option declarations ───────────────────────────────────────
#
# Four commands, identical option set. Declared once and re-used so a
# tweak (e.g. adding --timeout) only needs to land in one place.


_PROJECT_PATH = typer.Option(".", "--project-path")
_SLUG = typer.Option(..., "--slug")
_DIRECTIVE_FILE = typer.Option(..., "--directive-file")
_ITER_NUM = typer.Option(..., "--iter-num")
_VERBOSE_LOGS = typer.Option(False, "--verbose-logs")
_PARENT_SLUG = typer.Option(
    ROOT_PARENT_SLUG, "--parent-slug",
    help="Slug of the subagent that spawned this one. "
         "Defaults to '_root' for plan-agent-launched calls.",
)
_WRITE_DOMAIN = typer.Option(
    None, "--write-domain",
    help="Glob pattern this subagent (and its descendants) is "
         "allowed to write to. Repeat to declare multiple. "
         "Validated against the parent's recorded domain.",
)


# ── subcommands ──────────────────────────────────────────────────────


@app.command("refactor")
def refactor(
    project_path: str = _PROJECT_PATH,
    slug: str = _SLUG,
    directive_file: Path = _DIRECTIVE_FILE,
    iter_num: int = _ITER_NUM,
    verbose_logs: bool = _VERBOSE_LOGS,
    parent_slug: str = _PARENT_SLUG,
    write_domain: list[str] = _WRITE_DOMAIN,
) -> None:
    """Invoke the refactor subagent on a directive file."""
    _invoke(
        RefactorSubagent,
        project_path=project_path, slug=slug,
        directive_file=directive_file, iter_num=iter_num,
        verbose_logs=verbose_logs,
        parent_slug=parent_slug, write_domain=write_domain,
    )


@app.command("analogy")
def analogy(
    project_path: str = _PROJECT_PATH,
    slug: str = _SLUG,
    directive_file: Path = _DIRECTIVE_FILE,
    iter_num: int = _ITER_NUM,
    verbose_logs: bool = _VERBOSE_LOGS,
    parent_slug: str = _PARENT_SLUG,
    write_domain: list[str] = _WRITE_DOMAIN,
) -> None:
    """Invoke the analogy subagent on a directive file."""
    _invoke(
        AnalogySubagent,
        project_path=project_path, slug=slug,
        directive_file=directive_file, iter_num=iter_num,
        verbose_logs=verbose_logs,
        parent_slug=parent_slug, write_domain=write_domain,
    )


@app.command("challenger")
def challenger(
    project_path: str = _PROJECT_PATH,
    slug: str = _SLUG,
    directive_file: Path = _DIRECTIVE_FILE,
    iter_num: int = _ITER_NUM,
    verbose_logs: bool = _VERBOSE_LOGS,
    parent_slug: str = _PARENT_SLUG,
    write_domain: list[str] = _WRITE_DOMAIN,
) -> None:
    """Invoke the challenger subagent on a directive file."""
    _invoke(
        ChallengerSubagent,
        project_path=project_path, slug=slug,
        directive_file=directive_file, iter_num=iter_num,
        verbose_logs=verbose_logs,
        parent_slug=parent_slug, write_domain=write_domain,
    )


@app.command("coordinator")
def coordinator(
    project_path: str = _PROJECT_PATH,
    slug: str = _SLUG,
    directive_file: Path = _DIRECTIVE_FILE,
    iter_num: int = _ITER_NUM,
    verbose_logs: bool = _VERBOSE_LOGS,
    parent_slug: str = _PARENT_SLUG,
    write_domain: list[str] = _WRITE_DOMAIN,
) -> None:
    """Invoke the coordinator subagent on a directive file.

    The coordinator decomposes a multi-part directive into sub-directives
    and dispatches them as child refactor/analogy/challenger/coordinator
    subagents in parallel (subject to ``max_parallel``).
    """
    _invoke(
        CoordinatorSubagent,
        project_path=project_path, slug=slug,
        directive_file=directive_file, iter_num=iter_num,
        verbose_logs=verbose_logs,
        parent_slug=parent_slug, write_domain=write_domain,
    )


# ── review-* subagents (Workstream E) ─────────────────────────────
#
# Five read-only reviewers that audit one aspect of the project each.
# All share the ReviewSubagent shape; only the role name differs.

@app.command("review-definition-correctness")
def review_definition_correctness(
    project_path: str = _PROJECT_PATH,
    slug: str = _SLUG,
    directive_file: Path = _DIRECTIVE_FILE,
    iter_num: int = _ITER_NUM,
    verbose_logs: bool = _VERBOSE_LOGS,
    parent_slug: str = _PARENT_SLUG,
    write_domain: list[str] = _WRITE_DOMAIN,
) -> None:
    """Detect stand-in / mathematically-wrong definitions."""
    _invoke(
        ReviewDefinitionCorrectnessSubagent,
        project_path=project_path, slug=slug,
        directive_file=directive_file, iter_num=iter_num,
        verbose_logs=verbose_logs,
        parent_slug=parent_slug, write_domain=write_domain,
    )


@app.command("review-comment-hygiene")
def review_comment_hygiene(
    project_path: str = _PROJECT_PATH,
    slug: str = _SLUG,
    directive_file: Path = _DIRECTIVE_FILE,
    iter_num: int = _ITER_NUM,
    verbose_logs: bool = _VERBOSE_LOGS,
    parent_slug: str = _PARENT_SLUG,
    write_domain: list[str] = _WRITE_DOMAIN,
) -> None:
    """Flag in-source iter-history comments, stale TODOs, docstring drift."""
    _invoke(
        ReviewCommentHygieneSubagent,
        project_path=project_path, slug=slug,
        directive_file=directive_file, iter_num=iter_num,
        verbose_logs=verbose_logs,
        parent_slug=parent_slug, write_domain=write_domain,
    )


@app.command("review-blueprint-consistency")
def review_blueprint_consistency(
    project_path: str = _PROJECT_PATH,
    slug: str = _SLUG,
    directive_file: Path = _DIRECTIVE_FILE,
    iter_num: int = _ITER_NUM,
    verbose_logs: bool = _VERBOSE_LOGS,
    parent_slug: str = _PARENT_SLUG,
    write_domain: list[str] = _WRITE_DOMAIN,
) -> None:
    """Verify Lean signatures match their blueprint statements."""
    _invoke(
        ReviewBlueprintConsistencySubagent,
        project_path=project_path, slug=slug,
        directive_file=directive_file, iter_num=iter_num,
        verbose_logs=verbose_logs,
        parent_slug=parent_slug, write_domain=write_domain,
    )


@app.command("review-design-choices")
def review_design_choices(
    project_path: str = _PROJECT_PATH,
    slug: str = _SLUG,
    directive_file: Path = _DIRECTIVE_FILE,
    iter_num: int = _ITER_NUM,
    verbose_logs: bool = _VERBOSE_LOGS,
    parent_slug: str = _PARENT_SLUG,
    write_domain: list[str] = _WRITE_DOMAIN,
) -> None:
    """Flag suboptimal architectural decisions (parallel pipelines, etc.)."""
    _invoke(
        ReviewDesignChoicesSubagent,
        project_path=project_path, slug=slug,
        directive_file=directive_file, iter_num=iter_num,
        verbose_logs=verbose_logs,
        parent_slug=parent_slug, write_domain=write_domain,
    )


@app.command("review-mathlib-overlap")
def review_mathlib_overlap(
    project_path: str = _PROJECT_PATH,
    slug: str = _SLUG,
    directive_file: Path = _DIRECTIVE_FILE,
    iter_num: int = _ITER_NUM,
    verbose_logs: bool = _VERBOSE_LOGS,
    parent_slug: str = _PARENT_SLUG,
    write_domain: list[str] = _WRITE_DOMAIN,
) -> None:
    """Surface project↔Mathlib structural duplication."""
    _invoke(
        ReviewMathlibOverlapSubagent,
        project_path=project_path, slug=slug,
        directive_file=directive_file, iter_num=iter_num,
        verbose_logs=verbose_logs,
        parent_slug=parent_slug, write_domain=write_domain,
    )

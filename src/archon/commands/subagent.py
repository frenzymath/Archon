"""Internal: invoke a subagent by name. Used by ``.claude/tools/`` wrappers.

One generic command — ``archon subagent <name> --slug ... --directive-file ...``
— covers every subagent. ``<name>`` is looked up in the registry built
from ``.archon/subagents/<name>.md`` descriptors (with built-in
fall-back). Unknown names produce a clear error listing what loaded.

After the subagent's run completes and its report lands at
``task_results/<name>-<slug>.md``, this command also copies the report
to ``logs/iter-NNN/<name>-<slug>-report.md`` so the dashboard can
render it within the same iter — the dispatching plan agent does NOT
need to perform that archival step itself (no agent-side ``cp``).

This is the runtime entry point shared by:

* The autonomous loop's in-session subagent calls (via the
  ``.claude/tools/archon-subagent.py`` wrapper, which streams the
  child's JSONL through the Archon parser).
* Anything else that wants to fire a subagent from the shell.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer

from archon import log
from archon.commands.tooling.project_config import (
    load_project_config,
    resolve_subagents_enabled,
)
from archon.subagents.base import (
    ROOT_PARENT_SLUG,
    Subagent,
    WriteDomainViolation,
)
from archon.subagents.registry import build_registry


def _archive_subagent_report(
    report_path: Path,
    iter_log_dir: Path,
    parent_slug: str,
    name: str,
    slug: str,
) -> Path | None:
    """Copy a finished subagent's report to ``logs/iter-NNN/`` for the dashboard.

    The original report stays under ``task_results/`` so the dispatching
    agent (and downstream subagents) can still find it. Returns the
    destination path on success, ``None`` when the source is missing
    or the copy fails. Failures are non-fatal — the loop still
    benefits from the source under ``task_results/``.

    The destination mirrors the parent-slug hierarchy so nested
    dispatches land under their parent's directory in the iter logs.
    """
    if not report_path or not report_path.is_file():
        return None
    if parent_slug == ROOT_PARENT_SLUG:
        dest_dir = iter_log_dir
    else:
        dest_dir = iter_log_dir / parent_slug
    dest_path = dest_dir / f"{name}-{slug}-report.md"
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(report_path, dest_path)
    except OSError as e:
        log.warn(
            f"could not archive {name}/{slug} report to {dest_path}: {e}"
        )
        return None
    return dest_path


def subagent_command(
    name: str = typer.Argument(
        ...,
        help="Name of the subagent to invoke. Must match a descriptor in "
             "`.archon/subagents/<name>.md` (or a built-in default).",
    ),
    project_path: str = typer.Option(".", "--project-path"),
    slug: str = typer.Option(..., "--slug"),
    directive_file: Path = typer.Option(..., "--directive-file"),
    iter_num: int = typer.Option(..., "--iter-num"),
    verbose_logs: bool = typer.Option(False, "--verbose-logs"),
    parent_slug: str = typer.Option(
        ROOT_PARENT_SLUG, "--parent-slug",
        help="Slug of the subagent that spawned this one. Defaults to "
             "'_root' for plan-agent-launched calls.",
    ),
    write_domain: list[str] = typer.Option(
        None, "--write-domain",
        help="Glob pattern this subagent (and descendants) may write to. "
             "Repeat to declare multiple. Validated against the parent's "
             "recorded domain.",
    ),
) -> None:
    """Invoke a subagent by name on a directive file.

    Looks up ``name`` in the registry (project descriptors override
    built-in by name), instantiates a generic :class:`Subagent`, runs
    it, exits with the run's success status.
    """
    resolved = Path(project_path).resolve()

    cfg = load_project_config(resolved)
    enabled = resolve_subagents_enabled(cfg)
    registry = build_registry(resolved, enabled=enabled)

    descriptor = registry.get(name)
    if descriptor is None:
        loaded = ", ".join(registry.names()) or "<none>"
        log.error(
            f"Unknown subagent {name!r}. Loaded: {loaded}.\n"
            "Drop a `.md` descriptor in `.archon/subagents/` or list "
            "the name in `config.json -> subagents.enabled`."
        )
        raise typer.Exit(2)

    if not directive_file.exists():
        log.error(f"Directive file not found: {directive_file}")
        raise typer.Exit(1)
    directive = directive_file.read_text(encoding="utf-8")

    iter_log_dir = resolved / ".archon" / "logs" / f"iter-{iter_num:03d}"
    iter_log_dir.mkdir(parents=True, exist_ok=True)

    if parent_slug == ROOT_PARENT_SLUG:
        log_base = iter_log_dir / f"{descriptor.name}-{slug}"
    else:
        parent_dir = iter_log_dir / parent_slug
        parent_dir.mkdir(parents=True, exist_ok=True)
        log_base = parent_dir / f"{descriptor.name}-{slug}"

    sub = Subagent(descriptor, resolved, verbose_logs=verbose_logs)
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
        log.error(f"write-domain violation: {e}")
        print(f"{slug}: REJECTED — {e}")
        raise typer.Exit(2)

    status = "COMPLETE" if result.ok else "INCOMPLETE"
    if result.report_path:
        # Auto-archive to logs/iter-NNN/ so the dashboard can render
        # the report alongside the JSONL stream. The dispatching plan
        # agent doesn't need to copy this itself.
        archived = _archive_subagent_report(
            result.report_path, iter_log_dir, parent_slug,
            descriptor.name, slug,
        )
        if archived is not None:
            print(
                f"{slug}: {status} — see {result.report_path} "
                f"(archived: {archived})"
            )
        else:
            print(f"{slug}: {status} — see {result.report_path}")
    else:
        print(f"{slug}: {status} (no report written)")
    raise typer.Exit(0 if result.ok else 1)

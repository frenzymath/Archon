"""Internal: invoke a subagent by name. Used by ``.claude/tools/`` wrappers.

One generic command — ``archon subagent <name> --slug ... --directive-file ...``
— covers every subagent. ``<name>`` is looked up in the registry built
from ``.archon/subagents/<name>.md`` descriptors (with built-in
fall-back). Unknown names produce a clear error listing what loaded.

This is the runtime entry point shared by:

* The autonomous loop's in-session subagent calls (via the
  ``.claude/tools/archon-subagent.py`` wrapper, which streams the
  child's JSONL through the Archon parser).
* Anything else that wants to fire a subagent from the shell.
"""

from __future__ import annotations

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
        print(f"{slug}: {status} — see {result.report_path}")
    else:
        print(f"{slug}: {status} (no report written)")
    raise typer.Exit(0 if result.ok else 1)

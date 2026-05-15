"""Post-phase audit: warn when mandatory subagents weren't dispatched.

Soft enforcement only — we never abort the phase. The plan/review
agent's prompt already says "you MUST dispatch every [MANDATORY]
subagent"; this module logs a clear warning when the dispatch.jsonl
shows that instruction was ignored, so the developer can see at a
glance that the iteration was non-compliant.
"""

from __future__ import annotations

from pathlib import Path

from archon import log

from .base import _read_dispatch_jsonl
from .registry import build_registry


def check_mandatory_dispatched(
    project_path: Path,
    state_dir: Path,
    iter_num: int,
    phase: str,
    *,
    enabled: list[str] | None = None,
) -> list[str]:
    """Return names of mandatory subagents that didn't fire this iter.

    ``phase`` is the calling phase name (``"plan"`` or ``"review"``).
    ``enabled`` mirrors the registry filter — pass the project's
    ``subagents.enabled`` config so the audit honors the same filter
    the catalog used when the agent was told what was available.

    Returns an (empty) list of missing subagent names. Emits a warning
    via :mod:`archon.log` when the list is non-empty.
    """
    registry = build_registry(project_path, enabled=enabled)
    expected = [d.name for d in registry.descriptors() if d.is_mandatory_for(phase)]
    if not expected:
        return []

    dispatch_log = state_dir / "logs" / f"iter-{iter_num:03d}" / "dispatch.jsonl"
    rows = _read_dispatch_jsonl(dispatch_log)
    dispatched = {
        row.get("role")
        for row in rows
        if row.get("event") == "dispatch_start"
    }

    missing = [name for name in expected if name not in dispatched]
    if missing:
        joined = ", ".join(f"`{n}`" for n in missing)
        log.warn(
            f"{phase} phase finished without dispatching mandatory "
            f"subagent(s): {joined}. Check the agent's session log to "
            f"see why; the catalog block named these as [MANDATORY]."
        )
    return missing

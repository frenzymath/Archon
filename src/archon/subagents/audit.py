"""Post-phase audit: warn when highly-recommended subagents weren't dispatched
without a documented skip rationale.

Soft enforcement only — we never abort the phase. The plan/review
agent's prompt asks the planner to either dispatch each highly-recommended
subagent OR record a skip rationale under ``## Subagent skips`` in the
iter sidecar (``iter/iter-NNN/plan.md`` or ``review.md``). This module
checks both signals before warning: if a subagent is named in the skip
section with a rationale, the warning is silenced and the audit logs
an info line instead so the developer can see the planner's reasoning.

Frontmatter compatibility: the ``mandatory: [...]`` field is preserved
as the schema-level marker (don't rename it across all user configs and
descriptors), but its SEMANTIC meaning is now "highly recommended in
this phase; planner may skip with rationale". The audit and the
catalog block in ``prompts.py`` are the source of truth for the user-
facing wording.
"""

from __future__ import annotations

import re
from pathlib import Path

from archon import log

from .base import _read_dispatch_jsonl
from .registry import build_registry


# A line under ``## Subagent skips`` of the form ``- <name>: <reason>``.
# Trailing whitespace is trimmed by the caller; the regex anchors on
# the bullet and the colon so that prose paragraphs in the same section
# don't accidentally match a subagent name. Reason is captured greedily
# to the end of the line.
_SKIP_LINE_RE = re.compile(
    r"^\s*[-*]\s+([A-Za-z0-9_.-]+)\s*:\s*(\S.*)$",
)


def check_mandatory_dispatched(
    project_path: Path,
    state_dir: Path,
    iter_num: int,
    phase: str,
    *,
    enabled: list[str] | None = None,
) -> list[str]:
    """Return names of highly-recommended subagents that didn't fire and
    weren't skipped with a documented rationale.

    ``phase`` is the calling phase name (``"plan"`` or ``"review"``).
    ``enabled`` mirrors the registry filter — pass the project's
    ``subagents.enabled`` config so the audit honors the same filter
    the catalog used when the agent was told what was available.

    Behavior:

    * Dispatched ⇒ silent (no log line).
    * Not dispatched, skip rationale recorded in ``## Subagent skips`` of
      the iter sidecar ⇒ info log (planner's reasoning is visible) and
      NOT returned in the missing list — the planner consciously skipped.
    * Not dispatched, no rationale ⇒ warning AND returned in the missing
      list (developer can see at a glance that the iter is non-compliant).
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

    rationales = _read_skip_rationales(state_dir, iter_num, phase)

    missing: list[str] = []
    skipped: list[tuple[str, str]] = []
    for name in expected:
        if name in dispatched:
            continue
        if name in rationales:
            skipped.append((name, rationales[name]))
        else:
            missing.append(name)

    if skipped:
        for name, reason in skipped:
            log.info(
                f"{phase} phase skipped highly-recommended subagent "
                f"`{name}` with rationale: {reason}"
            )

    if missing:
        joined = ", ".join(f"`{n}`" for n in missing)
        log.warn(
            f"{phase} phase finished without dispatching highly-recommended "
            f"subagent(s) {joined} and without a `## Subagent skips` entry "
            f"naming each. Either dispatch them next iter or record a "
            f"one-line rationale per skipped subagent in "
            f"`iter/iter-NNN/{phase}.md`."
        )
    return missing


def _read_skip_rationales(
    state_dir: Path,
    iter_num: int,
    phase: str,
) -> dict[str, str]:
    """Parse ``## Subagent skips`` from the iter sidecar.

    Returns ``{subagent_name: rationale}``. Plan phase reads from
    ``iter/iter-NNN/plan.md``; review phase from ``iter/iter-NNN/review.md``.
    Empty dict when the sidecar or the section is missing.

    Sidecar grammar:

    .. code-block:: markdown

        ## Subagent skips

        - strategy-critic: STRATEGY.md unchanged from iter-NNN and last verdict was SOUND
        - progress-critic: no prover output last iter

    Each entry is ``- <name>: <reason>``. Lines that don't match the
    pattern (free prose, blank lines, sub-bullets) are ignored, so the
    planner can mix prose with bullets without confusing the parser.
    """
    sidecar_name = f"{phase}.md"
    sidecar = state_dir / "iter" / f"iter-{iter_num:03d}" / sidecar_name
    if not sidecar.is_file():
        return {}
    try:
        text = sidecar.read_text(encoding="utf-8")
    except OSError:
        return {}

    # Walk lines until we hit the section heading, then collect bullets
    # until the next ``## `` heading.
    out: dict[str, str] = {}
    in_section = False
    for line in text.splitlines():
        stripped = line.rstrip()
        if not in_section:
            if stripped.lower() == "## subagent skips":
                in_section = True
            continue
        if stripped.startswith("## "):
            break
        m = _SKIP_LINE_RE.match(stripped)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out

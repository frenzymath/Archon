"""Helpers for the ``archon loop --resume`` flag.

Each phase calls :func:`pick_resume_session` before invoking the Claude
agent to decide whether to pass ``--resume <id>`` and replace the
priming prompt with a short continuation. After the agent returns, the
phase calls :func:`persist_session_id` to record the new (or continued)
session id in ``meta.json`` so a future ``--resume`` can find it.

When ``--resume`` is passed without ``--from``,
:func:`detect_last_interrupted_phase` picks the target by inspecting
the prior iter's ``meta.json`` — the first of plan / prover / review
whose status isn't ``"done"`` is the one that crashed.

The continuation prompts deliberately stay short: Claude Code's
``--resume`` replays the prior conversation into the model's context,
so re-priming with the full plan/prover/review prompt would just
duplicate instructions. Each prompt only nudges the model to refresh
state files that may have changed since the crash.
"""

from __future__ import annotations

import json
from pathlib import Path

from archon import log
from archon.state import extract_session_id, read_meta, write_meta


_PHASE_ORDER = ("plan", "prover", "review")


PLAN_CONTINUE = (
    "Continue your previous task from where you left off. "
    "If you need to refresh context, re-read .archon/PROGRESS.md, "
    ".archon/STRATEGY.md, .archon/USER_HINTS.md, and any "
    "task_pending.md / task_done.md. Then resume planning the current "
    "iteration."
)

PROVER_CONTINUE = (
    "Continue your previous task from where you left off. "
    "If you need to refresh context, re-read your assigned .lean file(s) "
    "and .archon/PROGRESS.md, then keep working on the in-progress "
    "proof obligation(s)."
)

REVIEW_CONTINUE = (
    "Continue your previous task from where you left off. "
    "If you need to refresh context, re-read "
    ".archon/proof-journal/current_session/attempts_raw.jsonl and "
    ".archon/PROJECT_STATUS.md, then finish the review."
)


def pick_resume_session(
    iter_meta: Path | None,
    meta_key: str,
    *,
    enabled: bool,
    label: str,
) -> str | None:
    """Return the stored session id for this phase, or None.

    ``enabled`` is the gate the caller computes (typically
    ``ctx.resume_phase == self.skip_token``; ``ctx.resume_phase`` is
    None on iter >= 1, so the gate naturally falls open for those).
    When disabled the function short-circuits to None so the caller does
    a fresh run. When enabled but no id is stored, logs a warning and
    falls back to fresh — matching the user's request that --resume
    degrade gracefully on missing state.
    """
    if not enabled or iter_meta is None:
        return None
    sid = read_meta(iter_meta, meta_key)
    if isinstance(sid, str) and sid:
        log.step(f"--resume: continuing {label} session {sid[:8]}…")
        return sid
    log.warn(
        f"--resume requested for {label}, but no '{meta_key}' in "
        f"{iter_meta.name}; running fresh."
    )
    return None


def persist_session_id(
    iter_meta: Path | None,
    jsonl_path: Path,
    meta_key: str,
) -> None:
    """Scrape the latest session_end.session_id from a phase JSONL and
    record it in meta.json. No-op when either path is missing or the
    JSONL contains no session_end (e.g. the agent crashed before its
    first 'result' event).
    """
    if iter_meta is None:
        return
    sid = extract_session_id(jsonl_path)
    if sid:
        write_meta(iter_meta, **{meta_key: sid})


def detect_last_interrupted_phase(iter_meta: Path | None) -> str | None:
    """Pick the phase to resume by inspecting the prior iter's meta.json.

    Walks plan → prover → review and returns the first phase whose
    ``<phase>.status`` is not ``"done"`` — that's the one that crashed
    or was interrupted. Returns ``None`` when every phase is marked
    ``"done"`` (the iteration completed cleanly; the caller should
    decide whether to redo from the start or skip the iteration).

    Falls back to ``"plan"`` when the meta.json is missing or unreadable
    — typical for a brand-new project where ``--resume`` was passed
    speculatively; running plan fresh is the safe default.
    """
    if iter_meta is None or not iter_meta.exists():
        return "plan"
    try:
        data = json.loads(iter_meta.read_text())
    except Exception:
        return "plan"
    if not isinstance(data, dict):
        return "plan"
    for phase in _PHASE_ORDER:
        section = data.get(phase)
        if not isinstance(section, dict):
            # No status block yet → this phase never started → resume here.
            return phase
        if section.get("status") != "done":
            return phase
    return None

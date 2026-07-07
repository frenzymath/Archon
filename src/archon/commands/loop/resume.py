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
import os
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
    cwd: Path | None = None,
    jsonl_fallback: Path | None = None,
) -> str | None:
    """Return the stored session id for this phase, or None.

    ``enabled`` is the gate the caller computes (typically
    ``ctx.resume_phase == self.skip_token``; ``ctx.resume_phase`` is
    None on iter >= 1, so the gate naturally falls open for those).
    When disabled the function short-circuits to None so the caller does
    a fresh run. When enabled but no id is stored anywhere, logs a
    warning and falls back to fresh — matching the user's request that
    --resume degrade gracefully on missing state.

    ``cwd`` is the project root (the working directory Claude Code was
    invoked from). When given, the function also verifies the session's
    JSONL file actually exists in Claude Code's store
    (``~/.claude/projects/<sanitized-cwd>/<sid>.jsonl``). If the file
    is missing, the stored id is stale (Claude Code's session log was
    rotated / cleaned) and passing it to ``claude --resume`` would
    error out. We warn and fall back to fresh instead.

    ``jsonl_fallback`` is the path to the phase's stream JSONL log
    (e.g. ``iter-NNN/plan.jsonl``). When the meta key is missing —
    which happens whenever the agent crashed BEFORE ``persist_session_id``
    ran at end of phase — the function reads the session id directly
    from the JSONL's ``session_meta`` event (emitted on session start).
    If found, the id is also stamped into ``iter_meta`` so the next
    inspection of meta.json shows it. The previous behavior (give up
    and run fresh) was the "lost session" failure mode the user
    reported: the JSONL had the id all along.
    """
    if not enabled or iter_meta is None:
        return None
    sid = read_meta(iter_meta, meta_key)
    recovered_from_jsonl = False
    if not isinstance(sid, str) or not sid:
        sid = _try_jsonl_fallback(
            jsonl_fallback, iter_meta, meta_key, label,
        )
        if sid is None:
            _warn_no_session(iter_meta, meta_key, label)
            return None
        recovered_from_jsonl = True

    if cwd is not None and not _claude_session_exists(cwd, sid):
        log.warn(
            f"--resume requested for {label}, but Claude Code has no "
            f"session log for id {sid[:8]}… in its store "
            f"(~/.claude/projects/<this-project>/). Running fresh."
        )
        return None

    if recovered_from_jsonl:
        log.step(
            f"--resume: recovered {label} session {sid[:8]}… from "
            f"phase JSONL (meta.json had no '{meta_key}'; prior run "
            f"crashed before persist_session_id ran)"
        )
    else:
        log.step(f"--resume: continuing {label} session {sid[:8]}…")
    return sid


def _try_jsonl_fallback(
    jsonl_fallback: Path | None,
    iter_meta: Path,
    meta_key: str,
    label: str,
) -> str | None:
    """Try to recover a session id from the phase's stream JSONL.

    Returns the recovered id (and stamps it into ``iter_meta`` for
    future lookups) or None. ``persist_session_id``'s same helper —
    ``extract_session_id`` — does the JSONL parsing; we just call it
    here when the meta lookup missed.
    """
    if jsonl_fallback is None or not jsonl_fallback.exists():
        return None
    from archon.state import extract_session_id
    sid = extract_session_id(jsonl_fallback)
    if not sid:
        return None
    # Stamp into meta so future invocations find it directly. This is
    # idempotent — a clean exit later will overwrite with the
    # session_end id (which is usually the same anyway).
    try:
        write_meta(iter_meta, **{meta_key: sid})
    except OSError:
        # Don't let a meta write failure block the resume.
        pass
    return sid


def _warn_no_session(
    iter_meta: Path, meta_key: str, label: str,
) -> None:
    """Log the "no session id anywhere" warning, with diagnostic detail."""
    available = _list_available_session_ids(iter_meta)
    iter_label = _iter_label_from_meta(iter_meta)
    if available:
        avail_str = ", ".join(available)
        log.warn(
            f"--resume requested for {label}, but no '{meta_key}' "
            f"stored in {iter_label}'s meta and no session_meta event "
            f"in the phase JSONL. Available session ids in meta: "
            f"{avail_str}. Running fresh."
        )
    else:
        log.warn(
            f"--resume requested for {label}, but no session ids are "
            f"stored in {iter_label}'s meta and no session_meta event "
            f"in the phase JSONL either (prior run crashed before "
            f"any session id was captured). Running fresh."
        )


def _iter_label_from_meta(meta_file: Path) -> str:
    """Return a human-readable iter label (e.g. ``iter-123``) for a
    meta.json path. Falls back to the filename when the parent is not
    an iter-NNN dir.
    """
    parent = meta_file.parent
    if parent.name.startswith("iter-"):
        return parent.name
    return meta_file.name


def _list_available_session_ids(meta_file: Path) -> list[str]:
    """Return a sorted list of ``<key>=<sid[:8]>…`` strings for every
    session id present in the meta.json. Used in the warning message
    so the user can see what their --resume options actually are.
    """
    if not meta_file.exists():
        return []
    try:
        data = json.loads(meta_file.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    out: list[str] = []
    for key, sid in _walk_session_ids("", data):
        out.append(f"{key}={sid[:8]}…")
    return sorted(out)


def _walk_session_ids(prefix: str, value: object):
    """Yield ``(dotted_key, session_id_str)`` pairs for every
    ``*.sessionId`` leaf in a nested meta dict.
    """
    if not isinstance(value, dict):
        return
    for k, v in value.items():
        full = f"{prefix}.{k}" if prefix else k
        if k == "sessionId" and isinstance(v, str) and v:
            yield prefix or k, v
        elif isinstance(v, dict):
            yield from _walk_session_ids(full, v)


def _claude_session_exists(cwd: Path, session_id: str) -> bool:
    """True iff Claude Code's session file for ``session_id`` exists.

    Claude Code stores sessions under
    ``~/.claude/projects/<sanitized-cwd>/<session_id>.jsonl``, where
    sanitization replaces every path separator ``/`` with ``-`` and
    keeps the leading dash (so an absolute path ``/home/x/y`` becomes
    ``-home-x-y``).
    """
    try:
        cwd_resolved = cwd.resolve()
    except OSError:
        # If we can't resolve, be permissive — let Claude Code's own
        # error surface.
        return True
    sanitized = str(cwd_resolved).replace(os.sep, "-")
    cfg_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(cfg_dir).expanduser() if cfg_dir else Path.home() / ".claude"
    candidate = base / "projects" / sanitized / f"{session_id}.jsonl"
    return candidate.is_file()


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

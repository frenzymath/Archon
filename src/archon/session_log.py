"""Read and classify Claude `session_end` events from prover JSONL logs.

Lane runs sometimes produce a clean ``session_end`` event but exit
non-zero (claude propagating a tool failure, for example). The lane
collector treats those as success when the session_end summary doesn't
contain known failure markers — that judgment lives here so both the
lane runtime and the test suite can reuse it.
"""

from __future__ import annotations

import json
from pathlib import Path


_FAILURE_MARKERS = (
    'api error',
    'tool_use_error',
    'error while processing your request',
    'build failed',
    'exited with code',
    'runner_failed',
)


def read_last_session_end(jsonl_path: str | Path) -> dict | None:
    """Return the final ``session_end`` event from the JSONL log, or None."""
    path = Path(jsonl_path)
    if not path.exists():
        return None
    last = None
    for raw_line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if row.get('event') == 'session_end':
            last = row
    return last


def session_end_indicates_success(session_end: dict | None) -> bool:
    """Return True iff the ``session_end`` summary lacks known failure markers."""
    if not session_end:
        return False
    summary = str(session_end.get('summary') or '').strip().lower()
    return not any(marker in summary for marker in _FAILURE_MARKERS)


def session_end_failure_kind(session_end: dict | None) -> str | None:
    """Classify a non-success ``session_end`` so the caller can report the
    real cause rather than a generic ``runner_failed``.

    Returns ``None`` when the summary doesn't match any known pattern —
    callers should fall back to ``runner_failed`` in that case.
    """
    if not session_end:
        return None
    summary = str(session_end.get('summary') or '').strip().lower()
    # Rate / quota signals from Anthropic, Moonshot, OpenAI, and the
    # bundled LiteLLM proxy all mention 429 or "rate limit" or TPD/RPM.
    if (
        '429' in summary
        or 'rate limit' in summary
        or 'rate_limit' in summary
        or 'rate-limit' in summary
        or 'tpd' in summary
        or 'rpm' in summary
        or 'tokens per day' in summary
        or 'tokens per minute' in summary
        or ('organization' in summary and 'limit' in summary)
    ):
        return 'rate_limited'
    if (
        'context length' in summary
        or 'context_length_exceeded' in summary
        or 'too many tokens' in summary
    ):
        return 'context_overflow'
    if 'auth' in summary and (
        'failed' in summary
        or 'invalid' in summary
        or '401' in summary
        or '403' in summary
    ):
        return 'auth_failed'
    if 'connection' in summary and ('refused' in summary or 'reset' in summary):
        return 'network_error'
    return None

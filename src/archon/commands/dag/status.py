"""DAG_STATUS.md helpers."""

from __future__ import annotations

from pathlib import Path

_STATUS_FILE = "DAG_STATUS.md"
_COMPLETE_MARKER = "## Status: COMPLETE"
_IN_PROGRESS_MARKER = "## Status: IN_PROGRESS"


def dag_status_path(state_dir: Path) -> Path:
    return state_dir / _STATUS_FILE


def dag_is_complete(state_dir: Path) -> bool:
    path = dag_status_path(state_dir)
    if not path.is_file():
        return False
    try:
        return _COMPLETE_MARKER in path.read_text(encoding="utf-8")
    except OSError:
        return False


def set_dag_in_progress(state_dir: Path) -> bool:
    """Demote a COMPLETE marker to IN_PROGRESS, preserving the rest of the file.

    Called when ``archon dag`` is (re)launched on a project the agent had
    marked COMPLETE: the user chose to run another elaboration pass, so the
    status must not short-circuit the run. The agent re-asserts COMPLETE at
    the end of the pass if the blueprint is in fact still done. Returns True
    iff a COMPLETE marker was found and rewritten.
    """
    path = dag_status_path(state_dir)
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if _COMPLETE_MARKER not in text:
        return False
    path.write_text(text.replace(_COMPLETE_MARKER, _IN_PROGRESS_MARKER), encoding="utf-8")
    return True

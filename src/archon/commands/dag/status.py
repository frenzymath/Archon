"""DAG_STATUS.md helpers."""

from __future__ import annotations

from pathlib import Path

_STATUS_FILE = "DAG_STATUS.md"
_COMPLETE_MARKER = "## Status: COMPLETE"


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

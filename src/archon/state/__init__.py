"""Project state helpers for the Archon loop.

Reads/writes `.archon/` state files: PROGRESS.md, iteration metadata,
task results, cost summaries.

The package is split by concern:
  - :mod:`.progress`  — PROGRESS.md (stage + objective files)
  - :mod:`.iteration` — iter dirs, meta.json, archival, sessions
  - :mod:`.cost`      — Claude session_end aggregation
"""

from .cost import CostData, cost_summary
from .iteration import (
    SessionRename,
    apply_session_renames,
    archive_task_results,
    cleanup_empty_sessions,
    next_iter_num,
    next_session_num,
    plan_session_renames,
    utcnow_iso,
    write_meta,
)
from .progress import is_complete, parse_objective_files, read_stage

__all__ = [
    "CostData",
    "cost_summary",
    "SessionRename",
    "apply_session_renames",
    "archive_task_results",
    "cleanup_empty_sessions",
    "next_iter_num",
    "next_session_num",
    "plan_session_renames",
    "utcnow_iso",
    "write_meta",
    "is_complete",
    "parse_objective_files",
    "read_stage",
]

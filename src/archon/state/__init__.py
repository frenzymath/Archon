"""Project state helpers for the Archon loop.

Reads/writes `.archon/` state files: PROGRESS.md, iteration metadata,
task results, cost summaries.

The package is split by concern:
  - :mod:`.progress`  — PROGRESS.md (stage + objective files)
  - :mod:`.iteration` — iter dirs, meta.json, archival, sessions
  - :mod:`.cost`      — Claude session_end aggregation
"""

from .cost import CostData, cost_summary, OperationCost
from .iteration import (
    SessionRename,
    apply_session_renames,
    archive_task_results,
    cleanup_empty_sessions,
    extract_session_id,
    next_iter_num,
    next_session_num,
    plan_session_renames,
    read_meta,
    utcnow_iso,
    write_meta,
)
from .iter_state import (
    IterSidecarSnapshot,
    format_recent_iter_sidecars_for_prompt,
    init_iter_sidecar_dir,
    iter_sidecar_dir,
    objectives_sidecar_path,
    plan_sidecar_path,
    read_recent_iter_sidecars,
    review_sidecar_path,
)
from .progress import (
    auto_fix_objectives,
    is_complete,
    normalize_stage_for_prompt_path,
    parse_objective_files,
    parse_objectives_with_modes,
    read_stage,
    write_stage,
)

__all__ = [
    "CostData",
    "OperationCost",
    "cost_summary",
    "SessionRename",
    "apply_session_renames",
    "archive_task_results",
    "cleanup_empty_sessions",
    "extract_session_id",
    "next_iter_num",
    "next_session_num",
    "plan_session_renames",
    "read_meta",
    "utcnow_iso",
    "write_meta",
    "auto_fix_objectives",
    "is_complete",
    "normalize_stage_for_prompt_path",
    "parse_objective_files",
    "parse_objectives_with_modes",
    "read_stage",
    "write_stage",
    "IterSidecarSnapshot",
    "format_recent_iter_sidecars_for_prompt",
    "init_iter_sidecar_dir",
    "iter_sidecar_dir",
    "objectives_sidecar_path",
    "plan_sidecar_path",
    "read_recent_iter_sidecars",
    "review_sidecar_path",
]

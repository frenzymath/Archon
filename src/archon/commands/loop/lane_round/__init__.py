"""Multi-lane round orchestration for the loop command.

This subpackage drives one iteration's worth of multi-lane work:
preview → assignment dispatch → writeback merge/promote. It sits
*inside* the loop command because it shares the loop's iteration shape
(iter_dir, iter_meta, etc.). The provider-agnostic primitives it builds
on live in :mod:`archon.multilane`.
"""

from .executor import LaneRoundExecutor
from .helpers import (
    assignment_code_snapshot_files,
    assignment_success,
    git_diff_files,
    non_archon_dirty_files,
    restore_repo_paths,
)
from .preview import LaneRoundPreviewRunner
from .writeback import (
    group_writeback_candidates_by_file,
    select_writeback_rows,
)

__all__ = [
    "LaneRoundExecutor",
    "LaneRoundPreviewRunner",
    "assignment_code_snapshot_files",
    "assignment_success",
    "git_diff_files",
    "non_archon_dirty_files",
    "restore_repo_paths",
    "group_writeback_candidates_by_file",
    "select_writeback_rows",
]

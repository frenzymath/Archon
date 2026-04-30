"""Multi-lane proving support for Archon.

This package holds the lane-oriented execution model used for future
multi-model proving rounds. The initial implementation intentionally
focuses on stable types/config/runtime scaffolding before changing the
main loop behaviour.
"""

from .types import (
    AssignmentStatus,
    LaneAssignment,
    LaneConfig,
    LaneExecutionConfig,
    LanePromotionConfig,
    LaneResult,
    LaneWorktreeConfig,
    MultiLaneConfig,
    PromoteCandidate,
    ProverJob,
    RuntimePaths,
)
from .config import (
    find_multilane_config,
    find_multilane_local_config,
    load_multilane_config,
    redacted_lane_summary,
    resolve_runtime_paths,
)

__all__ = [
    'AssignmentStatus',
    'LaneAssignment',
    'LaneConfig',
    'LaneExecutionConfig',
    'LanePromotionConfig',
    'LaneResult',
    'LaneWorktreeConfig',
    'MultiLaneConfig',
    'PromoteCandidate',
    'ProverJob',
    'RuntimePaths',
    'find_multilane_config',
    'find_multilane_local_config',
    'load_multilane_config',
    'redacted_lane_summary',
    'resolve_runtime_paths',
]

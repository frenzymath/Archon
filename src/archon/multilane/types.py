"""Core types for Archon multi-lane proving."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class AssignmentStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    done = 'done'
    failed = 'failed'
    cancelled = 'cancelled'


@dataclass(slots=True)
class LaneWorktreeConfig:
    path: str
    branch: str
    sync_mode: str = 'hard-reset'


@dataclass(slots=True)
class LaneExecutionConfig:
    max_parallel_jobs: int = 1
    enabled: bool = True


@dataclass(slots=True)
class LanePromotionConfig:
    enabled: bool = False
    mode: str = 'manual-only'


@dataclass(slots=True)
class LaneConfig:
    lane_id: str
    label: str
    provider: str
    claude_config_dir: str | None = None
    claude_settings_path: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    worktree: LaneWorktreeConfig = field(default_factory=lambda: LaneWorktreeConfig(path='', branch=''))
    execution: LaneExecutionConfig = field(default_factory=LaneExecutionConfig)
    promotion: LanePromotionConfig = field(default_factory=LanePromotionConfig)
    enabled: bool = True
    purpose: str | None = None


@dataclass(slots=True)
class MultiLaneConfig:
    enabled: bool = False
    version: int = 1
    base_ref: str = 'main'
    lanes: list[LaneConfig] = field(default_factory=list)

    def enabled_lanes(self) -> list[LaneConfig]:
        return [lane for lane in self.lanes if lane.enabled and lane.execution.enabled]

    def lane_by_id(self, lane_id: str) -> LaneConfig | None:
        for lane in self.lanes:
            if lane.lane_id == lane_id:
                return lane
        return None


@dataclass(slots=True)
class ProverJob:
    job_id: str
    iteration: int
    stage: str
    target_file: str
    objective_ref: str | None = None
    prompt_fragment: str | None = None
    source_plan_revision: str | None = None


@dataclass(slots=True)
class LaneAssignment:
    assignment_id: str
    lane_id: str
    job_id: str
    assigned_file: str
    worktree_path: str
    log_path: str
    result_path: str
    state_view_path: str | None = None
    status: AssignmentStatus = AssignmentStatus.queued


@dataclass(slots=True)
class LaneResult:
    assignment_id: str
    lane_id: str
    job_id: str
    success: bool
    exit_code: int | None = None
    duration_secs: int | None = None
    cost_usd: float | None = None
    changed_files: list[str] = field(default_factory=list)
    assigned_file_only: bool = False
    verification_passed: bool = False
    patch_path: str | None = None
    summary_path: str | None = None
    raw_log_path: str | None = None
    promote_readiness: str = 'manual-only'


@dataclass(slots=True)
class PromoteCandidate:
    candidate_id: str
    lane_id: str
    assignment_id: str
    source_commit: str
    target_file: str
    changed_files: list[str] = field(default_factory=list)
    patch_path: str | None = None
    safety_level: str = 'unsafe'


@dataclass(slots=True)
class RuntimePaths:
    root: Path
    runtime_dir: Path
    reports_dir: Path
    lanes_dir: Path

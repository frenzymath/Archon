"""Shared state for the loop command.

`LoopOptions` is the immutable bundle of CLI flags resolved against
`.archon/config.json`. `LoopContext` carries the live state that flows
between phases within an iteration (paths, current stage, sorry counts,
background services, …).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from archon.commands.tooling.blueprint import BlueprintServer


@dataclass
class LoopOptions:
    """User-facing knobs (CLI flags, resolved against project config)."""

    project_path: Path
    max_iterations: int
    max_parallel: int
    parallel: bool
    verbose_logs: bool
    no_review: bool
    no_finalize: bool
    no_git_commit: bool
    no_lake_build: bool
    no_blueprint_web: bool
    dry_run: bool
    no_dashboard: bool
    blueprint_server_flag: bool
    open_browser: bool
    model: str
    force_stage: str | None
    skip_first_iter: set[str]
    from_phase: str | None

    multilane_execute: bool
    multilane_preview: bool
    multilane_cfg: dict[str, Any]

    debug_feedback: bool = False
    resume: bool = False

    @property
    def do_git(self) -> bool:
        return not self.no_finalize and not self.no_git_commit

    @property
    def do_lake(self) -> bool:
        return not self.no_finalize and not self.no_lake_build

    @property
    def do_bp_web(self) -> bool:
        return not self.no_finalize and not self.no_blueprint_web

    @property
    def has_finalize(self) -> bool:
        return self.do_git or self.do_lake or self.do_bp_web

    @property
    def resume_phase(self) -> str | None:
        """Phase whose stored session id should be resumed on iter 0.

        Returns ``from_phase`` when ``--resume`` is on (entry.py defaults
        from_phase to ``"plan"`` for ``--resume`` without ``--from``), or
        ``None`` when resume is off. Phases compare this against their
        own ``skip_token`` to decide whether to pass ``--resume <id>``.
        """
        return self.from_phase if self.resume else None


@dataclass
class LoopContext:
    """Live state shared across phases.

    Phase classes mutate this in place — paths and resolved values are
    set up once at bootstrap, while `current_stage` / `prev_sorry` /
    `iter_*` fields are refreshed each iteration.
    """

    options: LoopOptions

    project_path: Path
    project_name: str
    state_dir: Path
    progress_file: Path
    log_dir: Path

    iter_index: int = 0
    iter_num: int = 0
    iter_dir: Path | None = None
    iter_meta: Path | None = None
    current_stage: str = ""
    skip_now: set[str] = field(default_factory=set)

    dashboard_url: str | None = None
    blueprint_url: str | None = None
    blueprint_server: "BlueprintServer | None" = None

    initial_sorry: int | None = None
    prev_sorry: int | None = None
    sorry_after: int | None = None

    @property
    def model(self) -> str:
        return self.options.model

    @property
    def verbose_logs(self) -> bool:
        return self.options.verbose_logs

    @property
    def dry_run(self) -> bool:
        return self.options.dry_run

    def force_stage(self) -> str | None:
        return self.options.force_stage

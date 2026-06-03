"""Shared state for the dag command.

`DagOptions` is the immutable bundle of CLI flags resolved against
`.archon/config.json`. `DagContext` carries the live state that flows
between phases within an iteration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from archon.agent import ClaudeBackend

if TYPE_CHECKING:
    from archon.commands.tooling.blueprint import BlueprintServer


@dataclass
class DagOptions:
    """User-facing knobs (CLI flags, resolved against project config)."""

    project_path: Path
    max_iterations: int
    max_parallel: int
    model: str
    verbose_logs: bool
    dry_run: bool
    no_dashboard: bool
    blueprint_server_flag: bool
    open_browser: bool
    lean_aware: bool

    backend: ClaudeBackend = field(default_factory=ClaudeBackend)


@dataclass
class DagContext:
    """Live state shared across phases."""

    options: DagOptions

    project_path: Path
    project_name: str
    state_dir: Path
    log_dir: Path

    iter_index: int = 0
    iter_num: int = 0
    iter_dir: Path | None = None
    iter_meta: Path | None = None

    dashboard_url: str | None = None
    blueprint_url: str | None = None
    blueprint_server: "BlueprintServer | None" = None

    @property
    def model(self) -> str:
        return self.options.model

    @property
    def backend(self) -> ClaudeBackend:
        return self.options.backend

    @property
    def verbose_logs(self) -> bool:
        return self.options.verbose_logs

    @property
    def dry_run(self) -> bool:
        return self.options.dry_run

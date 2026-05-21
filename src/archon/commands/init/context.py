"""Shared state for the init command."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from archon.commands.tooling.project import BootstrapReport


@dataclass
class InitContext:
    """Live state shared across init steps.

    Most state lives on disk (under `.archon/`); this carries the
    cross-step values that aren't persisted there — fresh-vs-merge mode,
    the model alias, and the bootstrap report passed from
    :class:`BootstrapStep` to :class:`SemanticPassStep`.
    """

    project_path: Path
    state_dir: Path
    fresh: bool
    model: str
    bootstrap_report: "BootstrapReport | None" = None

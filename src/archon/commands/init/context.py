"""Shared state for the init command."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from archon.agent import ClaudeBackend

if TYPE_CHECKING:
    from archon.commands.tooling.project import BootstrapReport


@dataclass
class InitContext:
    """Live state shared across init steps.

    Most state lives on disk (under `.archon/`); this carries the
    cross-step values that aren't persisted there — fresh-vs-merge mode,
    the model alias, and the bootstrap report passed from
    :class:`BootstrapStep` to :class:`SemanticPassStep`.

    ``fresh`` — True only for a brand-new init with no existing .archon/.
    ``overwrite`` — True when the user explicitly chose "overwrite" (or
        --force) during a re-init. Distinct from ``fresh`` so that
        CopyPromptsStep can force-replace existing prompt files without
        conflating that with the "first install" semantic.
    """

    project_path: Path
    state_dir: Path
    fresh: bool
    model: str
    overwrite: bool = False
    backend: ClaudeBackend = field(default_factory=ClaudeBackend)
    # Raw --harness flag, consumed only when writing a fresh config.json.
    harness: str | None = None
    bootstrap_report: "BootstrapReport | None" = None

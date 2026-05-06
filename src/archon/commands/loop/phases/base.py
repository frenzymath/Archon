"""Base class for loop phases.

A `Phase` reads/writes the shared `LoopContext` and signals back to the
orchestrator via a `PhaseResult`: did anything run, did the project
just hit COMPLETE (so the outer loop should break)?
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from ..context import LoopContext


@dataclass
class PhaseResult:
    skipped: bool = False
    completed: bool = False  # PROGRESS.md says COMPLETE — break the outer loop


class Phase(ABC):
    """Base class for one phase of the loop iteration.

    Subclasses set `name` / `number` for log headers and implement
    `run()`. Skipping logic (`--from <phase>`, `--no-review`, etc.) lives
    in the subclass — the base only carries the context handle.
    """

    name: ClassVar[str] = ""
    number: ClassVar[int] = 0
    skip_token: ClassVar[str] = ""

    def __init__(self, ctx: LoopContext) -> None:
        self.ctx = ctx

    @abstractmethod
    def run(self) -> PhaseResult:
        ...

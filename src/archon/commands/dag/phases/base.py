"""Base class for dag phases."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from ..context import DagContext


@dataclass
class DagPhaseResult:
    skipped: bool = False
    complete: bool = False  # agent declared DAG_STATUS: COMPLETE


class DagPhase(ABC):
    name: ClassVar[str] = ""
    number: ClassVar[int] = 0

    def __init__(self, ctx: DagContext) -> None:
        self.ctx = ctx

    @abstractmethod
    def run(self) -> DagPhaseResult:
        ...

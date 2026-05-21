"""Base class for an init step."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from ..context import InitContext


class InitStep(ABC):
    """One phase of `archon init`.

    Each step is given the shared :class:`InitContext` and runs its
    specific responsibility. Steps may mutate the context (e.g. the
    bootstrap step sets `bootstrap_report` for the semantic pass).
    """

    name: ClassVar[str] = ""
    number: ClassVar[int] = 0

    def __init__(self, ctx: InitContext) -> None:
        self.ctx = ctx

    @abstractmethod
    def run(self) -> None:
        ...

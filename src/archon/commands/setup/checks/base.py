"""Base class for one dependency check / install."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from ..installer import PackageInstaller


class DependencyCheck(ABC):
    """One probe-and-install unit for `archon setup`.

    Subclasses set `name` for log readability and implement `run()`,
    returning True iff the dependency is available after the check.
    """

    name: ClassVar[str] = ""

    def __init__(self, installer: PackageInstaller) -> None:
        self.installer = installer

    @abstractmethod
    def run(self) -> bool:
        ...

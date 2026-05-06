"""`archon setup` command — install system-level dependencies.

Entry point: :func:`setup`. Orchestrator: :class:`SetupCommand`. Each
dependency is its own :class:`DependencyCheck` subclass under
:mod:`~archon.commands.setup.checks`.
"""

from .command import SetupCommand
from .entry import setup
from .installer import PackageInstaller

__all__ = ["setup", "SetupCommand", "PackageInstaller"]

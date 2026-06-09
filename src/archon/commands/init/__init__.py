"""`archon init` command - deterministic bootstrap + semantic pass.

The Typer entry point is :func:`init`. The orchestrator is
:class:`~archon.commands.init.command.InitCommand`; individual phases
live under :mod:`~archon.commands.init.steps`.
"""

from .command import InitCommand
from .context import InitContext
from .entry import init

__all__ = ["init", "InitCommand", "InitContext"]

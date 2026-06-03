"""`archon dag` command — full-project blueprint elaboration.

The Typer entry point is :func:`dag`. The orchestrator is
:class:`~archon.commands.dag.command.DagCommand`; phases live under
:mod:`~archon.commands.dag.phases`.
"""

from .command import DagCommand
from .context import DagContext, DagOptions
from .entry import dag

__all__ = ["dag", "DagCommand", "DagContext", "DagOptions"]

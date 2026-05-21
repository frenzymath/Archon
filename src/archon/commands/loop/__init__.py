"""`archon loop` command — plan → refactor → prover → review → finalize.

The Typer entry point is :func:`loop`. The orchestrator is
:class:`~archon.commands.loop.command.LoopCommand`; phases live under
:mod:`~archon.commands.loop.phases`. Lane-round work (multi-provider
parallel proving) lives under :mod:`~archon.commands.loop.lane_round`.
"""

from .command import LoopCommand
from .context import LoopContext, LoopOptions
from .entry import loop

__all__ = ["loop", "LoopCommand", "LoopContext", "LoopOptions"]

"""Pre-agent compactors that rewrite oversized state files in place.

Each compactor reads one ``.archon/<file>.md``, decides whether it needs
trimming (``needs_compaction``), and if so spawns a Claude run that
shrinks old narrative while preserving every actionable detail (errors,
dead ends, recent iterations). Output overwrites the same file.

Compactors are invoked by ``PreCompactPhase`` ahead of the agent that
will read the file — STRATEGY.md / task_pending.md / task_done.md before
the plan agent, PROJECT_STATUS.md before the review agent.

The compactor's stream is a JSONL like every other phase agent, so the
dashboard can render it. The rewrite gets its own inner-git commit
``archon[NNN/precompact/<target>]`` so the user can audit and revert.
"""

from .base import Compactor, CompactorResult
from .strategy import StrategyCompactor
from .task_pending import TaskPendingCompactor
from .task_done import TaskDoneCompactor
from .project_status import ProjectStatusCompactor

__all__ = [
    "Compactor",
    "CompactorResult",
    "StrategyCompactor",
    "TaskPendingCompactor",
    "TaskDoneCompactor",
    "ProjectStatusCompactor",
]

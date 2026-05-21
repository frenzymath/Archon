"""`archon dashboard` command — start the web dashboard.

Entry point: :func:`dashboard`. Orchestrator:
:class:`DashboardCommand`. Server lifecycle, port probing, and pid-file
management are split into their own modules.
"""

from .command import DashboardCommand
from .entry import dashboard

__all__ = ["dashboard", "DashboardCommand"]

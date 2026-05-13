"""RefactorSubagent — used by both the CLI and the in-loop tool.

Wraps ``build_refactor_prompt`` (which is now slug-aware) and pins the
report path to ``task_results/refactor-<slug>.md``. The CLI flow uses
``slug="cli"``; the autonomous loop generates a fresh slug per call.
"""

from __future__ import annotations

from pathlib import Path

from archon.commands.tooling.project_config import load_project_config
from archon.prompts import build_refactor_prompt

from .base import Subagent


class RefactorSubagent(Subagent):
    name = "refactor"

    def build_prompt(
        self, *, directive: str, slug: str, iter_num: int,
    ) -> str:
        state_dir = self.project_path / ".archon"
        cfg = load_project_config(self.project_path)
        debug_feedback = bool(cfg.loop_section().get("debug_feedback"))
        return build_refactor_prompt(
            self.project_path.name, self.project_path, state_dir,
            directive, iter_num, slug,
            debug_feedback=debug_feedback,
        )

    def report_path(self, slug: str) -> Path:
        return (
            self.project_path / ".archon" / "task_results"
            / f"refactor-{slug}.md"
        )
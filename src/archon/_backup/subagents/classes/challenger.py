"""ChallengerSubagent — sanity-check theorems for new or doubted definitions.

Parallel to RefactorSubagent. The challenger writes a Lean file under
``Challenges/<Name>.lean`` (Name comes from the directive, not the slug)
plus a per-call report at ``task_results/challenger-<slug>.md``.
``report_path`` returns the report; the Lean file is named by the
agent based on the directive contents.
"""

from __future__ import annotations

from textwrap import dedent

from archon.commands.tooling.project_config import load_project_config
from archon.prompts import debug_feedback_block

from .base import Subagent


class ChallengerSubagent(Subagent):
    name = "challenger"

    def build_prompt(
        self, *, directive: str, slug: str, iter_num: int,
    ) -> str:
        state_dir = self.project_path / ".archon"
        cfg = load_project_config(self.project_path)
        debug_feedback = bool(cfg.loop_section().get("debug_feedback"))
        return dedent(f"""\
            You are the challenger subagent for project '{self.project_path.name}'.
            Archon iteration: {iter_num:03d}.
            Project directory: {self.project_path}
            Project state directory: {state_dir}

            Slug: {slug}

            Read {state_dir}/prompts/challenger.md for your full instructions.
            Read {state_dir}/CLAUDE.md for project-wide context.

            Your directive (also reproduced below for convenience) is at:
              {state_dir}/logs/iter-{iter_num:03d}/challenger-{slug}-directive.md

            DIRECTIVE:
            {directive}

            The directive's `Name` field gives the Challenges/<Name>.lean
            filename. Conventionally the slug is the kebab-case form of
            Name, but treat them as separate inputs.

            Report: {state_dir}/task_results/challenger-{slug}.md

            Do not modify any of the target files in-place; all sanity
            checks go into Challenges/<Name>.lean. Do not write to
            PROGRESS.md, STRATEGY.md, task_pending.md, task_done.md,
            USER_HINTS.md, or blueprint chapters.
            """) + debug_feedback_block(
                debug_feedback, state_dir, f"challenger ({slug})", iter_num,
            )
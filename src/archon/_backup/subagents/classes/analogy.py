"""AnalogySubagent — design-rationale lookups in Mathlib.

Parallel structure to RefactorSubagent: builds a prompt that tells the
agent where to find its directive and what slug to use, then delegates
the actual run to ``Subagent.run``. The analogy agent writes both a
persistent file (``analogies/<slug>.md`` — kept for future iterations
to re-read) and a per-call report (``task_results/analogy-<slug>.md``).
``report_path`` returns the latter; the persistent file is implicit.
"""

from __future__ import annotations

from textwrap import dedent

from archon.commands.tooling.project_config import load_project_config
from archon.prompts import debug_feedback_block

from .base import Subagent


class AnalogySubagent(Subagent):
    name = "analogy"

    def build_prompt(
        self, *, directive: str, slug: str, iter_num: int,
    ) -> str:
        # Note: ``directive`` is also passed inline so the agent doesn't
        # need to re-read the file off disk if the wrapper has already
        # loaded it. We still tell it the on-disk location for
        # consistency with the other subagents and so it can refer back
        # to it if it loses track in a long run.
        state_dir = self.project_path / ".archon"
        cfg = load_project_config(self.project_path)
        debug_feedback = bool(cfg.loop_section().get("debug_feedback"))
        return dedent(f"""\
            You are the analogy subagent for project '{self.project_path.name}'.
            Archon iteration: {iter_num:03d}.
            Project directory: {self.project_path}
            Project state directory: {state_dir}

            Slug: {slug}

            Read {state_dir}/prompts/analogy.md for your full instructions.
            Read {state_dir}/CLAUDE.md for project-wide context.

            Your directive (also reproduced below for convenience) is at:
              {state_dir}/logs/iter-{iter_num:03d}/analogy-{slug}-directive.md

            DIRECTIVE:
            {directive}

            Persistent output: {self.project_path}/analogies/{slug}.md
            Report:            {state_dir}/task_results/analogy-{slug}.md

            Do not write to PROGRESS.md, STRATEGY.md, task_pending.md,
            task_done.md, USER_HINTS.md, or any project .lean file.
            """) + debug_feedback_block(
                debug_feedback, state_dir, f"analogy ({slug})", iter_num,
            )
"""CoordinatorSubagent — decomposes a multi-part directive into sub-directives.

The coordinator's job is *dispatch*, not domain-specific work. Use it
when the plan agent has a task that naturally fans out across many
chapters / files / phases (e.g. "rewrite the blueprint's chapters on
schemes", "audit all files under Algebra/ for stand-in defs"). The
coordinator reads the directive, decides a tree of sub-directives
with disjoint write-domains, dispatches children via Bash (parallel
where independent), aggregates their reports, and writes a
consolidated summary.

The actual decomposition logic lives in the prompt
(``.archon/prompts/coordinator.md``) — this Python class is a thin
wrapper, parallel to RefactorSubagent.
"""

from __future__ import annotations

from textwrap import dedent

from archon.commands.tooling.project_config import load_project_config
from archon.prompts import debug_feedback_block

from .base import Subagent


class CoordinatorSubagent(Subagent):
    name = "coordinator"

    def build_prompt(
        self, *, directive: str, slug: str, iter_num: int,
    ) -> str:
        state_dir = self.project_path / ".archon"
        cfg = load_project_config(self.project_path)
        debug_feedback = bool(cfg.loop_section().get("debug_feedback"))
        return dedent(f"""\
            You are the coordinator subagent for project '{self.project_path.name}'.
            Archon iteration: {iter_num:03d}.
            Project directory: {self.project_path}
            Project state directory: {state_dir}

            Slug: {slug}

            Read {state_dir}/prompts/coordinator.md for your full instructions.
            Read {state_dir}/CLAUDE.md for project-wide context.

            Your directive (also reproduced below for convenience) is at:
              {state_dir}/logs/iter-{iter_num:03d}/coordinator-{slug}-directive.md

            DIRECTIVE:
            {directive}

            Report: {state_dir}/task_results/coordinator-{slug}.md
            (When invoked as a child of another subagent, your report
            lands at task_results/<parent_slug>/coordinator-{slug}.md
            — the Archon CLI handles the path automatically.)

            Your write-domain is whatever the directive declares. When
            you spawn child subagents, every child's declared
            write-domain must be a strict subset of yours, and
            siblings must declare disjoint domains. Pass your slug
            ({slug}) as their --parent-slug.
            """) + debug_feedback_block(
                debug_feedback, state_dir, f"coordinator ({slug})", iter_num,
            )

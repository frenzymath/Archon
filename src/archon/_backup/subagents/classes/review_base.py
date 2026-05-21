"""Shared base for read-only review subagents.

Each of the five review subagents (Workstream E) has the same
external shape — read project sources + blueprint chapters, write a
report — and differs only in *what aspect of the project it audits*.
Putting the boilerplate in one place keeps each concrete subagent
class trivially short (typically just a ``name = "..."`` line).

All review subagents are **read-only on project source and the
blueprint**. Their declared write-domain at the wrapper level is
their own report file under ``task_results/...``. The orchestrator
that dispatches them (review phase, plan agent, or coordinator)
passes ``--write-domain 'task_results/**'`` (or a tighter pattern).
"""

from __future__ import annotations

from textwrap import dedent

from archon.commands.tooling.project_config import load_project_config
from archon.prompts import debug_feedback_block

from .base import Subagent


class ReviewSubagent(Subagent):
    """Base class for review-* subagents.

    Subclasses set ``name`` (e.g. ``"review-definition-correctness"``).
    The prompt is built generically: it points the agent at
    ``prompts/<name>.md`` for the audit rules, names the directive
    file, names the report path, and forbids project-source writes.
    """

    name: str = ""

    def build_prompt(
        self, *, directive: str, slug: str, iter_num: int,
    ) -> str:
        state_dir = self.project_path / ".archon"
        cfg = load_project_config(self.project_path)
        debug_feedback = bool(cfg.loop_section().get("debug_feedback"))
        return dedent(f"""\
            You are the {self.name} subagent for project '{self.project_path.name}'.
            Archon iteration: {iter_num:03d}.
            Project directory: {self.project_path}
            Project state directory: {state_dir}

            Slug: {slug}

            Read {state_dir}/prompts/{self.name}.md for your full instructions.
            Read {state_dir}/CLAUDE.md for project-wide context.

            Your directive (also reproduced below for convenience) is at:
              {state_dir}/logs/iter-{iter_num:03d}/{self.name}-{slug}-directive.md

            DIRECTIVE:
            {directive}

            Report: {state_dir}/task_results/{self.name}-{slug}.md
            (When invoked as a child of another subagent, your report
            lands at task_results/<parent_slug>/{self.name}-{slug}.md
            — the Archon CLI handles the path automatically.)

            You are READ-ONLY on every project source file (.lean files,
            blueprint chapters, archon-protected.yaml, every state file
            under .archon/ except your own report). Your declared
            write-domain at dispatch time is your report file only.
            """) + debug_feedback_block(
                debug_feedback, state_dir, f"{self.name} ({slug})", iter_num,
            )

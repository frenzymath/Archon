"""Hand off to the configured harness after deterministic bootstrap.

The semantic pass verifies the bootstrap, reorganizes loose reference files,
fills in README / summary.md prose, walks the user through
archon-protected.yaml, and proposes initial objectives. Everything
deterministic has already happened before this step starts.
"""

from __future__ import annotations

import json
import textwrap

from archon import log
from archon.commands.tooling.project_config import load_project_config
from archon.agent import build_runner
from archon.commands.tooling import protect
from archon.commands.tooling.project import ProjectLayout

from ..utils import parse_stage
from .base import InitStep


class SemanticPassStep(InitStep):
    name = "Semantic pass"
    number = 7

    def run(self) -> None:
        ctx = self.ctx
        progress_md = ctx.state_dir / "PROGRESS.md"
        stage = parse_stage(progress_md)
        project_name = ctx.project_path.name

        if stage != "init":
            log.success(f"Init already complete — current stage: {stage}")
            log.step(f"Next: archon loop {ctx.project_path}")
            return

        # Re-inspect to pick up what the bootstrap just did.
        layout = ProjectLayout.inspect(ctx.project_path)

        log.header(f"Initializing project: {project_name}")
        log.step("Handing off to the configured harness for the semantic pass.")

        bootstrap_summary = self._build_bootstrap_summary(layout)

        if ctx.headless:
            objective_instruction = (
                "IMPORTANT: You are running in a headless CI/automation environment. "
                "Do NOT ask the user to confirm or adjust objectives. Make your best judgment, "
                "write the initial objectives directly to PROGRESS.md, and proceed."
            )
            verification_instruction = (
                "When you have finished the init steps, run /archon-lean4:doctor to verify "
                "the full setup before exiting."
            )
        else:
            objective_instruction = (
                "IMPORTANT: After checking the project state, do NOT write initial objectives \\\n"
                "            on your own. Instead, propose what you think the objectives should be, then \\\n"
                "            ask the user to confirm or adjust before writing them to PROGRESS.md. Wait \\\n"
                "            for the user's reply."
            )
            verification_instruction = (
                "When the user has confirmed and you have finished the init steps, run \\\n"
                "            /archon-lean4:doctor to verify the full setup before exiting."
            )

        prompt = textwrap.dedent(f"""\
            You are in the init stage for project '{project_name}' at {ctx.project_path}. \
            Read {ctx.state_dir}/AGENTS.md, then read {ctx.state_dir}/prompts/init.md and follow \
            its instructions. Project state files are in {ctx.state_dir}/. Write PROGRESS.md \
            and other state files there, not in the project directory.

            {objective_instruction}

            {verification_instruction}

            Remark: A bootstrap process has already run to install lake, mathlib, and \
            other deterministic setup, including creating an empty \
            {protect.PROTECTED_FILENAME} at the project root. Here is its report:
            {json.dumps(bootstrap_summary, indent=2)}
            """)
            
        if ctx.focus:
            prompt += f"\n\nIMPORTANT CLI INSTRUCTION:\n{ctx.focus}\nPlease ensure you factor this into your init tasks.\n"

        cfg = load_project_config(ctx.project_path)
        runner = build_runner(
            role="init", model=ctx.model, cfg=cfg, backend=ctx.backend, harness=ctx.harness,
        )
        if ctx.headless:
            runner.run(prompt, cwd=ctx.project_path)
        else:
            runner.run_interactive(prompt, cwd=ctx.project_path)

        new_stage = parse_stage(progress_md)
        if new_stage == "init":
            log.warn("Stage is still 'init' — setup may not be complete")
            log.step(f"Re-run: archon init {ctx.project_path}")
        else:
            log.success(f"Init complete — stage is now: {new_stage}")
            log.step(f"Next: archon loop {ctx.project_path}")

    # ── private ─────────────────────────────────────────────────────────

    def _build_bootstrap_summary(self, layout: ProjectLayout) -> dict:
        """Structured report for Claude — exactly what the bootstrap did/didn't do.

        Lets Claude skip re-checking the deterministic steps.
        """
        ctx = self.ctx
        report = ctx.bootstrap_report
        return {
            "actions": report.actions if report else [],
            "warnings": report.warnings if report else [],
            "skipped": report.skipped if report else [],
            "layout": {
                "has_lakefile": layout.has_lakefile,
                "lakefile_kind": layout.lakefile_kind,
                "has_mathlib": layout.has_mathlib,
                "has_blueprint": layout.has_blueprint,
                "has_references_dir": layout.has_references_dir,
                "has_readme": layout.has_readme,
                "lean_file_count": len(layout.lean_files),
                "loose_references": [
                    str(p.relative_to(ctx.project_path))
                    for p in layout.loose_references
                ],
            },
            "stage": (
                report.stage_report.stage
                if report and report.stage_report else "autoformalize"
            ),
            "sorry_count": (
                report.stage_report.sorry_count
                if report and report.stage_report else 0
            ),
            "decl_count": (
                report.stage_report.decl_count
                if report and report.stage_report else 0
            ),
        }

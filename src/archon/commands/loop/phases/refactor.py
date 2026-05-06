"""Refactor phase: conditional on plan agent writing REFACTOR_DIRECTIVE.md.

When triggered, runs the refactor agent, re-runs the plan agent in
verification mode, archives the directive + report, and re-checks for
COMPLETE.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from textwrap import dedent

from archon import log
from archon.agent import ClaudeAgent
from archon.commands.tooling.iteration import commit_phase
from archon.prompts import build_refactor_prompt
from archon.state import is_complete, read_stage, utcnow_iso, write_meta

from ..sorry_count import count_sorries
from .base import Phase, PhaseResult


_DIRECTIVE_FILE = "REFACTOR_DIRECTIVE.md"
_DIRECTIVE_PLACEHOLDER = (
    "# Refactor Directive\n\n"
    "<!-- Plan agent: write your refactoring directive here. -->\n"
    "<!-- The refactor agent will execute it at the start of the next iteration. -->\n"
    "<!-- This file is cleared after each refactor run. -->\n"
)


def read_refactor_directive(state_dir: Path) -> str | None:
    """Return the directive content if non-empty, else None.

    A directive is "empty" if it has no real content lines: only
    headings, HTML comments, and blank lines indicate the placeholder
    template the loop wrote at the end of the previous iteration.
    """
    directive_file = state_dir / _DIRECTIVE_FILE
    if not directive_file.exists():
        return None
    content = directive_file.read_text().strip()
    if not content:
        return None
    lines = [
        l.strip() for l in content.splitlines()
        if l.strip() and not l.strip().startswith("<!--")
    ]
    non_header = [l for l in lines if not l.startswith("#")]
    if not non_header:
        return None
    return content


def archive_refactor_directive(directive: str, iter_dir: Path) -> None:
    iter_dir.mkdir(parents=True, exist_ok=True)
    archive_path = iter_dir / "refactor-directive.md"
    header = dedent(f"""\
        <!-- Archived from {_DIRECTIVE_FILE} at {utcnow_iso()} -->
        <!-- This is the directive the plan agent wrote for the refactor agent in this iteration. -->

        """)
    archive_path.write_text(header + directive + "\n")


def archive_refactor_report(state_dir: Path, iter_dir: Path) -> None:
    report_src = state_dir / "task_results" / "refactor.md"
    if not report_src.exists():
        return
    iter_dir.mkdir(parents=True, exist_ok=True)
    report_dst = iter_dir / "refactor-report.md"
    try:
        shutil.copy2(report_src, report_dst)
    except OSError:
        pass


def clear_refactor_directive(state_dir: Path) -> None:
    directive_file = state_dir / _DIRECTIVE_FILE
    if directive_file.exists():
        directive_file.write_text(_DIRECTIVE_PLACEHOLDER)


def build_post_refactor_plan_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
    iter_num: int,
) -> str:
    return dedent(f"""\
        You are the plan agent for project '{project_name}'. Current stage: {stage}.
        Archon iteration: {iter_num:03d} (canonical — use this number anywhere you reference an iteration; do NOT invent your own counter).
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/plan.md and {state_dir}/PROGRESS.md.
        All state files (PROGRESS.md, task_pending.md, task_done.md, USER_HINTS.md, task_results/) are in {state_dir}/.
        The .lean files are in {project_path}/.

        IMPORTANT — POST-REFACTOR VERIFICATION PASS:
        The refactor agent has just run. Read {state_dir}/task_results/refactor.md FIRST
        to understand what changed. Then follow the "Post-Refactor Verification" section
        in your prompt (plan.md).

        CRITICAL: Do NOT write a new {_DIRECTIVE_FILE} in this pass. The refactor
        loop runs at most once per iteration. If further refactoring is needed, document
        it in task_pending.md — it will be addressed in the next iteration.""")


class RefactorPhase(Phase):
    name = "Refactor agent"
    number = 2
    skip_token = "refactor"

    def run(self) -> PhaseResult:
        ctx = self.ctx

        if self.skip_token in ctx.skip_now:
            log.phase(self.number, f"{self.name} — skipped (--from)")
            return PhaseResult(skipped=True)

        if ctx.options.no_refactor or ctx.dry_run:
            return PhaseResult(skipped=True)

        directive = read_refactor_directive(ctx.state_dir)
        if not directive:
            return PhaseResult(skipped=True)

        self._run_refactor_agent(directive)
        self._verify_with_plan_agent()
        archive_refactor_report(ctx.state_dir, ctx.iter_dir)

        sorry_after_refactor = count_sorries(ctx.project_path)
        if sorry_after_refactor is not None:
            log.info(f"Sorry count after refactor: {sorry_after_refactor}")

        commit_phase(
            ctx.project_path, iter_num=ctx.iter_num, phase="refactor",
            summary=(
                f"sorry={sorry_after_refactor}"
                if sorry_after_refactor is not None
                else "refactor complete"
            ),
        )

        if is_complete(ctx.progress_file, ctx.force_stage()):
            log.success("Plan agent set stage to COMPLETE after refactor. Exiting loop.")
            return PhaseResult(completed=True)

        ctx.current_stage = read_stage(ctx.progress_file, ctx.force_stage())
        return PhaseResult()

    def _run_refactor_agent(self, directive: str) -> None:
        ctx = self.ctx
        log.phase(self.number, self.name)
        log.info("Plan agent requested structural changes")

        directive_lines = directive.strip().splitlines()
        preview_lines = [
            l.strip() for l in directive_lines
            if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("<!--")
        ]
        for line in preview_lines[:5]:
            log.step(line)
        if len(preview_lines) > 5:
            log.step("... (%d more lines)" % (len(preview_lines) - 5))

        archive_refactor_directive(directive, ctx.iter_dir)
        write_meta(ctx.iter_meta, **{"refactor.status": "running"})

        refactor_start = time.monotonic()
        prompt = build_refactor_prompt(
            ctx.project_name, ctx.project_path, ctx.state_dir, directive,
            ctx.iter_num,
        )
        refactor_log = ctx.iter_dir / "refactor"
        ok = ClaudeAgent(model=ctx.model, role="refactor").run(
            prompt, cwd=ctx.project_path,
            log_base=refactor_log, verbose_logs=ctx.verbose_logs,
        )
        refactor_secs = int(time.monotonic() - refactor_start)

        write_meta(ctx.iter_meta, **{
            "refactor.status": "done" if ok else "error",
            "refactor.durationSecs": refactor_secs,
        })

        if ok:
            log.success("Refactor agent finished (%ds)" % refactor_secs)
        else:
            log.error("Refactor agent failed (%ds)" % refactor_secs)

        clear_refactor_directive(ctx.state_dir)

    def _verify_with_plan_agent(self) -> None:
        ctx = self.ctx
        log.step("Re-running plan agent to verify refactor results...")
        post_refactor_prompt = build_post_refactor_plan_prompt(
            ctx.project_name, ctx.project_path, ctx.state_dir, ctx.current_stage,
            ctx.iter_num,
        )
        plan_log2 = ctx.iter_dir / "plan-post-refactor"
        ClaudeAgent(model=ctx.model, role="plan-post-refactor").run(
            post_refactor_prompt, cwd=ctx.project_path,
            log_base=plan_log2, verbose_logs=ctx.verbose_logs,
        )

        rogue_directive = read_refactor_directive(ctx.state_dir)
        if rogue_directive:
            log.warn(
                "Post-refactor plan agent wrote another REFACTOR_DIRECTIVE.md — "
                "clearing it to prevent infinite loop. It will be reconsidered next iteration.",
            )
            clear_refactor_directive(ctx.state_dir)

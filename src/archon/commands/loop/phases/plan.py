"""Plan phase: invoke the plan agent and refresh `current_stage`."""

from __future__ import annotations

import time

from archon import log
from archon.agent import ClaudeAgent
from archon.commands.tooling.iteration import commit_phase
from archon.prompts import build_plan_prompt
from archon.state import is_complete, read_stage, write_meta

from .base import Phase, PhaseResult


class PlanPhase(Phase):
    name = "Plan agent"
    number = 1
    skip_token = "plan"

    def run(self) -> PhaseResult:
        ctx = self.ctx
        if self.skip_token in ctx.skip_now:
            log.phase(self.number, f"{self.name} — skipped (--from)")
            ctx.current_stage = read_stage(ctx.progress_file, ctx.force_stage())
            return PhaseResult(skipped=True)

        log.phase(self.number, self.name)
        plan_start = time.monotonic()
        plan_prompt = build_plan_prompt(
            ctx.project_name, ctx.project_path, ctx.state_dir, ctx.current_stage,
            ctx.iter_num,
            ignore_multilane=(
                ctx.options.multilane_preview or ctx.options.multilane_execute
            ),
        )

        if ctx.dry_run:
            log.step("[dry-run] Plan prompt:")
            print(plan_prompt)
        else:
            plan_log = ctx.iter_dir / "plan"
            ClaudeAgent(model=ctx.model, role="plan").run(
                plan_prompt, cwd=ctx.project_path,
                log_base=plan_log, verbose_logs=ctx.verbose_logs,
                env_overrides={"ARCHON_ITER_NUM": f"{ctx.iter_num:03d}"},
            )

        plan_secs = int(time.monotonic() - plan_start)
        log.info(f"Plan phase finished ({plan_secs}s)")
        if not ctx.dry_run:
            write_meta(
                ctx.iter_meta,
                **{"plan.status": "done", "plan.durationSecs": plan_secs},
            )
            commit_phase(
                ctx.project_path, iter_num=ctx.iter_num, phase="plan",
                summary=f"stage={ctx.current_stage} ({plan_secs}s)",
            )

        if is_complete(ctx.progress_file, ctx.force_stage()):
            log.success("PROGRESS.md says COMPLETE. Exiting loop.")
            return PhaseResult(completed=True)

        ctx.current_stage = read_stage(ctx.progress_file, ctx.force_stage())
        return PhaseResult()

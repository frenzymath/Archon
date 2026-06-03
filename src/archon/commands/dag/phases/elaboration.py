"""DagElaborationPhase — invoke the DAG elaboration agent."""

from __future__ import annotations

import time

from archon import log
from archon.agent import ClaudeAgent
from archon.commands.tooling.iteration import commit_phase
from archon.state import write_meta

from .base import DagPhase, DagPhaseResult
from ..status import dag_is_complete


class DagElaborationPhase(DagPhase):
    name = "DAG elaboration agent"
    number = 1

    def run(self) -> DagPhaseResult:
        ctx = self.ctx
        log.phase(self.number, self.name)
        start = time.monotonic()

        from archon.prompts import build_dag_prompt
        prompt = build_dag_prompt(
            ctx.project_name,
            ctx.project_path,
            ctx.state_dir,
            ctx.iter_num,
            lean_aware=ctx.options.lean_aware,
        )

        if ctx.dry_run:
            log.step("[dry-run] DAG prompt:")
            print(prompt)
        else:
            dag_log = ctx.iter_dir / "dag"
            ClaudeAgent(model=ctx.model, role="dag", backend=ctx.backend).run(
                prompt,
                cwd=ctx.project_path,
                log_base=dag_log,
                verbose_logs=ctx.verbose_logs,
                env_overrides={"ARCHON_ITER_NUM": f"{ctx.iter_num:03d}"},
            )

        secs = int(time.monotonic() - start)
        log.info(f"DAG elaboration phase finished ({secs}s)")

        if not ctx.dry_run:
            # Audit that the agent actually dispatched the subagents that
            # are mandatory for the dag role (blueprint-writer /
            # -reviewer). Warns when one neither fired nor was skipped
            # with a documented rationale under `## Subagent skips` in
            # iter/iter-NNN/dag.md — the same affordance the plan phase uses.
            from archon.commands.tooling.project_config import (
                apply_forced_subagents,
                load_project_config,
                resolve_subagents_enabled,
            )
            from archon.subagents.audit import check_mandatory_dispatched

            cfg = load_project_config(ctx.project_path)
            enabled = apply_forced_subagents(
                ctx.project_path, resolve_subagents_enabled(cfg),
            )
            check_mandatory_dispatched(
                ctx.project_path, ctx.state_dir, ctx.iter_num,
                phase="dag", enabled=enabled,
            )
            write_meta(
                ctx.iter_meta,
                **{"dag.status": "done", "dag.durationSecs": secs},
            )
            commit_phase(
                ctx.project_path, iter_num=ctx.iter_num, phase="dag",
                summary=f"elaboration ({secs}s)",
            )

        complete = dag_is_complete(ctx.state_dir)
        return DagPhaseResult(complete=complete)

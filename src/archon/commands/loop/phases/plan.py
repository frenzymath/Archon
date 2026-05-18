"""Plan phase: invoke the plan agent and refresh `current_stage`.

Loop-side automation done here so the prompt doesn't have to ask the
agent to do it:

* **User hints**: read ``USER_HINTS.md`` BEFORE the agent starts,
  inject the content into the prompt via ``captured_user_hints=``,
  and clear the file AFTER the plan phase succeeds. The agent does
  not read or clear that file itself.
* **Blueprint-doctor findings**: the build_plan_prompt builder reads
  the prior iter's doctor JSON and injects findings inline — no
  agent-side file read required.
"""

from __future__ import annotations

import time
from pathlib import Path

from archon import log
from archon.agent import ClaudeAgent
from archon.commands.tooling.iteration import commit_phase
from archon.commands.tooling.project_config import (
    load_project_config,
    resolve_recent_iter_window,
    resolve_subagents_enabled,
)
from archon.prompts import build_plan_prompt
from archon.state import is_complete, read_stage, write_meta
from archon.subagents.audit import check_mandatory_dispatched

from ..resume import PLAN_CONTINUE, persist_session_id, pick_resume_session
from .base import Phase, PhaseResult


def _capture_user_hints(state_dir: Path) -> str | None:
    """Read USER_HINTS.md if present and return its text.

    Returns the raw text (whitespace preserved) when the file exists
    and is readable; returns ``None`` when the file is missing or
    unreadable. The clear step is deferred to
    :func:`_clear_user_hints` so we only clear when the plan phase
    actually consumes the hints — a crashed plan keeps the file
    intact for the retry.
    """
    hints_file = state_dir / "USER_HINTS.md"
    if not hints_file.is_file():
        return None
    try:
        return hints_file.read_text(encoding="utf-8")
    except OSError:
        return None


def _clear_user_hints(state_dir: Path) -> None:
    """Empty USER_HINTS.md after the plan phase consumed its content.

    Idempotent: writes an empty string regardless of prior content.
    """
    hints_file = state_dir / "USER_HINTS.md"
    try:
        hints_file.write_text("", encoding="utf-8")
    except OSError as e:
        log.warn(f"could not clear {hints_file}: {e}")


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
        cfg = load_project_config(ctx.project_path)
        captured_hints = _capture_user_hints(ctx.state_dir)
        plan_prompt = build_plan_prompt(
            ctx.project_name, ctx.project_path, ctx.state_dir, ctx.current_stage,
            ctx.iter_num,
            ignore_multilane=(
                ctx.options.multilane_preview or ctx.options.multilane_execute
            ),
            debug_feedback=ctx.options.debug_feedback,
            recent_iter_window=resolve_recent_iter_window(cfg),
            captured_user_hints=captured_hints,
        )

        if ctx.dry_run:
            log.step("[dry-run] Plan prompt:")
            print(plan_prompt)
        else:
            plan_log = ctx.iter_dir / "plan"
            resume_sid = pick_resume_session(
                ctx.iter_meta, "plan.sessionId",
                enabled=(ctx.resume_phase == self.skip_token),
                label="plan",
                cwd=ctx.project_path,
            )
            ClaudeAgent(model=ctx.model, role="plan").run(
                PLAN_CONTINUE if resume_sid else plan_prompt,
                cwd=ctx.project_path,
                log_base=plan_log, verbose_logs=ctx.verbose_logs,
                env_overrides={"ARCHON_ITER_NUM": f"{ctx.iter_num:03d}"},
                resume_session_id=resume_sid,
            )
            persist_session_id(
                ctx.iter_meta, Path(str(plan_log) + ".jsonl"),
                "plan.sessionId",
            )

        plan_secs = int(time.monotonic() - plan_start)
        log.info(f"Plan phase finished ({plan_secs}s)")
        if not ctx.dry_run:
            # Clear USER_HINTS.md AFTER the plan agent has had a chance
            # to consume the captured content. If the plan crashed
            # before completing this far, the file is left intact so
            # the retry sees the same hints.
            if captured_hints is not None and captured_hints.strip():
                _clear_user_hints(ctx.state_dir)
            check_mandatory_dispatched(
                ctx.project_path, ctx.state_dir, ctx.iter_num,
                phase="plan",
                enabled=resolve_subagents_enabled(cfg),
            )
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

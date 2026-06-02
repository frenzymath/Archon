"""Review phase: extract attempts, run review agent, validate output."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from archon import log
from archon.commands.tooling.iteration import commit_phase
from archon.commands.tooling.project_config import (
    load_project_config,
    resolve_recent_iter_window,
    resolve_subagents_enabled,
)
from archon.prompts import build_review_prompt
from archon.state import write_meta
from archon.subagents.audit import check_mandatory_dispatched

from ..resume import REVIEW_CONTINUE, persist_session_id, pick_resume_session
from ..utils import data_path
from .base import Phase, PhaseResult


class ReviewPhase(Phase):
    name = "Review agent"
    number = 3
    skip_token = "review"

    def run(self) -> PhaseResult:
        ctx = self.ctx

        if self.skip_token in ctx.skip_now:
            log.phase(self.number, f"{self.name} — skipped (--from)")
            return PhaseResult(skipped=True)

        if ctx.options.no_review or ctx.dry_run:
            return PhaseResult(skipped=True)

        log.phase(self.number, self.name)
        review_start = time.monotonic()
        write_meta(ctx.iter_meta, **{"review.status": "running"})

        self._invoke_review()

        review_secs = int(time.monotonic() - review_start)
        log.info(f"Review phase finished ({review_secs}s)")
        if ctx.dashboard_url:
            log.step(f"Journal: {ctx.dashboard_url}/journal")
        cfg = load_project_config(ctx.project_path)
        check_mandatory_dispatched(
            ctx.project_path, ctx.state_dir, ctx.iter_num,
            phase="review",
            enabled=resolve_subagents_enabled(cfg),
        )
        write_meta(ctx.iter_meta, **{
            "review.status": "done",
            "review.durationSecs": review_secs,
        })
        commit_phase(
            ctx.project_path, iter_num=ctx.iter_num, phase="review",
            summary=f"journal session ({review_secs}s)",
        )
        return PhaseResult()

    def _invoke_review(self) -> None:
        ctx = self.ctx
        # Session number == iteration number. The pre-2026 monotonic
        # counter drifted away from iter numbers whenever the user did
        # ``git reset --hard`` past existing review dirs (the dirs are
        # filesystem-only, never tracked by the inner archon git, so a
        # reset would leave them in place while the iter numbers
        # rewound). Aligning the two means session_NNN/ is always
        # exactly the review of iter-NNN.
        session_num = ctx.iter_num
        journal_dir = ctx.state_dir / "proof-journal"
        session_dir = journal_dir / "sessions" / f"session_{session_num}"
        current_session_dir = journal_dir / "current_session"
        attempts_file = current_session_dir / "attempts_raw.jsonl"

        session_dir.mkdir(parents=True, exist_ok=True)
        current_session_dir.mkdir(parents=True, exist_ok=True)

        log.step("Extracting attempt data from prover logs...")
        provers_dir = ctx.iter_dir / "provers"
        # Take only the parsed `.jsonl` files (skip `.raw.jsonl`). The
        # `is_file()` filter drops dangling symlinks — these come from
        # (a) cancelled lanes whose parser was killed before writing
        # the target file, or (b) prior runs on the same iter dir with
        # a different lane set (e.g. kimi was enabled then disabled),
        # leaving stale symlinks pointing at non-existent files.
        parsed_logs = [
            p for p in sorted(provers_dir.glob("*.jsonl"))
            if not p.name.endswith(".raw.jsonl") and p.is_file()
        ] if provers_dir.exists() else []
        if parsed_logs:
            combined = ctx.iter_dir / "provers-combined.jsonl"
            with combined.open("w") as out:
                for jf in parsed_logs:
                    try:
                        out.write(jf.read_text(encoding="utf-8", errors="ignore"))
                    except OSError as e:
                        log.warn(f"skipping unreadable prover log {jf.name}: {e}")
        else:
            combined = ctx.iter_dir / "prover.jsonl"

        # ``current_session/attempts_raw.jsonl`` is reused across iters.
        # When no prover ran this iter (intentional skip, plan-validate
        # failure, etc.), extract-attempts.py either errors out (input
        # missing) or produces nothing, leaving the file with the *prior*
        # iter's attempts — which the review agent then reads as if it
        # were current. Stamp a sentinel so the file is unambiguously
        # this-iter, and skip the extract step entirely.
        if not parsed_logs:
            sentinel = {
                "type": "summary",
                "no_prover_lane": True,
                "iter": ctx.iter_num,
                "reason": (
                    "No prover lane this iter — either an intentional "
                    "skip (see plan-validate marker / iter sidecar) or "
                    "the prover phase produced no parsed logs."
                ),
            }
            attempts_file.write_text(
                json.dumps(sentinel, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        else:
            extract_script = data_path("scripts/extract-attempts.py")
            if extract_script.exists():
                subprocess.run(
                    [sys.executable, str(extract_script),
                     str(combined), str(attempts_file)],
                    capture_output=True,
                )
        
        to_user_file = ctx.state_dir / "TO_USER.md"
        to_user_file.write_text("", encoding="utf-8")

        cfg = load_project_config(ctx.project_path)
        prompt = build_review_prompt(
            ctx.project_name, ctx.project_path, ctx.state_dir, ctx.current_stage,
            session_num, session_dir, attempts_file, combined,
            ctx.iter_num,
            debug_feedback=ctx.options.debug_feedback,
            recent_iter_window=resolve_recent_iter_window(cfg),
        )
        review_log = ctx.iter_dir / "review"
        resume_sid = pick_resume_session(
            ctx.iter_meta, "review.sessionId",
            enabled=(ctx.resume_phase == self.skip_token),
            label="review",
            cwd=ctx.project_path,
            jsonl_fallback=Path(str(review_log) + ".jsonl"),
        )
        ctx.make_agent("review").run(
            REVIEW_CONTINUE if resume_sid else prompt,
            cwd=ctx.project_path,
            log_base=review_log, verbose_logs=ctx.verbose_logs,
            resume_session_id=resume_sid,
        )
        persist_session_id(
            ctx.iter_meta, Path(str(review_log) + ".jsonl"),
            "review.sessionId",
        )

        validate_script = data_path("scripts/validate-review.py")
        if validate_script.exists():
            subprocess.run(
                [sys.executable, str(validate_script), str(session_dir), str(attempts_file)],
                capture_output=True,
            )

        # If the agent crashed before writing anything, drop the empty
        # session_NNN/ rather than leaving a stub the UI will surface.
        if session_dir.is_dir():
            try:
                content = list(session_dir.iterdir())
            except OSError:
                content = []
            if not content or all(
                p.is_file() and p.stat().st_size == 0 for p in content
            ):
                for p in content:
                    if p.is_file():
                        p.unlink()
                try:
                    session_dir.rmdir()
                    log.warn(
                        f"Removed empty session_{session_num}/ — review "
                        f"agent produced no output"
                    )
                except OSError:
                    pass

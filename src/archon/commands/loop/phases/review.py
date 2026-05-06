"""Review phase: extract attempts, run review agent, validate output."""

from __future__ import annotations

import subprocess
import sys
import time

from archon import log
from archon.agent import ClaudeAgent
from archon.commands.tooling.iteration import commit_phase
from archon.prompts import build_review_prompt
from archon.state import next_session_num, write_meta

from ..utils import data_path
from .base import Phase, PhaseResult


class ReviewPhase(Phase):
    name = "Review agent"
    number = 4
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
        session_num = next_session_num(ctx.state_dir)
        journal_dir = ctx.state_dir / "proof-journal"
        session_dir = journal_dir / "sessions" / f"session_{session_num}"
        current_session_dir = journal_dir / "current_session"
        attempts_file = current_session_dir / "attempts_raw.jsonl"

        session_dir.mkdir(parents=True, exist_ok=True)
        current_session_dir.mkdir(parents=True, exist_ok=True)

        log.step("Extracting attempt data from prover logs...")
        provers_dir = ctx.iter_dir / "provers"
        # Take only the parsed `.jsonl` files (skip `.raw.jsonl`). Cancelled
        # lanes can leave dangling symlinks if the parser was killed before
        # writing — `Path.exists()` returns False for those, so we skip
        # them rather than crash the whole loop on read_text().
        parsed_logs = [
            p for p in sorted(provers_dir.glob("*.jsonl"))
            if not p.name.endswith(".raw.jsonl") and p.exists()
        ] if provers_dir.exists() else []
        if parsed_logs:
            combined = ctx.iter_dir / "provers-combined.jsonl"
            with combined.open("w") as out:
                for jf in parsed_logs:
                    try:
                        out.write(jf.read_text())
                    except OSError as e:
                        log.warn(f"skipping unreadable prover log {jf.name}: {e}")
        else:
            combined = ctx.iter_dir / "prover.jsonl"

        extract_script = data_path("scripts/extract-attempts.py")
        if extract_script.exists():
            subprocess.run(
                [sys.executable, str(extract_script), str(combined), str(attempts_file)],
                capture_output=True,
            )

        prompt = build_review_prompt(
            ctx.project_name, ctx.project_path, ctx.state_dir, ctx.current_stage,
            session_num, session_dir, attempts_file, combined,
            ctx.iter_num,
        )
        review_log = ctx.iter_dir / "review"
        ClaudeAgent(model=ctx.model, role="review").run(
            prompt, cwd=ctx.project_path,
            log_base=review_log, verbose_logs=ctx.verbose_logs,
        )

        validate_script = data_path("scripts/validate-review.py")
        if validate_script.exists():
            subprocess.run(
                [sys.executable, str(validate_script), str(session_dir), str(attempts_file)],
                capture_output=True,
            )

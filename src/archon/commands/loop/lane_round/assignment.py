"""Run a single lane assignment to completion and classify the result.

Used by `LaneRoundExecutor` — one `LaneAssignmentRunner` per
(lane, assigned_file) pair. Each runner spawns its own Claude
subprocess in the lane's worktree, watches for cancellation, and
returns a result row that the executor aggregates into the round JSONL.
"""

from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path

from archon import log
from archon.agent import DEFAULT_HARNESS, build_runner
from archon.multilane.dispatch import build_assignment_prompt
from archon.state import utcnow_iso

from ..prover.environment import prover_env_dict, snapshot_baseline
from ..utils import file_slug
from .helpers import (
    assignment_code_snapshot_files,
    assignment_early_stop_worthy,
    assignment_success,
    git_diff_files,
)


class LaneAssignmentRunner:
    """Runs one (lane, file) assignment and returns its result row."""

    def __init__(
        self,
        *,
        project_name: str,
        project_path: Path,
        state_dir: Path,
        stage: str,
        assignment,
        iter_num: int,
        verbose_logs: bool,
        model: str,
        iter_dir: Path | None = None,
        lane_provider: str | None = None,
        lane_env: dict[str, str] | None = None,
        cancel_event: threading.Event | None = None,
        harness: str = DEFAULT_HARNESS,
    ) -> None:
        self.project_name = project_name
        self.project_path = project_path
        self.state_dir = state_dir
        self.stage = stage
        self.assignment = assignment
        self.iter_num = iter_num
        self.verbose_logs = verbose_logs
        self.model = model
        self.iter_dir = iter_dir
        self.lane_provider = lane_provider
        self.lane_env = lane_env
        self.cancel_event = cancel_event
        # Forward-compat: in Phase 1 the lane axis always builds a
        # claude-code runner via the factory. The lane's own ``harness``
        # field (LaneConfig.harness) is parsed but not yet rewired into
        # dispatch; this defaults to "claude-code" so behaviour is
        # unchanged.
        self.harness = harness

        self.lane_path = Path(assignment.worktree_path)
        self.slug = file_slug(assignment.assigned_file)
        self.snap_dir = (
            Path(assignment.log_path).parent.parent / 'snapshots' / self.slug
        )
        self.raw_log_path = str(Path(str(assignment.log_path) + '.jsonl'))
        self.target_file = self.lane_path / assignment.assigned_file
        self.log_jsonl = Path(f"{assignment.log_path}.jsonl")

    def run(self) -> dict[str, object]:
        if self.target_file.exists():
            snapshot_baseline(self.target_file, self.snap_dir)

        self._announce_start()
        self._link_log_into_iter_dir()
        before_files = set(git_diff_files(self.lane_path))
        ok = self._invoke_claude()
        self._status('lane_assignment_finished', lane_id=self.assignment.lane_id, ok=bool(ok))

        cancelled = bool(self.cancel_event is not None and self.cancel_event.is_set())
        if cancelled:
            self._write_cancelled_summary()

        return self._build_result(ok, before_files, cancelled=cancelled)

    # ── private ─────────────────────────────────────────────────────────

    def _status(self, event: str, **fields: object) -> None:
        """Append a JSONL status row to the lane's log.

        Best-effort: if the disk is full or the path isn't writable, the
        run continues with a stderr warning rather than dying.
        """
        try:
            self.log_jsonl.parent.mkdir(parents=True, exist_ok=True)
            row = {'ts': utcnow_iso(), 'event': event, **fields}
            with open(self.log_jsonl, 'a', encoding='utf-8') as f:
                f.write(json.dumps(row) + '\n')
        except OSError:
            log.warn(f"could not append to {self.log_jsonl}")

    def _announce_start(self) -> None:
        # Always write a status row to the lane's JSONL BEFORE the agent
        # is invoked. Two reasons:
        #   1. If Claude itself crashes at startup (bad endpoint, missing
        #      auth, segfault, ...), the JSONL would otherwise stay empty
        #      and the dashboard / user has no idea what went wrong.
        #   2. Live tools that tail the JSONL get an immediate "the lane
        #      started, here are its assignment params" entry instead of
        #      a several-second silence.
        self._status(
            'lane_assignment_started',
            lane_id=self.assignment.lane_id,
            provider=self.lane_provider,
            assignment_id=self.assignment.assignment_id,
            assigned_file=self.assignment.assigned_file,
            worktree_path=str(self.lane_path),
            model=self.model,
        )
        log.step(
            f"  lane '{self.assignment.lane_id}': starting on "
            f"{self.assignment.assigned_file} (log: {self.log_jsonl})"
        )

    def _link_log_into_iter_dir(self) -> None:
        """Symlink the lane's log under iter_dir/provers/ for the dashboard.

        Tail-based live UI keeps working because we symlink rather than
        copy. On filesystems without symlink support we silently skip —
        the log still exists at its primary path under multilane/lanes/.
        """
        if self.iter_dir is None:
            return
        provers_dir = self.iter_dir / "provers"
        provers_dir.mkdir(parents=True, exist_ok=True)
        for ext in ("jsonl", "raw.jsonl"):
            target = Path(f"{self.assignment.log_path}.{ext}")
            link = provers_dir / f"{self.slug}__{self.assignment.lane_id}.{ext}"
            try:
                if link.exists() or link.is_symlink():
                    link.unlink()
                link.symlink_to(target)
            except OSError:
                pass

    def _invoke_claude(self) -> bool:
        prompt = build_assignment_prompt(
            project_name=self.project_name,
            lane_project_path=self.lane_path,
            state_dir=self.state_dir,
            stage=self.stage,
            assignment=self.assignment,
            iter_num=self.iter_num,
        )
        # Tag the role with lane + provider so the user can tell which
        # session is hitting which API. The model alias is the global
        # --model; the lane's settings.local.json maps that alias to the
        # provider's actual endpoint via ANTHROPIC_BASE_URL +
        # ANTHROPIC_MODEL.
        role_tag = f"prover[{self.assignment.lane_id}"
        if self.lane_provider:
            role_tag += f"/{self.lane_provider}"
        role_tag += "]"

        # Build env: prover-runtime vars first, then lane provider vars
        # on top so e.g. an empty ``ANTHROPIC_API_KEY`` from the lane
        # settings overrides anything inherited from the parent shell.
        run_env = prover_env_dict(
            snap_dir=self.snap_dir,
            prover_jsonl=Path(self.raw_log_path),
            project_path=self.lane_path,
        )
        if self.lane_env:
            run_env = {**run_env, **self.lane_env}

        try:
            return build_runner(
                role=role_tag, model=self.model, harness=self.harness,
            ).run(
                prompt,
                cwd=self.lane_path,
                log_base=Path(self.assignment.log_path),
                verbose_logs=self.verbose_logs,
                env_overrides=run_env,
                cancel_event=self.cancel_event,
            )
        except Exception as e:  # noqa: BLE001 — record and re-raise
            self._status(
                'lane_assignment_exception',
                lane_id=self.assignment.lane_id,
                error=str(e),
                traceback=traceback.format_exc(),
            )
            raise

    def _build_result(
        self, ok: bool, before_files: set[str], *, cancelled: bool = False,
    ) -> dict[str, object]:
        after_files = set(git_diff_files(self.lane_path))
        lane_dirty_files = sorted(after_files - before_files)
        changed_files, escaped_files, attribution_source = assignment_code_snapshot_files(
            Path(self.raw_log_path), self.lane_path, self.project_path,
        )

        summary_path = (
            self.assignment.result_path
            if Path(self.assignment.result_path).exists() else None
        )
        assigned_file_only = (
            bool(changed_files)
            and set(changed_files) == {self.assignment.assigned_file}
        )
        # The baseline snapshot was written by run() before the prover
        # started; passing it lets `assignment_success` measure progress
        # by comparing sorry counts before vs after rather than insisting
        # on a sorry-free file. The early-stop trigger (below) keeps the
        # strict 'sorry-free' check separately so partial wins merge but
        # don't kill slower lanes.
        baseline_path = self.snap_dir / "baseline.lean"
        strict_success, failure_reason = assignment_success(
            ok=ok,
            assigned_file=self.assignment.assigned_file,
            changed_files=changed_files,
            escaped_files=escaped_files,
            summary_path=summary_path,
            assigned_file_path=str(self.target_file),
            baseline_file_path=str(baseline_path) if baseline_path.exists() else None,
        )
        # When the catch-all "runner_failed" fires, peek at the lane's
        # session_end summary to refine the diagnosis — rate limits,
        # auth failures, context overflows, network blips all deserve
        # their own label so the user can act on them.
        if not strict_success and failure_reason == 'runner_failed':
            from archon.session_log import (
                read_last_session_end,
                session_end_failure_kind,
            )
            kind = session_end_failure_kind(read_last_session_end(self.raw_log_path))
            if kind is not None:
                failure_reason = kind
        # Cancellation is its own category — keep it distinct from
        # provider failures so the dashboard / review can tell that the
        # lane was preempted, not that it crashed.
        if cancelled and not strict_success:
            failure_reason = 'cancelled_by_winner'

        early_stop_worthy = assignment_early_stop_worthy(
            success=strict_success,
            assigned_file_path=str(self.target_file),
        )

        return {
            'assignment_id': self.assignment.assignment_id,
            'lane_id': self.assignment.lane_id,
            'job_id': self.assignment.job_id,
            'assigned_file': self.assignment.assigned_file,
            'worktree_path': self.assignment.worktree_path,
            'success': strict_success,
            'early_stop_worthy': early_stop_worthy,
            'cancelled': cancelled,
            'failure_reason': failure_reason,
            'changed_files': changed_files,
            'escaped_files': escaped_files,
            'attribution_source': attribution_source,
            'lane_dirty_files': lane_dirty_files,
            'assigned_file_only': assigned_file_only,
            'verification_passed': ok,
            'summary_path': summary_path,
            'raw_log_path': self.raw_log_path,
            'promote_readiness': 'manual-only',
        }

    def _write_cancelled_summary(self) -> None:
        """Auto-write a `task_results/<file>.md` for a cancelled lane.

        Without this, a lane that was preempted mid-flight leaves no
        summary, so the merger and review agent can't see what it
        attempted (its work sits only as raw JSONL events). Generates a
        terse markdown digest from the JSONL: edit count, last tool
        call, last text excerpt. If the prover already wrote its own
        summary before SIGTERM, leave that file alone.
        """
        result_path = Path(self.assignment.result_path)
        if result_path.exists():
            return
        edits = 0
        last_tool = ''
        last_text = ''
        last_edited_file = ''
        try:
            for raw_line in self.log_jsonl.read_text(
                encoding='utf-8', errors='ignore',
            ).splitlines():
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    evt = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                event = evt.get('event')
                if event == 'tool_call' and evt.get('tool') in {'Edit', 'Write'}:
                    edits += 1
                    last_tool = str(evt.get('tool') or '')
                    fp = (evt.get('input') or {}).get('file_path')
                    if isinstance(fp, str):
                        last_edited_file = fp
                elif event == 'text':
                    content = evt.get('content') or ''
                    if isinstance(content, str) and content.strip():
                        last_text = content.strip()[-400:]
        except OSError:
            pass

        body = (
            f"# {self.assignment.assigned_file}\n\n"
            f"**Cancelled mid-flight by Archon multilane** "
            f"(lane `{self.assignment.lane_id}`; another lane finished "
            f"this file cleanly first and the grace timer expired).\n\n"
            f"## Auto-generated digest of work before cancellation\n\n"
            f"- Edits/Writes attempted: **{edits}**\n"
            f"- Last tool: `{last_tool or 'n/a'}`\n"
            f"- Last edited file: `{last_edited_file or 'n/a'}`\n"
            f"- Last text excerpt:\n\n"
            f"  > {last_text or '(no text emitted)'}\n\n"
            f"This summary was generated automatically because the "
            f"prover was preempted before it could write its own "
            f"task_results entry. The raw JSONL log is at "
            f"`{self.log_jsonl}`.\n"
        )
        try:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(body, encoding='utf-8')
        except OSError as e:
            log.warn(f"could not write cancelled-lane summary to {result_path}: {e}")

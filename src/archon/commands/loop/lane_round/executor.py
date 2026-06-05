"""Run the multi-lane assignment round and merge/promote the winners.

`LaneRoundExecutor` drives one iteration's worth of lane work:
preview, dispatch, early-stop on first-clean-win, contamination check,
per-file merge, summary report. The actual lane subprocess work happens
inside `LaneAssignmentRunner`; this class just orchestrates the fanout.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import typer

from archon import log
from archon.multilane.collect import write_results_jsonl
from archon.multilane.config import (
    MultiLaneConfig,
    find_multilane_config,
    find_multilane_local_config,
    load_multilane_config,
)
from archon.multilane.dispatch import (
    prepare_lanes_for_preview,
    preview_round,
    write_preview_runtime_artifacts,
)

from archon.agent import ClaudeBackend

from .assignment import LaneAssignmentRunner
from .helpers import non_archon_dirty_files, restore_repo_paths
from .lock import MultiLaneLock
from .settings import autogen_lane_settings
from .writeback import (
    WritebackPromoter,
    group_writeback_candidates_by_file,
    preferred_writeback_lane,
)


_DEFAULT_GRACE_SECONDS = 600.0  # 10 min — overridden by `multilane.grace_minutes` in config.json


class LaneRoundExecutor:
    """Orchestrates one iteration's multi-lane round."""

    def __init__(
        self,
        *,
        project_name: str,
        project_path: Path,
        state_dir: Path,
        progress_file: Path,
        stage: str,
        iteration: int,
        verbose_logs: bool,
        model: str,
        max_parallel: int = 4,
        config: MultiLaneConfig | None = None,
        backend: ClaudeBackend | None = None,
    ) -> None:
        self.project_name = project_name
        self.project_path = project_path
        self.state_dir = state_dir
        self.progress_file = progress_file
        self.stage = stage
        self.iteration = iteration
        self.verbose_logs = verbose_logs
        self.model = model
        self.max_parallel = max_parallel
        self.config = config
        self.backend = backend or ClaudeBackend()

        # Filled during run().
        self._assignments: list = []
        self._results: list[dict[str, object]] = []
        self._failures: int = 0
        self._restored_main_paths: set[str] = set()
        self._grace_timers: list[threading.Timer] = []
        self._cancel_events: dict[str, threading.Event] = {}
        self._results_path: Path | None = None
        self._summary: dict[str, object] = {}

    def run(self) -> dict[str, object] | None:
        config = self._resolve_config()
        if config is None:
            return None

        self._assert_clean_baseline()
        if not config.enabled:
            log.warn('Multi-lane config exists but is disabled')
            return None

        # For lanes whose provider needs external API credentials (kimi,
        # deepseek, …), auto-generate the {ANTHROPIC_BASE_URL, …}-shaped
        # settings file from the project .env.
        config = autogen_lane_settings(self.state_dir, config)
        self.config = config

        summary, assignments = preview_round(
            config=config,
            progress_file=self.progress_file,
            project_path=self.project_path,
            state_dir=self.state_dir,
            iteration=self.iteration,
            stage=self.stage,
        )
        prepared = prepare_lanes_for_preview(
            config=config, project_path=self.project_path,
        )
        runtime_info = write_preview_runtime_artifacts(
            state_dir=self.state_dir,
            iteration=self.iteration,
            assignments=assignments,
            prepared=prepared,
        )

        self._summary = summary
        self._assignments = list(assignments)
        self._results_path = (
            self.state_dir / 'multilane' / 'runtime'
            / f'iter-{self.iteration:03d}-results.jsonl'
        )

        with MultiLaneLock(self.state_dir):
            self._dispatch_assignments(config)

        self._check_contamination()
        promotion_info = self._promote(config)

        self._results.sort(key=lambda row: str(row.get('assignment_id', '')))
        write_results_jsonl(self._results_path, self._results)

        report_path = self._write_report(promotion_info)
        self._panel_summary(report_path)

        return {
            'summary': summary,
            'prepared': prepared,
            'assignments': self._assignments,
            'results': self._results,
            'results_path': str(self._results_path),
            'report_path': str(report_path),
            'promoted_files': promotion_info.get('promoted_files', []),
            'promotion_commit': promotion_info.get('promotion_commit'),
            **runtime_info,
        }

    # ── private ─────────────────────────────────────────────────────────

    def _resolve_config(self) -> MultiLaneConfig | None:
        """Use the caller-supplied config if present (the new path: typed
        off `.archon/config.json`'s ``multilane`` section); otherwise
        fall back to file-based discovery for backwards-compat.
        """
        if self.config is not None:
            return self.config
        config_path = find_multilane_config(self.state_dir)
        if config_path is None:
            log.warn(
                'Multi-lane execution requested but no config provided '
                'and no .archon/multilane/config.* found',
            )
            return None
        local_path = find_multilane_local_config(self.state_dir)
        return load_multilane_config(config_path, local_path)

    def _assert_clean_baseline(self) -> None:
        dirty = non_archon_dirty_files(self.project_path)
        if dirty:
            log.error(
                'Multi-lane execute requires a clean main project tree (outside .archon).',
            )
            for path in dirty:
                log.error(f'  dirty: {path}')
            raise typer.Exit(1)

    def _dispatch_assignments(self, config: MultiLaneConfig) -> None:
        assignment_count = len(self._assignments)
        if assignment_count == 0:
            write_results_jsonl(self._results_path, self._results)
            return

        # Each lane gets up to ``max_parallel`` concurrent files —
        # mirrors non-multilane parallel-prover semantics. The remaining
        # (lane, file) jobs queue inside the executor and start as
        # workers free up. This is what makes early-stop useful with
        # many files: when a slow lane is cancelled on file F, its
        # worker frees up and pulls the next file off the queue
        # automatically.
        num_lanes = max(1, len(config.enabled_lanes()))
        workers = max(1, min(assignment_count, self.max_parallel * num_lanes))
        log.info(
            f'Launching {assignment_count} multi-lane assignment(s) '
            f'({workers} concurrent slots = {self.max_parallel} per lane × {num_lanes} lanes)'
        )
        iter_dir = self.state_dir / 'logs' / f'iter-{self.iteration:03d}'
        lane_providers = {lane.lane_id: lane.provider for lane in config.lanes}
        lane_harnesses = self._build_lane_harnesses(config)
        lane_envs = self._build_lane_envs(config)

        # Per-(lane, file) cancel event. When one lane finishes a file
        # cleanly, we set the cancel events of all OTHER lanes working
        # on that same file after a grace period — that's the "first
        # clean lane wins, rest get N minutes to wrap" semantics. The
        # actual termination happens inside ClaudeAgent.run, which polls
        # these events.
        self._cancel_events = {
            a.assignment_id: threading.Event() for a in self._assignments
        }

        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        self._run_one,
                        assignment, iter_dir,
                        lane_providers.get(assignment.lane_id),
                        lane_envs.get(assignment.lane_id),
                        lane_harnesses.get(assignment.lane_id),
                    ): assignment
                    for assignment in self._assignments
                }
                for future in as_completed(futures):
                    assignment = futures[future]
                    self._handle_completion(future, assignment)
        finally:
            # Cancel any timers that haven't fired yet — once we leave
            # the executor, the cancel_events would be setting nothing
            # useful (assignments have all completed or been killed).
            for t in self._grace_timers:
                try:
                    t.cancel()
                except Exception:
                    pass

    def _build_lane_envs(self, config: MultiLaneConfig) -> dict[str, dict[str, str]]:
        """Pre-load each lane's env-vars dict.

        The same dict was already written to the lane's
        ``.claude/settings.local.json``, but Claude Code's env-merge
        semantics across versions are inconsistent — empty values may
        get dropped, leaving an inherited ``ANTHROPIC_API_KEY`` from
        the parent shell to override the proxy redirect. Setting them
        on the subprocess directly is unambiguous.
        """
        lane_envs: dict[str, dict[str, str]] = {}
        for lane in config.lanes:
            if not lane.claude_settings_path:
                continue
            try:
                raw = Path(lane.claude_settings_path).read_text(encoding='utf-8')
                lane_envs[lane.lane_id] = json.loads(raw).get('env', {}) or {}
            except (OSError, json.JSONDecodeError):
                lane_envs[lane.lane_id] = {}
        return lane_envs

    def _build_lane_harnesses(self, config: MultiLaneConfig):
        """Resolve each lane's ``harness`` name to a full descriptor.

        ``LaneConfig.harness`` is just a name (default ``"claude-code"``).
        Resolving it here — at the dispatch site, where the project config
        is loadable — lets the descriptor (model / backend) travel with the
        lane. Returns ``{lane_id: HarnessDescriptor}``.

        Phase-1 guard (fail-closed): the lane axis only supports the
        ``"claude-code"`` runner today. A lane whose resolved harness names
        a non-claude runner (e.g. ``codex``) is rejected *here*, before any
        lane subprocess spawns — because lane result-attribution
        (``assignment_code_snapshot_files`` → ``assignment_success``) only
        recognizes Claude Code's ``code_snapshot`` hook events and
        ``Edit``/``Write`` tool_calls. Codex writes neither (it applies
        edits via apply_patch / ``file_change`` items in its raw stream),
        so a successful codex lane edit would silently fail to promote and
        never trigger early-stop. Rather than downgrade silently, we raise.
        To run codex as the prover, route it via ``loop.roles.prover``
        (the single-lane prover path supports it).
        """
        from archon.agent import UnknownHarnessError
        from archon.commands.tooling.project_config import (
            DEFAULT_HARNESS,
            load_harness_descriptor,
            load_project_config,
        )

        cfg = load_project_config(self.project_path)
        harnesses = {}
        for lane in config.lanes:
            descriptor = load_harness_descriptor(cfg, lane.harness)
            if descriptor.runner != DEFAULT_HARNESS:
                raise UnknownHarnessError(
                    f"Lane {lane.lane_id!r} requests harness "
                    f"{descriptor.name!r} (runner {descriptor.runner!r}), but "
                    f"the multilane lane axis only supports the "
                    f"{DEFAULT_HARNESS!r} runner today. Lane-level result "
                    f"attribution tracks only Claude Code's code_snapshot "
                    f"hook events and Edit/Write tool_calls, so a "
                    f"{descriptor.runner!r} lane's edits would silently fail "
                    f"to promote and never trigger early-stop. To run codex "
                    f"as the prover, route it via loop.roles.prover instead "
                    f"(the single-lane prover path supports it), or keep "
                    f"this lane on {DEFAULT_HARNESS!r}."
                )
            harnesses[lane.lane_id] = descriptor
        return harnesses

    def _run_one(
        self, assignment, iter_dir: Path,
        lane_provider: str | None, lane_env: dict[str, str] | None,
        lane_harness=None,
    ) -> dict[str, object]:
        runner = LaneAssignmentRunner(
            project_name=self.project_name,
            project_path=self.project_path,
            state_dir=self.state_dir,
            stage=self.stage,
            assignment=assignment,
            iter_num=self.iteration,
            verbose_logs=self.verbose_logs,
            model=self.model,
            iter_dir=iter_dir,
            lane_provider=lane_provider,
            lane_env=lane_env,
            cancel_event=self._cancel_events[assignment.assignment_id],
            backend=self.backend,
            harness=lane_harness,
        )
        return runner.run()

    def _handle_completion(self, future, assignment) -> None:
        try:
            row = future.result()
        except Exception as exc:
            self._failures += 1
            row = {
                'assignment_id': assignment.assignment_id,
                'lane_id': assignment.lane_id,
                'job_id': assignment.job_id,
                'assigned_file': assignment.assigned_file,
                'worktree_path': assignment.worktree_path,
                'success': False,
                'failure_reason': 'exception',
                'changed_files': [],
                'assigned_file_only': False,
                'verification_passed': False,
                'summary_path': None,
                'raw_log_path': str(Path(str(assignment.log_path) + '.jsonl')),
                'promote_readiness': 'manual-only',
                'error': str(exc),
            }
            log.warn(
                f"  Lane {assignment.lane_id} :: {assignment.assigned_file} "
                f"-> exception: {exc}"
            )
        else:
            escaped_files = [str(path) for path in row.get('escaped_files', [])]
            if escaped_files:
                restore_repo_paths(self.project_path, escaped_files)
                self._restored_main_paths.update(escaped_files)
            if not row['success']:
                self._failures += 1
            status = 'ok' if row['success'] else f"error ({row.get('failure_reason')})"
            log.info(
                f"  Lane {assignment.lane_id} :: {assignment.assigned_file} -> {status}"
            )
            # Early-stop trigger: only a *fully-clearing* win starts the
            # grace timer for other lanes on the same file. A merge-
            # worthy partial win (sorry count decreased but not zero)
            # still goes to the merger but does NOT preempt slower lanes
            # — they might land their own version that fills different
            # sorries the partial win didn't touch.
            if (row.get('early_stop_worthy')
                    and row.get('assigned_file_only')
                    and row.get('verification_passed')):
                self._schedule_kill_on_file(assignment)
        self._results.append(row)
        write_results_jsonl(self._results_path, self._results)

    def _grace_seconds(self) -> float:
        """Resolve the grace period (s) from the multilane config, with
        a sane default if config is missing or malformed."""
        if self.config is not None and self.config.grace_minutes >= 0:
            return float(self.config.grace_minutes) * 60.0
        return _DEFAULT_GRACE_SECONDS

    def _schedule_kill_on_file(self, winning_assignment) -> None:
        """Set cancel events for OTHER assignments on the same file
        after the configured grace period. Idempotent — multiple
        winners on the same file just refresh / extend nothing (we only
        set the first timer).
        """
        file_key = winning_assignment.assigned_file
        if any(getattr(t, 'file_key', None) == file_key for t in self._grace_timers):
            return  # already scheduled
        victims = [
            a for a in self._assignments
            if a.assigned_file == file_key
            and a.assignment_id != winning_assignment.assignment_id
        ]
        if not victims:
            return
        grace = self._grace_seconds()
        if grace <= 0:
            log.info(
                f"  lane '{winning_assignment.lane_id}' won {file_key} cleanly — "
                f"cancelling other lane(s) immediately (grace=0)"
            )
            for v in victims:
                ev = self._cancel_events.get(v.assignment_id)
                if ev is not None and not ev.is_set():
                    ev.set()
            return
        log.info(
            f"  lane '{winning_assignment.lane_id}' won {file_key} cleanly — "
            f"giving other lane(s) {grace/60:.1f} min to wrap up"
        )

        def _kill():
            for v in victims:
                ev = self._cancel_events.get(v.assignment_id)
                if ev is not None and not ev.is_set():
                    log.info(
                        f"  grace expired — cancelling lane '{v.lane_id}' on {file_key}"
                    )
                    ev.set()

        t = threading.Timer(grace, _kill)
        t.file_key = file_key  # type: ignore[attr-defined]
        t.daemon = True
        t.start()
        self._grace_timers.append(t)

    def _check_contamination(self) -> None:
        contamination = non_archon_dirty_files(self.project_path)
        self._contamination = contamination
        if contamination:
            self._failures += 1
            log.warn(
                'Main project tree was modified during multi-lane execution; '
                'marking run contaminated.',
            )
            for path in contamination:
                log.warn(f'  contaminated: {path}')

    def _promote(self, config: MultiLaneConfig) -> dict[str, object]:
        promotion_info: dict[str, object] = {
            'promoted_files': [],
            'promotion_commit': None,
            'preferred_lane_id': None,
            'merges': [],
        }
        if self._contamination:
            return promotion_info

        # Preferred-lane id is still recorded for the report, but the
        # merge agent now considers every successful lane per file.
        promotion_info['preferred_lane_id'] = preferred_writeback_lane(config)
        groups = group_writeback_candidates_by_file(self._results)
        if not groups:
            return promotion_info

        promoter = WritebackPromoter(
            project_path=self.project_path,
            state_dir=self.state_dir,
            iteration=self.iteration,
            model=self.model,
            verbose_logs=self.verbose_logs,
            backend=self.backend,
        )
        promotion_info.update(promoter.merge_and_promote(groups))
        return promotion_info

    def _write_report(self, promotion_info: dict[str, object]) -> Path:
        report_path = (
            self.state_dir / 'multilane' / 'reports'
            / f'iter-{self.iteration:03d}-execution.md'
        )
        lines = [
            '# Multi-lane execution summary',
            '',
            f"- lanes: {self._summary.get('lane_count', 0)}",
            f"- jobs: {self._summary.get('job_count', 0)}",
            f"- assignments: {self._summary.get('assignment_count', 0)}",
            f"- failures: {self._failures}",
        ]
        if promotion_info.get('promoted_files'):
            lines.extend([
                '', '## Promoted to main',
                *[f'- {path}' for path in promotion_info['promoted_files']],
            ])
            if promotion_info.get('promotion_commit'):
                lines.append(f"- commit: {promotion_info['promotion_commit']}")
        merges = promotion_info.get('merges') or []
        if merges:
            lines.extend(['', '## Per-file merges'])
            for entry in merges:
                shape = (
                    'merged' if entry.get('merged')
                    else f"copied from lane={entry.get('chosen_lane')}"
                )
                lanes = ', '.join(entry.get('lanes') or [])
                lines.append(f"- {entry['file']}: {shape} (lanes: {lanes})")
        if self._restored_main_paths:
            lines.extend([
                '', '## Escaped main-checkout edits reverted to HEAD',
                *[f'- {path}' for path in sorted(self._restored_main_paths)],
            ])
        if self._contamination:
            lines.extend([
                '', '## Main-tree contamination',
                *[f'- {path}' for path in self._contamination],
            ])
        lines.extend(['', '## Results'])
        for row in self._results:
            lines.append(
                f"- {row['lane_id']} :: {row['job_id']} :: success={row['success']} "
                f"reason={row.get('failure_reason')} changed={row['changed_files']}"
            )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return report_path

    def _panel_summary(self, report_path: Path) -> None:
        log.panel(
            f"Executed multi-lane prover round.\n"
            f"Lanes: {self._summary.get('lane_count', 0)}\n"
            f"Jobs: {self._summary.get('job_count', 0)}\n"
            f"Assignments: {self._summary.get('assignment_count', 0)}\n"
            f"Failures: {self._failures}\n"
            f"Report: {report_path}",
            title='Multi-lane execution',
            style='green' if self._failures == 0 else 'yellow',
        )

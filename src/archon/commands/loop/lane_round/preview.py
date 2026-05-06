"""Preview-only multi-lane round.

Runs the planning + prep stages (writes assignments JSONL, prepared
worktrees, preview report) but does NOT spawn lane provers. Used by the
legacy `multilane_preview` flag — kept around because the orchestrator
still dispatches to it via config, even though the new path always uses
the full executor.
"""

from __future__ import annotations

from pathlib import Path

from archon import log
from archon.multilane.collect import write_preview_report, write_results_jsonl
from archon.multilane.config import (
    find_multilane_config,
    find_multilane_local_config,
    load_multilane_config,
)
from archon.multilane.dispatch import (
    execute_assignments_preview_only,
    prepare_lanes_for_preview,
    preview_round,
    write_preview_runtime_artifacts,
)


class LaneRoundPreviewRunner:
    """Generate the lane round's preview artifacts without launching provers."""

    def __init__(
        self,
        *,
        project_path: Path,
        state_dir: Path,
        progress_file: Path,
        stage: str,
        iteration: int,
    ) -> None:
        self.project_path = project_path
        self.state_dir = state_dir
        self.progress_file = progress_file
        self.stage = stage
        self.iteration = iteration

    def run(self) -> dict[str, object] | None:
        config_path = find_multilane_config(self.state_dir)
        if config_path is None:
            log.warn(
                'Multi-lane preview requested but no .archon/multilane/config.* was found',
            )
            return None

        local_path = find_multilane_local_config(self.state_dir)
        config = load_multilane_config(config_path, local_path)
        if not config.enabled:
            log.warn('Multi-lane config exists but is disabled')
            return None

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

        report_path = (
            self.state_dir / 'multilane' / 'reports'
            / f'iter-{self.iteration:03d}-preview.md'
        )
        write_preview_report(
            report_path=report_path, summary=summary, prepared=prepared,
        )
        results = execute_assignments_preview_only(assignments)
        results_path = (
            self.state_dir / 'multilane' / 'runtime'
            / f'iter-{self.iteration:03d}-results.jsonl'
        )
        write_results_jsonl(results_path, results)

        log.panel(
            f"Preview only — no prover launched.\n"
            f"Lanes: {summary.get('lane_count', 0)}\n"
            f"Jobs: {summary.get('job_count', 0)}\n"
            f"Assignments: {summary.get('assignment_count', 0)}\n"
            f"Report: {report_path}",
            title='Multi-lane preview',
            style='cyan',
        )
        return {
            'summary': summary,
            'prepared': prepared,
            'assignments': assignments,
            'results_path': str(results_path),
            'report_path': str(report_path),
            **runtime_info,
        }

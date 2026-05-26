"""Dispatch helpers for future multi-lane prover rounds."""

from __future__ import annotations

import shutil
from pathlib import Path
from textwrap import dedent

from archon.state import normalize_stage_for_prompt_path, parse_objective_files

from .collect import summarize_assignments, write_runtime_jsonl
from .config import resolve_runtime_paths
from .types import LaneAssignment, LaneResult, MultiLaneConfig, ProverJob
from .worktree import lane_worktree_path, prepare_lane


def _prepare_assignment_state_view(source_state_dir: Path, lane_iter_dir: Path) -> Path:
    state_view_dir = lane_iter_dir / 'state'
    if state_view_dir.exists():
        shutil.rmtree(state_view_dir)
    state_view_dir.mkdir(parents=True, exist_ok=True)

    for name in ('CLAUDE.md', 'PROGRESS.md', 'USER_HINTS.md'):
        src = source_state_dir / name
        if src.exists():
            shutil.copy2(src, state_view_dir / name)

    prompts_src = source_state_dir / 'prompts'
    if prompts_src.exists():
        shutil.copytree(prompts_src, state_view_dir / 'prompts', dirs_exist_ok=True)

    return state_view_dir


def build_jobs_from_progress(
    *,
    progress_file: Path,
    project_path: Path,
    iteration: int,
    stage: str,
) -> list[ProverJob]:
    jobs: list[ProverJob] = []
    for idx, file_path in enumerate(parse_objective_files(progress_file, project_path), start=1):
        jobs.append(
            ProverJob(
                job_id=f'job-{iteration:03d}-{idx:03d}',
                iteration=iteration,
                stage=stage,
                target_file=str(file_path.relative_to(project_path)),
            )
        )
    return jobs


def plan_assignments(
    *,
    config: MultiLaneConfig,
    jobs: list[ProverJob],
    state_dir: Path,
    iteration: int,
) -> list[LaneAssignment]:
    runtime_paths = resolve_runtime_paths(state_dir)
    assignments: list[LaneAssignment] = []
    for lane in config.enabled_lanes():
        lane_iter_dir = runtime_paths.lanes_dir / lane.lane_id / f'iter-{iteration:03d}'
        provers_dir = lane_iter_dir / 'provers'
        results_dir = lane_iter_dir / 'results'
        for path in (lane_iter_dir, provers_dir, results_dir):
            path.mkdir(parents=True, exist_ok=True)
        state_view_dir = _prepare_assignment_state_view(state_dir, lane_iter_dir)
        for job in jobs:
            slug = job.target_file.replace('/', '_').removesuffix('.lean')
            assignments.append(
                LaneAssignment(
                    assignment_id=f'{lane.lane_id}--{job.job_id}',
                    lane_id=lane.lane_id,
                    job_id=job.job_id,
                    assigned_file=job.target_file,
                    worktree_path=str(lane_worktree_path(state_dir.parent, lane)),
                    log_path=str(provers_dir / slug),
                    result_path=str(results_dir / f'{slug}.md'),
                    state_view_path=str(state_view_dir),
                )
            )
    return assignments


def build_assignment_prompt(
    *,
    project_name: str,
    lane_project_path: Path,
    state_dir: Path,
    stage: str,
    assignment: LaneAssignment,
    iter_num: int,
) -> str:
    state_view = Path(assignment.state_view_path) if assignment.state_view_path else state_dir
    stage_path = normalize_stage_for_prompt_path(stage)
    return dedent(f"""\
        You are a prover agent for project '{project_name}'. Current stage: {stage}.
        Archon iteration: {iter_num:03d}.
        Project directory: {lane_project_path}
        Instruction/state directory (read-only): {state_view}
        Read {state_view}/CLAUDE.md for your role, then read {state_view}/prompts/prover-{stage_path}.md and {state_view}/PROGRESS.md.
        Check your assigned .lean file for /- USER: ... -/ comments for file-specific hints.

        CRITICAL WORKTREE RULES:
        - The ONLY Lean project checkout you may edit is under {lane_project_path}/
        - NEVER read or edit any .lean file outside {lane_project_path}/
        - Treat the instruction/state directory as read-only metadata, not as the project root.
        - Ignore any sibling or main checkout paths even if they look similar.
        - If a tool path resolves outside {lane_project_path}/, stop and stay inside the worktree.

        IMPORTANT:
        - You own ONLY the file assigned below. Do NOT edit any other .lean file.
        - Write your result summary to EXACTLY this file: {assignment.result_path}
        - Do NOT edit PROGRESS.md, task_pending.md, or task_done.md.
        - Missing Mathlib infrastructure is NEVER a valid reason to leave a sorry.
        - NEVER revert to a bare sorry. Always leave your partial proof attempt in the code.

        Your assigned file: {assignment.assigned_file}
    """)


def preview_round(
    *,
    config: MultiLaneConfig,
    progress_file: Path,
    project_path: Path,
    state_dir: Path,
    iteration: int,
    stage: str,
) -> tuple[dict[str, object], list[LaneAssignment]]:
    jobs = build_jobs_from_progress(
        progress_file=progress_file,
        project_path=project_path,
        iteration=iteration,
        stage=stage,
    )
    assignments = plan_assignments(
        config=config,
        jobs=jobs,
        state_dir=state_dir,
        iteration=iteration,
    )
    return summarize_assignments(config=config, jobs=jobs, assignments=assignments), assignments


def prepare_lanes_for_preview(
    *,
    config: MultiLaneConfig,
    project_path: Path,
) -> list[dict[str, object]]:
    return [prepare_lane(project_path, lane, base_ref=config.base_ref) for lane in config.enabled_lanes()]


def write_preview_runtime_artifacts(
    *,
    state_dir: Path,
    iteration: int,
    assignments: list[LaneAssignment],
    prepared: list[dict[str, object]],
) -> dict[str, str]:
    runtime_paths = resolve_runtime_paths(state_dir)
    assignments_path = runtime_paths.runtime_dir / f'iter-{iteration:03d}-assignments.jsonl'
    prepared_path = runtime_paths.runtime_dir / f'iter-{iteration:03d}-prepared-lanes.jsonl'
    write_runtime_jsonl(assignments_path, assignments)
    write_runtime_jsonl(prepared_path, prepared)
    return {
        'assignments_path': str(assignments_path),
        'prepared_path': str(prepared_path),
    }


def execute_assignments_preview_only(assignments: list[LaneAssignment]) -> list[LaneResult]:
    results: list[LaneResult] = []
    for assignment in assignments:
        results.append(
            LaneResult(
                assignment_id=assignment.assignment_id,
                lane_id=assignment.lane_id,
                job_id=assignment.job_id,
                success=False,
                summary_path=None,
                raw_log_path=None,
                promote_readiness='manual-only',
            )
        )
    return results

"""Collection/summarisation helpers for multi-lane scaffolding."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .types import LaneAssignment, LaneResult, MultiLaneConfig, ProverJob


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def summarize_assignments(
    *,
    config: MultiLaneConfig,
    jobs: list[ProverJob],
    assignments: list[LaneAssignment],
) -> dict[str, object]:
    per_lane: dict[str, int] = {}
    for assignment in assignments:
        per_lane[assignment.lane_id] = per_lane.get(assignment.lane_id, 0) + 1
    return {
        'enabled': config.enabled,
        'lane_count': len(config.enabled_lanes()),
        'job_count': len(jobs),
        'assignment_count': len(assignments),
        'per_lane_assignments': per_lane,
    }


def write_preview_report(
    *,
    report_path: Path,
    summary: dict[str, object],
    prepared: list[dict[str, object]],
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '# Multi-lane preview summary',
        '',
        f"- lane_count: {summary.get('lane_count', 0)}",
        f"- job_count: {summary.get('job_count', 0)}",
        f"- assignment_count: {summary.get('assignment_count', 0)}",
        '',
        '## Per-lane assignments',
    ]
    per_lane = summary.get('per_lane_assignments', {}) or {}
    for lane_id, count in per_lane.items():
        lines.append(f'- {lane_id}: {count}')
    lines.extend(['', '## Prepared lanes'])
    for item in prepared:
        health = item.get('health', {}) or {}
        lines.append(
            f"- {item.get('lane_id')}: created={item.get('created')} branch={health.get('branch_actual')} dirty={health.get('dirty')}"
        )
        settings = item.get('settings_local_json')
        if settings:
            lines.append('  - settings.local.json: configured')
    report_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    json_path = report_path.with_suffix('.json')
    json_path.write_text(json.dumps({'summary': summary, 'prepared': prepared}, indent=2), encoding='utf-8')


def write_runtime_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(_jsonable(row), ensure_ascii=False) + '\n')


def write_results_jsonl(path: Path, results: list[LaneResult]) -> None:
    write_runtime_jsonl(path, results)

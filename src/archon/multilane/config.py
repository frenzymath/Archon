"""Configuration loading for Archon multi-lane proving."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from .types import (
    LaneConfig,
    LaneExecutionConfig,
    LanePromotionConfig,
    LaneWorktreeConfig,
    MultiLaneConfig,
    RuntimePaths,
)

CONFIG_CANDIDATES = ('config.yaml', 'config.yml', 'config.json')
LOCAL_CONFIG_CANDIDATES = ('config.local.yaml', 'config.local.yml', 'config.local.json')
SENSITIVE_ENV_KEYS = ('KEY', 'TOKEN', 'SECRET', 'PASSWORD')


def find_multilane_config(state_dir: Path) -> Path | None:
    root = state_dir / 'multilane'
    for name in CONFIG_CANDIDATES:
        path = root / name
        if path.exists():
            return path
    return None


def find_multilane_local_config(state_dir: Path) -> Path | None:
    root = state_dir / 'multilane'
    for name in LOCAL_CONFIG_CANDIDATES:
        path = root / name
        if path.exists():
            return path
    return None


def _read_config_file(path: Path) -> dict:
    suffix = path.suffix.lower()
    text = path.read_text(encoding='utf-8')
    if suffix == '.json':
        return json.loads(text)
    if suffix in {'.yaml', '.yml'}:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                'YAML multi-lane config requires PyYAML; use JSON for now or install PyYAML.'
            ) from exc
        data = yaml.safe_load(text)
        return data or {}
    raise ValueError(f'Unsupported config format: {path}')


def _merge_dict(base: dict, extra: dict) -> dict:
    result = deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _merge_lane_overrides(base_raw: dict, local_raw: dict) -> dict:
    merged = deepcopy(base_raw)
    if not local_raw:
        return merged
    lane_map = {lane['lane_id']: lane for lane in merged.get('lanes', [])}
    local_lanes = local_raw.get('lanes', {})
    if isinstance(local_lanes, list):
        local_lanes = {lane['lane_id']: lane for lane in local_lanes}
    for lane_id, override in local_lanes.items():
        if lane_id not in lane_map:
            continue
        lane_map[lane_id] = _merge_dict(lane_map[lane_id], override)
    merged['lanes'] = list(lane_map.values())
    for key, value in local_raw.items():
        if key == 'lanes':
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _lane_from_raw(lane_raw: dict) -> LaneConfig:
    worktree_raw = lane_raw.get('worktree', {}) or {}
    execution_raw = lane_raw.get('execution', {}) or {}
    promotion_raw = lane_raw.get('promotion', {}) or {}
    return LaneConfig(
        lane_id=lane_raw['lane_id'],
        label=lane_raw.get('label', lane_raw['lane_id']),
        provider=lane_raw.get('provider', 'unknown'),
        claude_config_dir=lane_raw.get('claude_config_dir'),
        claude_settings_path=lane_raw.get('claude_settings_path'),
        env=dict(lane_raw.get('env', {}) or {}),
        worktree=LaneWorktreeConfig(
            path=worktree_raw['path'],
            branch=worktree_raw['branch'],
            sync_mode=worktree_raw.get('sync_mode', 'hard-reset'),
        ),
        execution=LaneExecutionConfig(
            max_parallel_jobs=int(execution_raw.get('max_parallel_jobs', 1)),
            enabled=bool(execution_raw.get('enabled', True)),
        ),
        promotion=LanePromotionConfig(
            enabled=bool(promotion_raw.get('enabled', False)),
            mode=promotion_raw.get('mode', 'manual-only'),
        ),
        enabled=bool(lane_raw.get('enabled', True)),
        purpose=lane_raw.get('purpose'),
    )


def load_multilane_config(path: Path, local_path: Path | None = None) -> MultiLaneConfig:
    raw = _read_config_file(path)
    if local_path is not None and local_path.exists():
        raw = _merge_lane_overrides(raw, _read_config_file(local_path))
    return MultiLaneConfig(
        enabled=bool(raw.get('enabled', False)),
        version=int(raw.get('version', 1)),
        base_ref=raw.get('base_ref', 'main'),
        grace_minutes=max(0.0, float(raw.get('grace_minutes', 10.0))),
        lanes=[_lane_from_raw(lane_raw) for lane_raw in raw.get('lanes', [])],
    )


def multilane_config_from_simple(simple: dict) -> MultiLaneConfig:
    """Build a MultiLaneConfig from .archon/config.json's ``multilane`` section.

    The top-level project config uses a deliberately minimal lane
    shape — just ``{lane_id, provider, model?}`` — because users only
    edit it to switch on which providers run in parallel. We fill in
    the ``worktree`` defaults here so the rest of the multilane runtime
    keeps using the same ``LaneConfig`` shape it already understands:

    - worktree.path  → ``.archon/lanes/<lane_id>/``  (inner-git worktree)
    - worktree.branch → ``lane/<lane_id>``
    - worktree.sync_mode → ``hard-reset``
    """
    lanes_out: list[LaneConfig] = []
    for raw in simple.get('lanes', []) or []:
        normalized = dict(raw)
        normalized.setdefault('worktree', {})
        wt = normalized['worktree']
        lane_id = str(normalized.get('lane_id') or normalized.get('provider') or 'lane')
        wt.setdefault('path', f'.archon/lanes/{lane_id}')
        wt.setdefault('branch', f'lane/{lane_id}')
        wt.setdefault('sync_mode', 'hard-reset')
        lanes_out.append(_lane_from_raw(normalized))
    return MultiLaneConfig(
        enabled=bool(simple.get('enabled', False)),
        version=int(simple.get('version', 1)),
        base_ref=str(simple.get('base_ref') or 'main'),
        grace_minutes=max(0.0, float(simple.get('grace_minutes', 10.0))),
        lanes=lanes_out,
    )


def resolve_runtime_paths(state_dir: Path) -> RuntimePaths:
    root = state_dir / 'multilane'
    runtime_dir = root / 'runtime'
    reports_dir = root / 'reports'
    lanes_dir = root / 'lanes'
    for path in (root, runtime_dir, reports_dir, lanes_dir):
        path.mkdir(parents=True, exist_ok=True)
    return RuntimePaths(root=root, runtime_dir=runtime_dir, reports_dir=reports_dir, lanes_dir=lanes_dir)


def redacted_lane_summary(lane: LaneConfig) -> dict[str, object]:
    env = {}
    for key, value in lane.env.items():
        env[key] = '<redacted>' if any(token in key.upper() for token in SENSITIVE_ENV_KEYS) else value
    return {
        'lane_id': lane.lane_id,
        'provider': lane.provider,
        'label': lane.label,
        'claude_config_dir': lane.claude_config_dir,
        'claude_settings_path': '<configured>' if lane.claude_settings_path else None,
        'worktree_path': lane.worktree.path,
        'worktree_branch': lane.worktree.branch,
        'sync_mode': lane.worktree.sync_mode,
        'env': env,
    }

"""Worktree lifecycle helpers for multi-lane proving.

Lanes used to be checked out in worktrees of the *outer* mathematician
git repo. The integrated design hosts them inside Archon's *inner* git
(``.archon/git-dir``) instead — that way time-travel via ``archon
branch`` operates on the same history that lanes branch off of, and
the outer repo is never touched by lane-side commits.

The ``LaneWorktreeConfig.path`` is still resolved relative to the
project root for backwards compatibility with existing configs and
tests, but the canonical location is ``.archon/lanes/<lane_id>/``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from archon.commands.tooling.inner_git import InnerGit

from .types import LaneConfig


def lane_worktree_path(project_path: Path, lane: LaneConfig) -> Path:
    return (project_path / lane.worktree.path).resolve()


def _inner(project_path: Path) -> InnerGit:
    """Return an initialized InnerGit, or raise if the project is unbootstrapped."""
    inner = InnerGit(project_path)
    if not inner.is_initialized():
        raise RuntimeError(
            f'Inner git not initialized at {inner.git_dir}. '
            f'Run `archon init` before preparing lane worktrees.'
        )
    return inner


def ensure_lane_worktree(project_path: Path, lane: LaneConfig, *, base_ref: str) -> dict[str, object]:
    path = lane_worktree_path(project_path, lane)
    if path.exists():
        return {'path': str(path), 'created': False, 'branch': lane.worktree.branch}
    inner = _inner(project_path)
    inner.worktree_add(path, lane.worktree.branch, base_ref=base_ref)
    return {'path': str(path), 'created': True, 'branch': lane.worktree.branch}


def sync_lane_worktree(project_path: Path, lane: LaneConfig, *, base_ref: str) -> dict[str, object]:
    path = lane_worktree_path(project_path, lane)
    if not path.exists():
        raise FileNotFoundError(f'Lane worktree does not exist: {path}')
    inner = _inner(project_path)
    rev = inner._run(['rev-parse', base_ref], check=False).stdout.strip()
    if not rev:
        raise RuntimeError(f'Could not resolve base ref {base_ref!r} from inner git')
    if lane.worktree.sync_mode == 'hard-reset':
        # Operate on the lane worktree directly: ``git -C <path>`` finds
        # the linked worktree's pointer file and routes to the inner
        # git-dir without needing the GIT_DIR/GIT_WORK_TREE env vars.
        cwd = str(path)
        subprocess.run(['git', '-C', cwd, 'checkout', '--force', '--detach', rev], check=True, capture_output=True, text=True)
        subprocess.run(['git', '-C', cwd, 'branch', '-f', lane.worktree.branch, rev], check=True, capture_output=True, text=True)
        subprocess.run(['git', '-C', cwd, 'checkout', lane.worktree.branch], check=True, capture_output=True, text=True)
        subprocess.run(['git', '-C', cwd, 'reset', '--hard', rev], check=True, capture_output=True, text=True)
        subprocess.run(['git', '-C', cwd, 'clean', '-fd'], check=True, capture_output=True, text=True)
    snapshot = lane_health_snapshot(project_path, lane)
    snapshot['synced_to'] = rev
    snapshot['base_ref'] = base_ref
    return snapshot


def ensure_lane_settings(project_path: Path, lane: LaneConfig) -> Path | None:
    if not lane.claude_settings_path:
        return None
    src = Path(lane.claude_settings_path).expanduser()
    if not src.exists():
        raise FileNotFoundError(f'Lane settings file not found: {src}')
    path = lane_worktree_path(project_path, lane)
    dst = path / '.claude' / 'settings.local.json'
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


def prepare_lane(project_path: Path, lane: LaneConfig, *, base_ref: str) -> dict[str, object]:
    created = ensure_lane_worktree(project_path, lane, base_ref=base_ref)
    synced = sync_lane_worktree(project_path, lane, base_ref=base_ref)
    settings_path = ensure_lane_settings(project_path, lane)
    return {
        'lane_id': lane.lane_id,
        'created': created['created'],
        'settings_local_json': str(settings_path) if settings_path else None,
        'health': synced,
    }


def lane_health_snapshot(project_path: Path, lane: LaneConfig) -> dict[str, object]:
    path = lane_worktree_path(project_path, lane)
    snapshot: dict[str, object] = {
        'lane_id': lane.lane_id,
        'path': str(path),
        'exists': path.exists(),
        'branch_expected': lane.worktree.branch,
    }
    if not path.exists():
        return snapshot
    cwd = str(path)
    result = subprocess.run(['git', '-C', cwd, 'status', '--short'], capture_output=True, text=True)
    snapshot['dirty'] = bool((result.stdout or '').strip())
    branch = subprocess.run(['git', '-C', cwd, 'branch', '--show-current'], capture_output=True, text=True)
    snapshot['branch_actual'] = (branch.stdout or '').strip()
    return snapshot

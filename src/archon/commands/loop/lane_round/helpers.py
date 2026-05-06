"""Shared helpers for lane-round work: dirty-tree probes, snapshot
extraction, and the strict-success classifier.

These are pure functions on filesystem state — no Claude I/O — so they
unit-test cleanly and are reused by `LaneAssignmentRunner` and the
contamination check.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def git_diff_files(repo_path: Path) -> list[str]:
    """Return outer-git modified files (best-effort; empty if outer git is absent)."""
    result = subprocess.run(
        ['git', '-C', str(repo_path), 'diff', '--name-only', 'HEAD'],
        capture_output=True,
        text=True,
    )
    return sorted(
        {line.strip() for line in (result.stdout or '').splitlines() if line.strip()},
    )


def non_archon_dirty_files(repo_path: Path) -> list[str]:
    """Outer-git modifications excluding `.archon/` state files."""
    return [path for path in git_diff_files(repo_path) if not path.startswith('.archon/')]


def restore_repo_paths(repo_path: Path, paths: list[str]) -> None:
    if not paths:
        return
    subprocess.run(
        ['git', '-C', str(repo_path), 'checkout', '--', *sorted(set(paths))],
        capture_output=True,
        text=True,
    )


def _record_assignment_file(
    *,
    raw_path: str,
    lane_root: Path,
    project_root: Path,
    changed_files: set[str],
    escaped_files: set[str],
) -> None:
    rel = str(raw_path or '').strip()
    if not rel.endswith('.lean'):
        return
    resolved = (lane_root / rel).resolve() if not os.path.isabs(rel) else Path(rel).resolve()
    try:
        changed_files.add(str(resolved.relative_to(lane_root)))
        return
    except ValueError:
        pass
    try:
        escaped_files.add(str(resolved.relative_to(project_root)))
    except ValueError:
        escaped_files.add(str(resolved))


def assignment_code_snapshot_files(
    log_path: Path, lane_path: Path, project_path: Path,
) -> tuple[list[str], list[str], str]:
    """Read a lane's JSONL log and classify which files it touched.

    Returns `(changed_files, escaped_files, source)`:
      - `changed_files`: paths inside the lane worktree (relative)
      - `escaped_files`: paths that resolved outside the worktree
      - `source`: 'code_snapshot' (preferred), 'tool_call' (fallback), or 'none'

    Prefers `code_snapshot` events emitted by the prover hooks. Falls
    back to scanning `tool_call` Edit/Write rows if the hooks didn't
    fire (rare — older prompts, sandbox modes that disable hooks).
    """
    lane_root = lane_path.resolve()
    project_root = project_path.resolve()
    changed_files: set[str] = set()
    escaped_files: set[str] = set()
    source = 'none'
    if not log_path.exists():
        return [], [], source

    fallback_tool_paths: list[str] = []
    for raw_line in log_path.read_text(encoding='utf-8', errors='ignore').splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        event = row.get('event')
        if event == 'code_snapshot':
            _record_assignment_file(
                raw_path=str(row.get('file') or ''),
                lane_root=lane_root,
                project_root=project_root,
                changed_files=changed_files,
                escaped_files=escaped_files,
            )
            if changed_files or escaped_files:
                source = 'code_snapshot'
        elif event == 'tool_call' and row.get('tool') in {'Edit', 'Write'}:
            file_path = str((row.get('input') or {}).get('file_path') or '').strip()
            if file_path.endswith('.lean'):
                fallback_tool_paths.append(file_path)

    if not changed_files and not escaped_files and fallback_tool_paths:
        for file_path in fallback_tool_paths:
            _record_assignment_file(
                raw_path=file_path,
                lane_root=lane_root,
                project_root=project_root,
                changed_files=changed_files,
                escaped_files=escaped_files,
            )
        if changed_files or escaped_files:
            source = 'tool_call'

    return sorted(changed_files), sorted(escaped_files), source


def assignment_success(
    *,
    ok: bool,
    assigned_file: str,
    changed_files: list[str],
    escaped_files: list[str],
    summary_path: str | None,
    assigned_file_path: str | None = None,
) -> tuple[bool, str | None]:
    """Strict success classifier.

    A lane assignment is successful only if every guardrail passes:
    runner returned ok, no escaped paths, summary exists, exactly the
    assigned file changed, and no `sorry` placeholder remains.
    """
    if not ok:
        return False, 'runner_failed'
    if escaped_files:
        return False, 'escaped_worktree'
    if summary_path is None:
        return False, 'missing_summary'
    if not changed_files:
        return False, 'no_file_change'
    changed = set(changed_files)
    if changed != {assigned_file}:
        return False, 'cross_file_change'
    if assigned_file_path is not None:
        try:
            text = Path(assigned_file_path).read_text(encoding='utf-8', errors='ignore')
        except OSError:
            return False, 'missing_assigned_file'
        if 'sorry' in text:
            return False, 'placeholder_remaining'
    return True, None

"""Shared helpers for lane-round work: dirty-tree probes, snapshot
extraction, and the success / early-stop classifiers.

These are pure functions on filesystem state — no Claude I/O — so they
unit-test cleanly and are reused by `LaneAssignmentRunner` and the
contamination check.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


# Comment-aware sorry counter. The previous implementation used a bare
# substring match (`'sorry' in text`), which fired on `-- discuss the
# sorry tactic` and identifiers like `sorrys`. We strip line and block
# comments, then count word-boundary matches.
_LEAN_BLOCK_COMMENT = re.compile(r'/-.*?-/', re.DOTALL)
_LEAN_LINE_COMMENT = re.compile(r'--[^\n]*')
_LEAN_STRING_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"')
_SORRY_WORD = re.compile(r'\bsorry\b')


def count_sorries_in_text(text: str) -> int:
    """Count `sorry` placeholders, ignoring comments and string literals.

    Lean block comments don't nest in this stripper — that's fine for
    counting because nested comments are rare in real proof bodies and
    even false positives only come from comment text, not real proofs.
    """
    stripped = _LEAN_BLOCK_COMMENT.sub('', text)
    stripped = _LEAN_LINE_COMMENT.sub('', stripped)
    stripped = _LEAN_STRING_LITERAL.sub('""', stripped)
    return len(_SORRY_WORD.findall(stripped))


def _read_sorry_count(path: str | Path | None) -> int | None:
    """Read a Lean file and return its sorry count, or None if unreadable."""
    if path is None:
        return None
    try:
        text = Path(path).read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return None
    return count_sorries_in_text(text)


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
    baseline_file_path: str | None = None,
) -> tuple[bool, str | None]:
    """Loose 'merge-worthy' classifier — did the lane do useful work?

    A lane is merge-worthy iff: runner returned ok, no escaped paths, a
    summary exists, the *only* changed file is the assigned one, and
    the sorry count in the assigned file strictly decreased (or was
    already 0). The "decreased" check replaces the old "no sorry
    remaining" rule, which incorrectly failed legitimate partial wins
    on files that have multiple sorries but only one assigned.

    Use `assignment_early_stop_worthy` for the stricter "lane has fully
    cleared this file" check that drives the early-stop / first-clean-
    wins timer.
    """
    if not ok:
        return False, 'runner_failed'
    if escaped_files:
        return False, 'escaped_worktree'
    if summary_path is None:
        return False, 'missing_summary'
    if not changed_files:
        return False, 'no_file_change'
    if set(changed_files) != {assigned_file}:
        return False, 'cross_file_change'
    if assigned_file_path is not None:
        sorry_after = _read_sorry_count(assigned_file_path)
        if sorry_after is None:
            return False, 'missing_assigned_file'
        sorry_before = _read_sorry_count(baseline_file_path)
        # If we have a baseline, require strict progress.
        # If we don't (no snapshot was taken), accept rather than
        # reject — better to count the lane as merge-worthy than to
        # drop a real win because we couldn't compare.
        if sorry_before is not None:
            if sorry_after >= sorry_before and sorry_after > 0:
                return False, 'no_sorry_decrease'
    return True, None


def assignment_early_stop_worthy(
    *,
    success: bool,
    assigned_file_path: str | None,
) -> bool:
    """Strict 'lane has fully cleared this file' check.

    Used by the multi-lane early-stop trigger: only a fully sorry-free
    win on the assigned file should kill slower lanes. Partial wins go
    to the merger but do not preempt other lanes.
    """
    if not success or assigned_file_path is None:
        return False
    sorry_after = _read_sorry_count(assigned_file_path)
    return sorry_after == 0

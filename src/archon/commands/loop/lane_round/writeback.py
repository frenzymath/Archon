"""Promote/merge lane outputs back into the project's main checkout.

The merge agent picks the best proof per file when multiple lanes
succeed; for files with a single successful lane, this collapses to a
verbatim copy. Either way every result row gets `promoted_to_main`
stamped so the JSONL stays accurate.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def select_writeback_rows(
    results: list[dict[str, object]],
    *,
    preferred_lane_id: str | None,
    limit: int = 1,
) -> list[dict[str, object]]:
    """Legacy single-lane writeback selector.

    Picks up to `limit` rows from the preferred lane that succeeded
    cleanly. Now used only for the report's ``preferred_lane_id``
    annotation — actual promotion goes through the per-file merge agent.
    """
    if not preferred_lane_id or limit <= 0:
        return []
    candidates: list[dict[str, object]] = []
    for row in sorted(results, key=lambda r: str(r.get('assignment_id', ''))):
        if row.get('lane_id') != preferred_lane_id:
            continue
        if not row.get('success'):
            continue
        if not row.get('assigned_file_only') or not row.get('verification_passed'):
            continue
        if not row.get('assigned_file') or not row.get('worktree_path'):
            continue
        candidates.append(row)
        if len(candidates) >= limit:
            break
    return candidates


def group_writeback_candidates_by_file(
    results: list[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    """Group successful, eligible result rows by their assigned file.

    A row is eligible iff it succeeded, did not escape its worktree,
    actually changed its assigned file, and has a worktree we can read
    from. The grouping preserves the order in which lanes finished
    (first-clean-wins) by sorting on assignment_id, so the merger can
    be told which candidate is the "first" if it falls back.
    """
    groups: dict[str, list[dict[str, object]]] = {}
    for row in sorted(results, key=lambda r: str(r.get('assignment_id', ''))):
        if not row.get('success'):
            continue
        if not row.get('assigned_file_only') or not row.get('verification_passed'):
            continue
        rel = str(row.get('assigned_file') or '')
        worktree_path = str(row.get('worktree_path') or '')
        if not rel or not worktree_path:
            continue
        groups.setdefault(rel, []).append(row)
    return groups


def preferred_writeback_lane(config) -> str | None:
    """Pick a lane id for the report's `preferred_lane_id` annotation.

    Anthropic comes first if present (most reliable historical baseline),
    otherwise the first enabled lane.
    """
    for lane in config.enabled_lanes():
        if lane.provider == 'anthropic':
            return lane.lane_id
    enabled = config.enabled_lanes()
    return enabled[0].lane_id if enabled else None


def git_commit_paths(repo_path: Path, paths: list[str], message: str) -> str | None:
    """Stage + commit specific paths in the outer git repo.

    Returns the commit SHA, or None if there was nothing to commit OR
    the outer git is unavailable. Any git failure is swallowed —
    first-clean-wins semantics work whether or not an outer git is
    present.
    """
    unique_paths = sorted({path for path in paths if path})
    if not unique_paths:
        return None
    try:
        subprocess.run(
            ['git', '-C', str(repo_path), 'add', '--', *unique_paths],
            check=True, capture_output=True, text=True,
        )
        diff = subprocess.run(
            ['git', '-C', str(repo_path), 'diff', '--cached', '--quiet', '--', *unique_paths],
            capture_output=True,
            text=True,
        )
        if diff.returncode == 0:
            return None
        subprocess.run(
            ['git', '-C', str(repo_path), 'commit', '-m', message],
            check=True, capture_output=True, text=True,
        )
        head = subprocess.run(
            ['git', '-C', str(repo_path), 'rev-parse', 'HEAD'],
            check=True, capture_output=True, text=True,
        )
        return (head.stdout or '').strip() or None
    except subprocess.CalledProcessError:
        return None


class WritebackPromoter:
    """Per-file merge across all successful lanes, then commit the result.

    For files with one successful lane, this collapses to verbatim copy.
    For files with multiple successful lanes, the merge agent picks the
    best proof per declaration. Either way, every row in `groups` gets
    its `promoted_to_main` flag set so the results JSONL stays correct.
    """

    def __init__(
        self,
        *,
        project_path: Path,
        state_dir: Path,
        iteration: int,
        model: str,
        verbose_logs: bool,
    ) -> None:
        self.project_path = project_path
        self.state_dir = state_dir
        self.iteration = iteration
        self.model = model
        self.verbose_logs = verbose_logs

    def promote_simple(self, rows: list[dict[str, object]]) -> dict[str, object]:
        """Verbatim copy + commit. Used when no merge agent is needed."""
        promoted_files: list[str] = []
        for row in rows:
            rel = str(row.get('assigned_file') or '')
            worktree_path = str(row.get('worktree_path') or '')
            if not rel or not worktree_path:
                row['promoted_to_main'] = False
                continue
            src = Path(worktree_path) / rel
            dst = self.project_path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            promoted_files.append(rel)
            row['promoted_to_main'] = True

        commit_sha = None
        if promoted_files:
            lane = rows[0].get('lane_id', 'lane') if rows else 'lane'
            message = (
                f"multilane promote({lane}): iter-{self.iteration:03d} "
                + ', '.join(promoted_files)
            )
            commit_sha = git_commit_paths(self.project_path, promoted_files, message)
        for row in rows:
            row['promotion_commit'] = commit_sha
        return {'promoted_files': promoted_files, 'promotion_commit': commit_sha}

    def merge_and_promote(
        self, groups: dict[str, list[dict[str, object]]],
    ) -> dict[str, object]:
        """Per-file merge across lanes, then a single commit for the whole batch."""
        from archon.multilane.merge_agent import LaneCandidate, merge_file_versions

        promoted_files: list[str] = []
        merge_records: list[dict[str, object]] = []

        for rel, rows in sorted(groups.items()):
            candidates: list[LaneCandidate] = []
            for row in rows:
                worktree_path = str(row.get('worktree_path') or '')
                if not worktree_path:
                    continue
                candidates.append(LaneCandidate(
                    lane_id=str(row.get('lane_id') or 'unknown'),
                    source_path=Path(worktree_path) / rel,
                ))
            if not candidates:
                for row in rows:
                    row['promoted_to_main'] = False
                continue

            outcome = merge_file_versions(
                project_path=self.project_path,
                state_dir=self.state_dir,
                target_rel=rel,
                candidates=candidates,
                iteration=self.iteration,
                model=self.model,
                verbose_logs=self.verbose_logs,
            )
            self._symlink_merge_log(outcome, rel)

            promoted_files.append(rel)
            for row in rows:
                row['promoted_to_main'] = True
                row['merge_outcome'] = 'merged' if outcome.merged else 'copied'
                row['merge_chosen_lane'] = outcome.chosen_lane
            merge_records.append({
                'file': rel,
                'merged': outcome.merged,
                'chosen_lane': outcome.chosen_lane,
                'lane_count': len(candidates),
                'lanes': [c.lane_id for c in candidates],
            })

        commit_sha = None
        if promoted_files:
            message = (
                f"multilane merge: iter-{self.iteration:03d} "
                + ', '.join(promoted_files)
            )
            commit_sha = git_commit_paths(self.project_path, promoted_files, message)
        for rows in groups.values():
            for row in rows:
                row.setdefault('promotion_commit', commit_sha)
                if 'promotion_commit' in row and row['promotion_commit'] is None:
                    row['promotion_commit'] = commit_sha

        return {
            'promoted_files': promoted_files,
            'promotion_commit': commit_sha,
            'merges': merge_records,
        }

    def _symlink_merge_log(self, outcome, rel: str) -> None:
        """Surface the merger's JSONL under iter_dir/provers/.

        The dashboard's existing log enumeration shows it alongside the
        per-lane prover logs. We symlink (not copy) so live tail still
        works during the run.
        """
        if outcome.log_path is None:
            return
        iter_dir = self.state_dir / 'logs' / f'iter-{self.iteration:03d}'
        (iter_dir / 'provers').mkdir(parents=True, exist_ok=True)
        slug = rel.replace('/', '_').removesuffix('.lean')
        for ext in ('jsonl', 'raw.jsonl'):
            target = Path(str(outcome.log_path).removesuffix('.jsonl') + f'.{ext}')
            link = iter_dir / 'provers' / f'{slug}__merge.{ext}'
            try:
                if link.exists() or link.is_symlink():
                    link.unlink()
                link.symlink_to(target)
            except OSError:
                pass

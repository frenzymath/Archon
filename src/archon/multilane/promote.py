"""Promotion helpers for multi-lane proving.

Initial implementation stays intentionally conservative: it only derives
candidate metadata and leaves application/merge behaviour for later
phases.
"""

from __future__ import annotations

from .types import LaneResult, PromoteCandidate


def candidate_from_result(result: LaneResult, *, source_commit: str, target_file: str) -> PromoteCandidate:
    safety = 'safe' if result.assigned_file_only and result.verification_passed else 'review_needed'
    if not result.patch_path:
        safety = 'unsafe'
    return PromoteCandidate(
        candidate_id=f'candidate--{result.assignment_id}',
        lane_id=result.lane_id,
        assignment_id=result.assignment_id,
        source_commit=source_commit,
        target_file=target_file,
        changed_files=list(result.changed_files),
        patch_path=result.patch_path,
        safety_level=safety,
    )

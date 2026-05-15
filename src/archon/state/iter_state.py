"""Per-iteration state sidecars.

The plan and review agents record their per-iteration narrative in
dedicated sidecar files instead of appending to STRATEGY.md /
PROJECT_STATUS.md / task_pending.md:

    .archon/iter/
    ├── iter-001/
    │   ├── plan.md          # what the plan agent decided this iter
    │   ├── objectives.md    # per-attempt rationale + detail
    │   └── review.md        # this iter's review summary
    ├── iter-002/
    ...

Top-level files (STRATEGY.md, PROGRESS.md, PROJECT_STATUS.md,
task_pending.md, task_done.md) keep their roles but stop growing per
iter — agents are instructed by their prompts to write only stable
/ current-state content there.

This module provides path helpers, the iter-dir initializer (called
from the loop at iter start), and the bounded-window reader the
prompt builders use to inject the last K iters' decisions into the
next agent's context.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SIDECAR_ROOT_NAME = "iter"
PLAN_SIDECAR_NAME = "plan.md"
REVIEW_SIDECAR_NAME = "review.md"
OBJECTIVES_SIDECAR_NAME = "objectives.md"


# ── path helpers ─────────────────────────────────────────────────────


def iter_sidecar_root(state_dir: Path) -> Path:
    """``<state_dir>/iter/`` — the parent of all per-iter sidecar dirs."""
    return state_dir / SIDECAR_ROOT_NAME


def iter_sidecar_dir(state_dir: Path, iter_num: int) -> Path:
    """``<state_dir>/iter/iter-NNN/`` for one iteration."""
    return iter_sidecar_root(state_dir) / f"iter-{iter_num:03d}"


def plan_sidecar_path(state_dir: Path, iter_num: int) -> Path:
    return iter_sidecar_dir(state_dir, iter_num) / PLAN_SIDECAR_NAME


def review_sidecar_path(state_dir: Path, iter_num: int) -> Path:
    return iter_sidecar_dir(state_dir, iter_num) / REVIEW_SIDECAR_NAME


def objectives_sidecar_path(state_dir: Path, iter_num: int) -> Path:
    return iter_sidecar_dir(state_dir, iter_num) / OBJECTIVES_SIDECAR_NAME


# ── lifecycle ────────────────────────────────────────────────────────


def init_iter_sidecar_dir(state_dir: Path, iter_num: int) -> Path:
    """Create ``iter/iter-NNN/`` at iteration start.

    Returns the directory path (whether freshly created or already
    existing). Idempotent.
    """
    d = iter_sidecar_dir(state_dir, iter_num)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── readers ─────────────────────────────────────────────────────────


@dataclass
class IterSidecarSnapshot:
    """One iter's sidecar content, packaged for prompt injection.

    Empty strings indicate the corresponding file was missing or
    unreadable; the caller decides whether to skip the iter or include
    a "no content" placeholder.
    """
    iter_num: int
    plan: str
    review: str
    objectives: str


def _existing_iter_nums(state_dir: Path) -> list[int]:
    root = iter_sidecar_root(state_dir)
    if not root.is_dir():
        return []
    nums: list[int] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if not name.startswith("iter-"):
            continue
        suffix = name[len("iter-"):]
        if not suffix.isdigit():
            continue
        nums.append(int(suffix))
    nums.sort()
    return nums


def _read_or_empty(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def read_recent_iter_sidecars(
    state_dir: Path,
    *,
    current_iter: int,
    window: int,
) -> list[IterSidecarSnapshot]:
    """Return up to ``window`` most-recent sidecars STRICTLY BEFORE current_iter.

    Ordered oldest-first so prompts can render them in chronological
    order. The current iteration is excluded because the plan/review
    agent hasn't written to it yet at the time the prompt is built.
    """
    if window < 1:
        return []
    all_nums = _existing_iter_nums(state_dir)
    candidates = [n for n in all_nums if n < current_iter]
    if not candidates:
        return []
    selected = candidates[-window:]
    snapshots: list[IterSidecarSnapshot] = []
    for n in selected:
        snapshots.append(IterSidecarSnapshot(
            iter_num=n,
            plan=_read_or_empty(plan_sidecar_path(state_dir, n)),
            review=_read_or_empty(review_sidecar_path(state_dir, n)),
            objectives=_read_or_empty(objectives_sidecar_path(state_dir, n)),
        ))
    return snapshots


def format_recent_iter_sidecars_for_prompt(
    snapshots: list[IterSidecarSnapshot],
    *,
    include_plan: bool = True,
    include_review: bool = True,
    include_objectives: bool = False,
    per_section_max_chars: int = 4000,
) -> str:
    """Render snapshots as a markdown block for prompt injection.

    Each section is truncated to ``per_section_max_chars`` to bound the
    total prompt size. Empty/missing files are omitted. Returns "" if
    there's nothing to render.
    """
    if not snapshots:
        return ""

    def _truncate(body: str) -> str:
        body = body.strip()
        if len(body) > per_section_max_chars:
            return body[:per_section_max_chars] + "\n\n... (truncated)"
        return body

    lines: list[str] = ["## Recent iteration sidecars"]
    for snap in snapshots:
        header = f"### iter-{snap.iter_num:03d}"
        sections: list[str] = []
        if include_plan and snap.plan.strip():
            sections.append(f"**plan.md**:\n\n{_truncate(snap.plan)}")
        if include_review and snap.review.strip():
            sections.append(f"**review.md**:\n\n{_truncate(snap.review)}")
        if include_objectives and snap.objectives.strip():
            sections.append(f"**objectives.md**:\n\n{_truncate(snap.objectives)}")
        if not sections:
            continue
        lines.append(header)
        lines.extend(sections)
    if len(lines) == 1:
        # Header only — no sidecars had useful content.
        return ""
    return "\n\n".join(lines)


__all__ = [
    "SIDECAR_ROOT_NAME",
    "PLAN_SIDECAR_NAME",
    "REVIEW_SIDECAR_NAME",
    "OBJECTIVES_SIDECAR_NAME",
    "IterSidecarSnapshot",
    "iter_sidecar_root",
    "iter_sidecar_dir",
    "plan_sidecar_path",
    "review_sidecar_path",
    "objectives_sidecar_path",
    "init_iter_sidecar_dir",
    "read_recent_iter_sidecars",
    "format_recent_iter_sidecars_for_prompt",
]

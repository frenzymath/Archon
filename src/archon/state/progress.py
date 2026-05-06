"""Read PROGRESS.md: current stage + objective files."""

from __future__ import annotations

import re
from pathlib import Path


# The plan agent writes objectives in three shapes — a per-objective
# ``###`` heading whose title contains the file, an unordered bullet
# list, or an ordered numbered list. Patterns in declaration order:
_OBJECTIVE_HEADING = re.compile(
    r'^\s*###\s+.*?(?:\*\*|`)([^*`]+\.lean)(?:\*\*|`)',
)
# "- "/"* "-style: accept any `.lean` reference on the line — the
# planner sometimes writes "- work on `Foo.lean`" without bolding the
# file. Reference mentions ("- see also `Bar.lean` for ...") get
# excluded by the colon-prefix check below.
_OBJECTIVE_BULLET = re.compile(
    r'^\s*[-*]\s+.*?(?:\*\*|`)([^*`]+\.lean)(?:\*\*|`)',
)
# "N. "-style numbered lists: stricter — require the file marker right
# after the number with at most an opening `**` between them. The
# planner's iteration templates write "1. **`Foo.lean`** ...", so this
# still matches the legit case but excludes Strategy-notes-style
# sub-points like "1. compare with `Foo.lean`" that share the section.
_OBJECTIVE_NUMBERED = re.compile(
    r'^\s*\d+\.\s+(?:\*\*\s*)?(?:\*\*|`)([^*`]+\.lean)(?:\*\*|`)',
)

# Multilane copies the project tree into .archon/lanes/<lane>/ for each
# lane's worktree. Without excluding .archon, rglob finds the lane's
# copy *first* and the planner ends up assigning every prover to e.g.
# ".archon/lanes/anthropic/Foo.lean" — which then resolves to nonsense
# relative paths inside other lanes' worktrees.
_SKIP_PARTS = {".lake", "lake-packages", ".archon"}

# Plan agents sometimes list off-limits files inside `## Current Objectives`
# (e.g. "### 3. The protected files (`Foo.lean`) — DO NOT TOUCH"). Without
# filtering, the regex picks up `Foo.lean` as an objective and the
# dispatcher fans out a prover that immediately stops with a no-op task
# result. Costs API time for no benefit.
#
# Markers must be unambiguous *line-level* directives — phrases that only
# make sense as a "skip this whole entry" annotation, never as ad-hoc text
# referring to a secondary file mentioned in the same line. A bullet like
# "use ideas from `Helper.lean` but do not edit it" is a *legitimate*
# objective whose stop wording refers to the secondary file, not the
# primary one, so phrases like "do not edit" are deliberately excluded.
_STOP_MARKERS = (
    "do not touch",
    "do not assign",
    "do-not-touch",
    "off-limits",
    "off limits",
    "do not work on",
    "no objective",
)


def _has_stop_marker(line: str) -> bool:
    low = line.lower()
    return any(marker in low for marker in _STOP_MARKERS)


def read_stage(progress_file: Path, force_stage: str | None = None) -> str:
    """Read the current stage from PROGRESS.md (or return forced override)."""
    if force_stage:
        return force_stage
    if not progress_file.exists():
        raise FileNotFoundError(f"PROGRESS.md not found at {progress_file}")
    lines = progress_file.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## Current Stage"):
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    raise ValueError("Could not read current stage from PROGRESS.md")


def is_complete(progress_file: Path, force_stage: str | None = None) -> bool:
    try:
        return read_stage(progress_file, force_stage) == "COMPLETE"
    except (FileNotFoundError, ValueError):
        return False


def parse_objective_files(progress_file: Path, project_path: Path) -> list[Path]:
    """Extract assigned .lean objective files from ## Current Objectives.

    Heading-style entries take precedence; if none present, both bullet
    and numbered lists are accepted.
    """
    if not progress_file.exists():
        return []

    section_lines = _extract_section(progress_file.read_text(), "## Current Objectives")
    candidates = _extract_heading_candidates(section_lines)
    if not candidates:
        candidates = _extract_list_candidates(section_lines)

    return _resolve_candidate_paths(project_path, candidates)


def _extract_section(text: str, heading: str) -> list[str]:
    """Return lines under `heading` (until the next ## heading)."""
    in_section = False
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith(heading):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            out.append(line)
    return out


def _extract_heading_candidates(section_lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in section_lines:
        if _has_stop_marker(line):
            continue
        m = _OBJECTIVE_HEADING.search(line)
        if m:
            out.append(m.group(1).strip())
    return out


def _extract_list_candidates(section_lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in section_lines:
        if _has_stop_marker(line):
            continue
        m = _OBJECTIVE_BULLET.search(line) or _OBJECTIVE_NUMBERED.search(line)
        if not m:
            continue
        prefix = line[:m.start(1)]
        if ':' in prefix:
            continue
        out.append(m.group(1).strip())
    return out


def _resolve_candidate_paths(project_path: Path, candidates: list[str]) -> list[Path]:
    results: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        for match in project_path.rglob(f"*{candidate}"):
            if any(part in _SKIP_PARTS for part in match.parts):
                continue
            resolved = str(match.resolve())
            if resolved not in seen:
                seen.add(resolved)
                results.append(match)
                break
    return sorted(results)

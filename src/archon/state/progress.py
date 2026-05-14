"""Read PROGRESS.md: current stage + objective files."""

from __future__ import annotations

import re
from pathlib import Path

from archon import log


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


# Headings the plan agent has been observed to use *instead of* the
# canonical "## Current Objectives" — the prover dispatcher's parser
# looks for that exact heading, so a drift here silently empties prover
# dispatch. ``auto_fix_objectives`` renames the first of these that
# happens to contain parseable .lean entries back to the canonical name.
#
# Order matters: more-specific headings first, ambiguous ones (like
# "## Strategy", which is a legitimate non-objectives section in many
# projects) last so they only win when nothing better is available.
_FALLBACK_OBJECTIVES_HEADINGS = (
    "## Objectives",
    "## Next Objectives",
    "## Plan Objectives",
    "## Current objectives",
    "## current objectives",
    "## CURRENT OBJECTIVES",
    "## Strategy",
    "## Targets",
    "## Plan",
)


def _has_stop_marker(line: str) -> bool:
    low = line.lower()
    return any(marker in low for marker in _STOP_MARKERS)

def write_stage(progress_file: Path, new_stage: str) -> None:
    """Reconstruct the Current Stage section with `\\n\\n{stage}\\n\\n` spacing.

    Raises ValueError if the `## Current Stage` ... `## Stages` section cannot
    be located, so a corrupted PROGRESS.md surfaces loudly instead of silently
    leaving the stage unchanged.
    """
    if not progress_file.exists():
        return

    content = progress_file.read_text()
    pattern = r"## Current Stage.*?(?=## Stages)"
    replacement = f"## Current Stage\n\n{new_stage}\n\n"

    new_content, n = re.subn(
        pattern,
        replacement,
        content,
        count=1,
        flags=re.DOTALL,
    )

    if n == 0:
        raise ValueError(
            f"Could not locate '## Current Stage' ... '## Stages' section in "
            f"{progress_file}; stage not updated."
        )

    if new_content != content:
        progress_file.write_text(new_content)
    log.info(f"PROGRESS.md stage set to: {new_stage}")

def read_stage(progress_file: Path, force_stage: str | None = None) -> str:
    """Read the current stage from PROGRESS.md using regex."""
    if force_stage:
        return force_stage
    if not progress_file.exists():
        raise FileNotFoundError(f"PROGRESS.md not found at {progress_file}")

    content = progress_file.read_text()

    pattern = r"## Current Stage\s+([^\n#]+)"
    
    match = re.search(pattern, content)
    if match:
        return match.group(1).strip()
    
    raise ValueError("Could not read current stage from PROGRESS.md")


def is_complete(progress_file: Path, force_stage: str | None = None) -> bool:
    try:
        return "COMPLETE" in read_stage(progress_file, force_stage)
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


def auto_fix_objectives(
    progress_file: Path, project_path: Path,
) -> tuple[list[Path], list[str]]:
    """Parse objectives; on miss, try deterministic rewrites and re-parse.

    Returns ``(objectives, fixes_applied)``. ``fixes_applied`` is a list
    of short human-readable strings describing what was rewritten in
    PROGRESS.md — empty when nothing was changed (either the file
    already parsed or no fixable mistake was detected).

    Today's rewrite catalogue is small on purpose: the plan agent
    occasionally writes its objective list under a non-canonical heading
    (``## Strategy``, ``## Objectives``, lower-case variants, …). When
    that section contains ``### N. **<file>.lean**`` entries that *would*
    parse if the heading were right, rename the heading in place. Any
    case the rewrites can't fix falls through with an empty
    ``objectives`` list so the caller (``validate_plan_output``) can log
    a corrective hint for the next plan agent and skip prover dispatch.
    """
    objectives = parse_objective_files(progress_file, project_path)
    if objectives or not progress_file.exists():
        return objectives, []

    text = progress_file.read_text()

    # If the canonical heading already exists but produced no
    # objectives, no rename will help — the issue is content, not
    # heading. Fall through to caller's USER_HINTS feedback path.
    if "## Current Objectives" in text:
        return [], []

    for candidate in _FALLBACK_OBJECTIVES_HEADINGS:
        section = _extract_section(text, candidate)
        if not section:
            continue
        looks_like_objectives = any(
            _OBJECTIVE_HEADING.search(line)
            or _OBJECTIVE_BULLET.search(line)
            or _OBJECTIVE_NUMBERED.search(line)
            for line in section
        )
        if not looks_like_objectives:
            continue

        # Anchor the rename to a standalone heading line so a code-fence
        # mention of the same string (e.g. inside a quoted prior plan)
        # isn't mutated. Match the heading then optional trailing
        # whitespace, end of line.
        pattern = re.compile(
            rf"^{re.escape(candidate)}\s*$",
            re.MULTILINE,
        )
        new_text, n = pattern.subn("## Current Objectives", text, count=1)
        if n == 0:
            continue
        progress_file.write_text(new_text)
        fixes = [f"renamed '{candidate}' → '## Current Objectives'"]
        return parse_objective_files(progress_file, project_path), fixes

    return [], []


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
    """Resolve PROGRESS.md candidate strings to filesystem paths.

    A candidate is either a bare basename (``Foo.lean``) or a project-relative
    path (``Sub/Dir/Foo.lean``). For each:

    1. Search the worktree via ``rglob`` for files matching the candidate,
       skipping ``_SKIP_PARTS`` (lane copies, build dirs, ...). Pick a
       deterministic winner among matches: longest suffix-match against the
       candidate, then shortest path, then lexicographic.
    2. If no existing file matches AND the candidate contains a slash, treat
       it as a project-relative path-to-create — the plan agent marked it as
       a new-file objective and the prover is expected to scaffold it.
       Without this branch, "new file" objectives were silently dropped.
    3. Otherwise warn and skip. The visible log line guarantees a future
       silent-drop can be diagnosed instead of vanishing.

    Output is sorted by path for stable iteration order across dispatch modes.
    """
    results: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate_norm = candidate.replace("\\", "/").strip("/")
        chosen = _best_existing_match(project_path, candidate_norm)
        if chosen is None:
            if "/" in candidate_norm:
                # The slash makes this an intentional path; treat as new file.
                chosen = (project_path / candidate_norm).resolve()
                log.info(
                    f"objective {candidate_norm!r}: no existing file — "
                    f"dispatching as a new-file objective"
                )
            else:
                log.warn(
                    f"objective {candidate_norm!r}: no file matches under "
                    f"{project_path}; skipping. Use a project-relative path "
                    f"(e.g. 'Dir/{candidate_norm}') for files that don't "
                    f"exist yet."
                )
                continue
        resolved = str(chosen)
        if resolved in seen:
            continue
        seen.add(resolved)
        results.append(chosen)
    return sorted(results)


def _best_existing_match(project_path: Path, candidate: str) -> Path | None:
    """Pick the most-specific existing file matching ``candidate``.

    Returns ``None`` when no file matches (the caller decides whether to
    treat the candidate as a new-file objective or to drop it).
    """
    basename = candidate.rsplit("/", 1)[-1]
    matches: list[Path] = []
    for match in project_path.rglob(f"*{basename}"):
        if any(part in _SKIP_PARTS for part in match.parts):
            continue
        if not match.is_file():
            # rglob can yield directories on dir-suffix candidates; filter.
            continue
        try:
            rel = match.resolve().relative_to(project_path.resolve())
        except ValueError:
            # Symlinks pointing outside the worktree; ignore.
            continue
        # Ensure the candidate is a path *suffix* of the match, not just
        # a basename-substring hit. ``rglob("*Foo.lean")`` would match
        # ``MyFoo.lean``; that's a false positive we don't want.
        rel_str = str(rel).replace("\\", "/")
        if not (rel_str == candidate or rel_str.endswith("/" + candidate)):
            continue
        matches.append(match)
    if not matches:
        return None
    # Deterministic tie-break: longer rel-path-suffix match first (favors a
    # match like ``A/B/Foo.lean`` over ``Foo.lean`` when candidate is
    # ``B/Foo.lean``), then shortest absolute path, then lexicographic.
    def _rank(p: Path) -> tuple[int, int, str]:
        rel_str = str(p.resolve().relative_to(project_path.resolve())).replace("\\", "/")
        suffix_len = len(candidate) if rel_str.endswith(candidate) else 0
        return (-suffix_len, len(rel_str), rel_str)
    matches.sort(key=_rank)
    return matches[0]

"""Sorry-count probe for the loop's progress reporting."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .utils import data_path


# A scaffold dispatch (create a file / add new declarations with `sorry`
# bodies) legitimately targets a file with zero open sorries, so it must
# NOT be dropped by the no-op filter. We detect the intent from the
# objective text. Kept in sync with the prose in prompts/plan.md.
_SCAFFOLD_RE = re.compile(
    r"scaffold|skeleton|leave\s+bodies\s+as|declarations?\s+for|"
    r"does\s+not\s+(yet\s+)?exist|stub\s+out|add\s+the\s+import",
    re.IGNORECASE,
)
_LEAN_NAME_RE = re.compile(r"([\w./-]+\.lean)")


def _scaffold_exempt_basenames(progress_file: Path) -> set[str]:
    """Lowercased basenames of `.lean` files the planner intends to scaffold.

    Scanned from each ``## Current Objectives`` line that carries a
    scaffold keyword — those names are exempt from the no-op filter.
    """
    from archon.state.progress import _extract_section

    if not progress_file.exists():
        return set()
    try:
        text = progress_file.read_text()
    except OSError:
        return set()
    names: set[str] = set()
    for line in _extract_section(text, "## Current Objectives"):
        if _SCAFFOLD_RE.search(line):
            for m in _LEAN_NAME_RE.findall(line):
                names.add(Path(m).name.lower())
    return names


def filter_noop_objectives(
    objectives: list[Path],
    *,
    progress_file: Path,
) -> tuple[list[Path], list[Path]]:
    """Drop objective files that would dispatch a no-op prover.

    A file is dropped when it *exists on disk* and has *zero open
    sorries*, unless the planner's objective text marks it as a scaffold
    dispatch. New (non-existent) files and files whose sorry count can't
    be determined are kept — the filter never drops on uncertainty.

    Returns ``(kept, dropped)``.
    """
    exempt = _scaffold_exempt_basenames(progress_file)
    kept: list[Path] = []
    dropped: list[Path] = []
    for obj in objectives:
        if obj.name.lower() in exempt:
            kept.append(obj)
            continue
        if file_open_sorry_count(obj) == 0:
            dropped.append(obj)
        else:
            kept.append(obj)
    return kept, dropped


def file_open_sorry_count(lean_file: Path) -> int | None:
    """Count real `sorry` placeholders in a single `.lean` file.

    Used by plan-validate to detect objective files that the prover
    cannot productively work on (zero open sorries → guaranteed no-op
    dispatch). Reuses the bundled analyzer, which strips comments,
    strings, and nested block comments and counts `sorry`, `sorryAx`,
    and `admit` tokens (excluding those embedded in identifiers); falls
    back to a conservative comment-stripping regex if the analyzer is unavailable.

    Returns ``None`` when the count can't be determined (caller should
    treat unknown as "keep the objective" — never drop on uncertainty).
    """
    if not lean_file.is_file():
        return None

    analyzer = data_path("skills/lean4/lib/scripts/sorry_analyzer.py")
    if analyzer.exists():
        try:
            r = subprocess.run(
                [sys.executable, str(analyzer), str(lean_file),
                 "--format=summary", "--exit-zero-on-findings"],
                capture_output=True, text=True, timeout=30,
            )
            if r.stdout.strip():
                first = r.stdout.strip().splitlines()[0]
                m = re.search(r"Sorry Summary:\s*(\d+)\s+total", first)
                if m:
                    return int(m.group(1))
        except Exception:
            pass

    # Fallback: strip line + nested block comments, then count placeholder
    # tokens not embedded in identifiers. Conservative — on any read
    # error we return None so the caller keeps the objective.
    try:
        text = lean_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    text = re.sub(r"--[^\n]*", "", text)
    # Remove (possibly nested) /- ... -/ blocks iteratively.
    while True:
        stripped = re.sub(r"/-(?:(?!/-|-/).)*?-/", "", text, flags=re.DOTALL)
        if stripped == text:
            break
        text = stripped
    return len(re.findall(r"(?<![A-Za-z0-9_!?'])(?:sorry|sorryAx|admit)(?![A-Za-z0-9_!?'])", text))


def count_sorries(project_path: Path) -> int | None:
    """Count remaining `sorry` placeholders in the project's `.lean` files.

    Tries the bundled analyzer first (it skips comments and strings,
    and counts `sorry`, `sorryAx`, and `admit` tokens); falls back to a coarse `grep` if the
    analyzer isn't available or its output format is unexpected.
    Returns `None` only if both probes fail.
    """
    analyzer = data_path("skills/lean4/lib/scripts/sorry_analyzer.py")
    if analyzer.exists():
        try:
            r = subprocess.run(
                [sys.executable, str(analyzer), str(project_path),
                 "--format=summary", "--exit-zero-on-findings"],
                capture_output=True, text=True, timeout=60,
            )
            if r.stdout.strip():
                first = r.stdout.strip().splitlines()[0]
                m = re.search(r"Sorry Summary:\s*(\d+)\s+total", first)
                if m:
                    return int(m.group(1))
        except Exception:
            pass

    try:
        r = subprocess.run(
            ["bash", "-c",
             "find " + str(project_path) + " -name '*.lean' -not -path '*/.lake/*' "
             "-not -path '*/lake-packages/*' "
             "| xargs grep -c 'sorry' 2>/dev/null "
             "| grep -v ':0$' | awk -F: '{s+=$2} END {print s}'"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip())
    except Exception:
        pass

    return None

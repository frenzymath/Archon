"""Deterministic stage detection for a Lean project.

Replaces the decision tree that was previously embedded in init.md:
  - No declarations yet     → autoformalize
  - Declarations with sorry → prover
  - No sorries              → polish (pending human review → complete)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# Directories inside a Lean project whose .lean files are NOT user code.
_IGNORED_DIR_PARTS = {".lake", "lake-packages", ".git", ".archon", ".claude", "blueprint"}


# Regex for top-level declarations that count as "has formalized content".
# We count: theorem, lemma, def, example, instance, structure, class, inductive.
_DECL_RX = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)?"
    r"(?:private\s+|protected\s+|noncomputable\s+|partial\s+|unsafe\s+)*"
    r"(theorem|lemma|def|example|instance|structure|class|inductive)\b",
    re.MULTILINE,
)

_SORRY_RX = re.compile(r"\bsorry\b")


@dataclass
class StageReport:
    stage: str              # "autoformalize" | "prover" | "polish"
    lean_file_count: int
    decl_count: int
    sorry_count: int
    files_with_sorries: list[Path]


def _is_user_lean_file(path: Path, project_root: Path) -> bool:
    try:
        rel = path.relative_to(project_root)
    except ValueError:
        return False
    return not any(part in _IGNORED_DIR_PARTS for part in rel.parts)


def _strip_comments(text: str) -> str:
    """Remove line comments (`--`) and block comments (`/- ... -/`).

    Good enough for counting sorries and declarations; we're not a parser.
    Handles nested block comments, since Lean allows them.
    """
    # Remove block comments, supporting nesting.
    out = []
    i = 0
    depth = 0
    while i < len(text):
        if depth == 0 and text.startswith("/-", i):
            depth = 1
            i += 2
            continue
        if depth > 0:
            if text.startswith("/-", i):
                depth += 1
                i += 2
                continue
            if text.startswith("-/", i):
                depth -= 1
                i += 2
                continue
            i += 1
            continue
        # depth == 0
        if text.startswith("--", i):
            nl = text.find("\n", i)
            if nl == -1:
                break
            i = nl
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def scan_project(project_root: str | Path) -> StageReport:
    """Walk the project, counting declarations and sorries in user .lean files."""
    root = Path(project_root).resolve()

    lean_files = [
        p for p in root.rglob("*.lean")
        if _is_user_lean_file(p, root)
    ]

    decl_count = 0
    sorry_count = 0
    files_with_sorries: list[Path] = []

    for f in lean_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        stripped = _strip_comments(text)
        decl_count += len(_DECL_RX.findall(stripped))
        file_sorries = len(_SORRY_RX.findall(stripped))
        sorry_count += file_sorries
        if file_sorries > 0:
            files_with_sorries.append(f)

    if decl_count == 0:
        stage = "autoformalize"
    elif sorry_count > 0:
        stage = "prover"
    else:
        stage = "polish"

    return StageReport(
        stage=stage,
        lean_file_count=len(lean_files),
        decl_count=decl_count,
        sorry_count=sorry_count,
        files_with_sorries=files_with_sorries,
    )


def detect_stage(project_root: str | Path) -> str:
    """Convenience: return just the stage string."""
    return scan_project(project_root).stage
"""Read/write archon-protected.yaml — the mathematician's read-only list.

The file lives at the project root and is committed to git. Every agent
(plan, prover, reviewer) must consult it before proposing edits and must
never modify a listed declaration's signature.

Schema:

    path/to/file.lean:
      - declaration_name
      - another_declaration

    path/to/other.lean:
      - some_other_name

Values are always a list of declaration names. No wildcards, no reasons,
no nesting — if it gets more complicated than this, it's the wrong file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PROTECTED_FILENAME = "archon-protected.yaml"


_TEMPLATE_EMPTY = """\
# archon-protected.yaml
# Declarations whose signatures must not be modified by any agent.
# The mathematician owns these; agents are read-only on them.
#
# Format:
#   path/to/file.lean:
#     - declaration_name
#     - another_declaration
#
# Add entries as you decide which parts of the formalization are stable.
# This file is committed to git so the whole team shares it.
#
# Example:
#   DecouplingMomentCurve/Main.lean:
#     - main_decoupling_theorem
#     - decoupling_torsion_curve
#
#   DecouplingMomentCurve/Defs.lean:
#     - HasFourierSupport
#     - decouplingConstant
"""


@dataclass
class ProtectedSet:
    """Parsed representation of archon-protected.yaml."""
    path: Path
    entries: dict[str, list[str]]

    def is_protected(self, rel_lean_path: str, decl_name: str) -> bool:
        return decl_name in self.entries.get(rel_lean_path, [])

    def all_entries(self) -> list[tuple[str, str]]:
        """Flatten to (file, name) pairs for display."""
        return [(f, n) for f, names in self.entries.items() for n in names]

    def total_count(self) -> int:
        return sum(len(v) for v in self.entries.values())


def protected_file_path(project_path: Path) -> Path:
    return project_path / PROTECTED_FILENAME


def exists(project_path: Path) -> bool:
    return protected_file_path(project_path).exists()


def write_template(project_path: Path) -> Path:
    """Write the empty template. Returns the path written."""
    target = protected_file_path(project_path)
    target.write_text(_TEMPLATE_EMPTY, encoding="utf-8")
    return target


def update_path(project_path: Path, old_rel: str, new_rel: str) -> bool:
    """Rename a file key in archon-protected.yaml.

    Used by the refactor agent when it moves a protected declaration from
    `old_rel` to `new_rel`. Declaration names are preserved; only the file
    path under which they are listed changes.

    Merges with any existing entry at `new_rel`, deduplicating declaration
    names. Returns True iff the YAML was modified.
    """
    target = protected_file_path(project_path)
    if not target.exists():
        return False

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return False

    if not isinstance(raw, dict):
        return False

    if old_rel not in raw:
        return False

    old_entries = raw.pop(old_rel)
    if not isinstance(old_entries, list):
        old_entries = []

    merged = list(raw.get(new_rel, []) or [])
    for name in old_entries:
        if isinstance(name, str) and name not in merged:
            merged.append(name)
    raw[new_rel] = merged

    # Preserve the header comments by writing a fresh template header + YAML.
    yaml_body = yaml.safe_dump(raw, sort_keys=True, default_flow_style=False)
    existing = target.read_text(encoding="utf-8")
    header = []
    for line in existing.splitlines():
        if line.startswith("#") or not line.strip():
            header.append(line)
        else:
            break
    new_text = "\n".join(header).rstrip() + "\n\n" + yaml_body
    target.write_text(new_text, encoding="utf-8")
    return True


def load(project_path: Path) -> ProtectedSet:
    """Parse archon-protected.yaml. Silently drops malformed entries.

    Returns an empty ProtectedSet if the file is missing or unparseable.
    """
    target = protected_file_path(project_path)
    if not target.exists():
        return ProtectedSet(path=target, entries={})

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return ProtectedSet(path=target, entries={})

    if not isinstance(raw, dict):
        return ProtectedSet(path=target, entries={})

    entries: dict[str, list[str]] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            entries[k] = v
        # Malformed entries are dropped. We could warn here, but the file
        # is user-edited; better to surface issues via a dedicated
        # `archon protect check` command later than spam at every load.
    return ProtectedSet(path=target, entries=entries)
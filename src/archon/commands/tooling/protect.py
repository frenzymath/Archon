"""Read/write archon-protected.yaml — the mathematician's read-only list.

The file lives at the project root and is committed to git. Every agent
(plan, prover, reviewer, writers) must consult it before proposing edits;
whole-file protection is additionally enforced deterministically by the
subagent dispatch gate (a writer cannot even declare a write-domain that
covers a protected file).

Schema (v2 — the v1 flat form below still parses):

    lean:
      AlgebraicJacobian/Jacobian.lean:
        - AlgebraicGeometry.Jacobian            # string → protect: signature
        - name: AlgebraicGeometry.genus
          protect: all                          # signature (default) | all
        - name: AlgebraicGeometry.Jacobian.*    # fnmatch globs allowed

    blueprint:
      - file: blueprint/src/chapters/Human_*.tex   # whole chapters read-only
      - label: thm:main*                           # protect one env block
        protect: all                               # statement (default) | all

    files:
      - references/my-notes-*.md                   # arbitrary read-only globs

    # v1 (still supported, means lean signature protection):
    path/to/file.lean:
      - declaration_name

Semantics:

* lean ``protect: signature`` — name/type frozen; agents MAY fill the proof
  body (close a ``sorry``). This is the historical behavior.
* lean ``protect: all`` — the whole declaration is frozen, body included:
  agents must not touch it **even to fill a sorry**.
* blueprint ``file:`` — the entire ``.tex`` is mathematician-owned; no agent
  writes it (enforced by the dispatch gate).
* blueprint ``label:`` ``protect: statement`` — the declaration block
  (statement, ``\\label``/``\\lean``/``\\uses`` annotations) is frozen; the
  following ``\\begin{proof}`` body may still be written.
* blueprint ``label:`` ``protect: all`` — statement AND proof frozen.
* ``files:`` — read-only path globs (anything: references, scripts, ...).

Patterns are ``fnmatch`` globs (``*``, ``?``, ``[...]``) — deliberately not
full regex: this file is hand-edited, and globs fail loudly and predictably.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

import typer
import yaml

PROTECTED_FILENAME = "archon-protected.yaml"

_LEAN_LEVELS = ("signature", "all")
_LABEL_LEVELS = ("statement", "all")


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
class LeanRule:
    """One protected Lean declaration (or glob of declarations)."""
    file: str           # project-relative .lean path (exact)
    name: str           # declaration name, fnmatch glob allowed
    level: str          # 'signature' | 'all'


@dataclass
class BlueprintRule:
    """One protected blueprint target."""
    kind: str           # 'file' | 'label'
    pattern: str        # path glob (file) or \label{} glob (label)
    level: str          # file → 'all'; label → 'statement' | 'all'


@dataclass
class ProtectedSet:
    """Parsed representation of archon-protected.yaml (v1 + v2)."""
    path: Path
    entries: dict[str, list[str]]   # legacy view: lean file → decl names
    lean_rules: list[LeanRule] = field(default_factory=list)
    blueprint_rules: list[BlueprintRule] = field(default_factory=list)
    file_rules: list[str] = field(default_factory=list)

    # ── Lean ────────────────────────────────────────────────────────────
    def lean_level(self, rel_lean_path: str, decl_name: str) -> str | None:
        """'all' | 'signature' | None — most restrictive matching rule wins."""
        best: str | None = None
        for r in self.lean_rules:
            if r.file != rel_lean_path:
                continue
            if r.name == decl_name or fnmatch.fnmatchcase(decl_name, r.name):
                if r.level == "all":
                    return "all"
                best = "signature"
        return best

    def is_protected(self, rel_lean_path: str, decl_name: str) -> bool:
        return self.lean_level(rel_lean_path, decl_name) is not None

    # ── Blueprint / files ───────────────────────────────────────────────
    def file_protected(self, rel_path: str) -> bool:
        """True when an agent must not write this file at all."""
        for pat in self.file_rules:
            if fnmatch.fnmatchcase(rel_path, pat):
                return True
        for r in self.blueprint_rules:
            if r.kind == "file" and fnmatch.fnmatchcase(rel_path, r.pattern):
                return True
        return False

    def protected_file_globs(self) -> list[str]:
        """Every whole-file glob (files: + blueprint file: rules)."""
        return self.file_rules + [
            r.pattern for r in self.blueprint_rules if r.kind == "file"
        ]

    def label_level(self, label: str) -> str | None:
        """'all' | 'statement' | None for a blueprint \\label{}."""
        best: str | None = None
        for r in self.blueprint_rules:
            if r.kind != "label":
                continue
            if r.pattern == label or fnmatch.fnmatchcase(label, r.pattern):
                if r.level == "all":
                    return "all"
                best = "statement"
        return best

    # ── Display / reporting ─────────────────────────────────────────────
    def all_entries(self) -> list[tuple[str, str]]:
        """Flatten lean rules to (file, name) pairs for display."""
        return [(r.file, r.name) for r in self.lean_rules]

    def total_count(self) -> int:
        return (len(self.lean_rules) + len(self.blueprint_rules)
                + len(self.file_rules))


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

    # The file key may live at the top level (v1) or under `lean:` (v2).
    container = raw
    if old_rel not in container:
        lean_sec = raw.get("lean")
        if isinstance(lean_sec, dict) and old_rel in lean_sec:
            container = lean_sec
        else:
            return False

    old_entries = container.pop(old_rel)
    if not isinstance(old_entries, list):
        old_entries = []

    merged = list(container.get(new_rel, []) or [])
    for name in old_entries:
        if name not in merged:
            merged.append(name)
    container[new_rel] = merged

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


def _parse_lean_section(raw_lean: object, rules: list[LeanRule],
                        problems: list[str]) -> None:
    if not isinstance(raw_lean, dict):
        problems.append("`lean:` must be a mapping of file → rule list")
        return
    for f, items in raw_lean.items():
        if not isinstance(f, str) or not isinstance(items, list):
            problems.append(f"`lean:` entry {f!r} must map to a list")
            continue
        for it in items:
            if isinstance(it, str):
                rules.append(LeanRule(file=f, name=it, level="signature"))
            elif isinstance(it, dict) and isinstance(it.get("name"), str):
                level = it.get("protect", "signature")
                if level not in _LEAN_LEVELS:
                    problems.append(
                        f"lean {it['name']!r}: protect must be one of "
                        f"{_LEAN_LEVELS}, got {level!r}")
                    level = "signature"
                rules.append(LeanRule(file=f, name=it["name"], level=level))
            else:
                problems.append(f"`lean:` {f}: unrecognized rule {it!r}")


def _parse_blueprint_section(raw_bp: object, rules: list[BlueprintRule],
                             problems: list[str]) -> None:
    if not isinstance(raw_bp, list):
        problems.append("`blueprint:` must be a list of rules")
        return
    for it in raw_bp:
        if not isinstance(it, dict):
            problems.append(f"`blueprint:` unrecognized rule {it!r}")
            continue
        if isinstance(it.get("file"), str):
            rules.append(BlueprintRule(kind="file", pattern=it["file"], level="all"))
        elif isinstance(it.get("label"), str):
            level = it.get("protect", "statement")
            if level not in _LABEL_LEVELS:
                problems.append(
                    f"blueprint label {it['label']!r}: protect must be one of "
                    f"{_LABEL_LEVELS}, got {level!r}")
                level = "statement"
            rules.append(BlueprintRule(kind="label", pattern=it["label"], level=level))
        else:
            problems.append(f"`blueprint:` rule needs `file:` or `label:`: {it!r}")


def protect_check_cli(
    project_path: str = typer.Option(".", "--project-path", help="Path to Lean project"),
) -> None:
    """Validate archon-protected.yaml and show what it actually protects.

    Reports schema problems, resolves every file glob against the tree
    (warning on globs that match nothing), checks each protected Lean
    declaration appears in its file, and resolves label globs against the
    blueprint's \\label{}s.
    """
    import re

    root = Path(project_path).resolve()
    ps, problems = load_with_problems(root)
    if not ps.path.exists():
        print(f"No {PROTECTED_FILENAME} at {root} — nothing is protected.")
        return

    for p in problems:
        print(f"- [schema] {p}")

    # Whole-file globs → concrete matches.
    for pat in ps.protected_file_globs():
        matches = sorted(str(p.relative_to(root)) for p in root.glob(pat) if p.is_file())
        if matches:
            print(f"- [file] `{pat}` protects {len(matches)} file(s): "
                  + ", ".join(matches[:5]) + (" …" if len(matches) > 5 else ""))
        else:
            print(f"- [warn] file glob `{pat}` matches NOTHING")

    # Lean rules → does the declaration (or any glob match) exist in the file?
    for r in ps.lean_rules:
        f = root / r.file
        if not f.is_file():
            print(f"- [warn] lean rule `{r.name}`: file {r.file} does not exist")
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        # Cheap existence probe: the final name component appears in the file.
        tail = r.name.split(".")[-1]
        if "*" in tail or "?" in tail or re.search(rf"\b{re.escape(tail)}\b", text):
            print(f"- [lean:{r.level}] {r.file} :: {r.name}")
        else:
            print(f"- [warn] lean rule `{r.name}` not found in {r.file}")

    # Label rules → resolve against the blueprint's labels.
    chapters = sorted((root / "blueprint" / "src" / "chapters").glob("*.tex"))
    labels: set[str] = set()
    for ch in chapters:
        labels.update(re.findall(r"\\label\{([^{}]+)\}",
                                 ch.read_text(encoding="utf-8", errors="ignore")))
    for r in ps.blueprint_rules:
        if r.kind != "label":
            continue
        hits = sorted(l for l in labels
                      if l == r.pattern or fnmatch.fnmatchcase(l, r.pattern))
        if hits:
            print(f"- [label:{r.level}] `{r.pattern}` protects {len(hits)} block(s): "
                  + ", ".join(hits[:5]) + (" …" if len(hits) > 5 else ""))
        else:
            print(f"- [warn] label glob `{r.pattern}` matches no \\label{{}}")

    if problems:
        raise typer.Exit(1)


def load(project_path: Path) -> ProtectedSet:
    """Parse archon-protected.yaml (v1 flat form and v2 sections).

    Silently drops malformed entries (run ``archon protect-check`` to see
    them). Returns an empty ProtectedSet if the file is missing/unparseable.
    """
    ps, _problems = load_with_problems(project_path)
    return ps


def load_with_problems(project_path: Path) -> tuple[ProtectedSet, list[str]]:
    """Like :func:`load`, but also returns the schema problems found."""
    target = protected_file_path(project_path)
    empty = ProtectedSet(path=target, entries={})
    if not target.exists():
        return empty, []

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return empty, [f"YAML parse error: {e}"]

    if not isinstance(raw, dict):
        return empty, ["top level must be a mapping"]

    problems: list[str] = []
    lean_rules: list[LeanRule] = []
    blueprint_rules: list[BlueprintRule] = []
    file_rules: list[str] = []

    for k, v in raw.items():
        if not isinstance(k, str):
            problems.append(f"non-string top-level key {k!r}")
            continue
        if k == "lean":
            _parse_lean_section(v, lean_rules, problems)
        elif k == "blueprint":
            _parse_blueprint_section(v, blueprint_rules, problems)
        elif k == "files":
            if isinstance(v, list) and all(isinstance(x, str) for x in v):
                file_rules.extend(v)
            else:
                problems.append("`files:` must be a list of path globs")
        elif isinstance(v, list) and all(isinstance(x, str) for x in v):
            # v1 flat form: file.lean → [decl names] = signature protection.
            for name in v:
                lean_rules.append(LeanRule(file=k, name=name, level="signature"))
        else:
            problems.append(f"unrecognized entry {k!r}")

    # Legacy view (prompts/report consumers): lean file → names.
    entries: dict[str, list[str]] = {}
    for r in lean_rules:
        entries.setdefault(r.file, []).append(r.name)

    return ProtectedSet(
        path=target, entries=entries,
        lean_rules=lean_rules, blueprint_rules=blueprint_rules,
        file_rules=file_rules,
    ), problems
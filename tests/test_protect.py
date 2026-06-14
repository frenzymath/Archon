"""Tests for archon-protected.yaml v2: schema, levels, globs, dispatch gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from archon.commands.tooling import protect
from archon.subagents.base import (
    WriteDomainViolation,
    assert_write_domain_not_protected,
)


def _write_yaml(root: Path, text: str) -> None:
    (root / protect.PROTECTED_FILENAME).write_text(text, encoding="utf-8")


# ── v1 back-compat ────────────────────────────────────────────────────────────

def test_v1_flat_form_is_signature_protection(tmp_path: Path):
    _write_yaml(tmp_path, "Foo/Bar.lean:\n  - My.decl\n  - My.other\n")
    ps = protect.load(tmp_path)
    assert ps.is_protected("Foo/Bar.lean", "My.decl")
    assert ps.lean_level("Foo/Bar.lean", "My.decl") == "signature"
    assert ps.lean_level("Foo/Bar.lean", "My.unknown") is None
    assert ps.entries == {"Foo/Bar.lean": ["My.decl", "My.other"]}


# ── v2 lean section ───────────────────────────────────────────────────────────

def test_v2_lean_levels_and_globs(tmp_path: Path):
    _write_yaml(tmp_path, """\
lean:
  Foo/Bar.lean:
    - My.sigOnly
    - name: My.frozen
      protect: all
    - name: My.Jacobian.*
      protect: all
""")
    ps = protect.load(tmp_path)
    assert ps.lean_level("Foo/Bar.lean", "My.sigOnly") == "signature"
    assert ps.lean_level("Foo/Bar.lean", "My.frozen") == "all"
    assert ps.lean_level("Foo/Bar.lean", "My.Jacobian.ofCurve") == "all"
    assert ps.lean_level("Other.lean", "My.frozen") is None
    # 'all' wins when both a glob-signature and an exact-all rule match.
    assert ps.is_protected("Foo/Bar.lean", "My.Jacobian.ofCurve")


def test_v2_bad_level_degrades_with_problem(tmp_path: Path):
    _write_yaml(tmp_path, """\
lean:
  Foo.lean:
    - name: My.decl
      protect: everything
""")
    ps, problems = protect.load_with_problems(tmp_path)
    assert ps.lean_level("Foo.lean", "My.decl") == "signature"
    assert any("protect must be one of" in p for p in problems)


# ── v2 blueprint + files sections ────────────────────────────────────────────

def test_blueprint_file_and_label_rules(tmp_path: Path):
    _write_yaml(tmp_path, """\
blueprint:
  - file: blueprint/src/chapters/Human_*.tex
  - label: thm:main*
    protect: all
  - label: def:ownnotion
files:
  - references/my-notes-*.md
""")
    ps = protect.load(tmp_path)
    assert ps.file_protected("blueprint/src/chapters/Human_Jacobian.tex")
    assert not ps.file_protected("blueprint/src/chapters/Picard.tex")
    assert ps.file_protected("references/my-notes-v2.md")
    assert ps.label_level("thm:main_theorem") == "all"
    assert ps.label_level("def:ownnotion") == "statement"
    assert ps.label_level("lem:helper") is None
    assert set(ps.protected_file_globs()) == {
        "blueprint/src/chapters/Human_*.tex", "references/my-notes-*.md",
    }


def test_mixed_v1_v2(tmp_path: Path):
    _write_yaml(tmp_path, """\
Foo.lean:
  - Old.style
lean:
  Bar.lean:
    - name: New.style
      protect: all
files:
  - keep/*.md
""")
    ps = protect.load(tmp_path)
    assert ps.lean_level("Foo.lean", "Old.style") == "signature"
    assert ps.lean_level("Bar.lean", "New.style") == "all"
    assert ps.file_protected("keep/notes.md")
    assert ps.total_count() == 3


# ── dispatch gate ─────────────────────────────────────────────────────────────

def _project_with_protected_chapter(tmp_path: Path) -> Path:
    ch = tmp_path / "blueprint" / "src" / "chapters"
    ch.mkdir(parents=True)
    (ch / "Human_Jacobian.tex").write_text("x", encoding="utf-8")
    (ch / "Picard.tex").write_text("y", encoding="utf-8")
    _write_yaml(tmp_path, """\
blueprint:
  - file: blueprint/src/chapters/Human_*.tex
""")
    return tmp_path


def test_gate_rejects_domain_covering_protected_file(tmp_path: Path):
    root = _project_with_protected_chapter(tmp_path)
    with pytest.raises(WriteDomainViolation, match="Human_Jacobian"):
        assert_write_domain_not_protected(
            root, ["blueprint/src/chapters/*.tex"],
        )
    with pytest.raises(WriteDomainViolation):
        assert_write_domain_not_protected(
            root, ["blueprint/src/chapters/Human_Jacobian.tex"],
        )


def test_gate_allows_disjoint_domain(tmp_path: Path):
    root = _project_with_protected_chapter(tmp_path)
    # Specific unprotected chapter: fine.
    assert_write_domain_not_protected(
        root, ["blueprint/src/chapters/Picard.tex"],
    )
    # Lean-only domain: fine.
    assert_write_domain_not_protected(root, ["*.lean", "Foo/**"])
    # No domain at all (read-only agent): fine.
    assert_write_domain_not_protected(root, [])


def test_gate_noop_without_yaml(tmp_path: Path):
    assert_write_domain_not_protected(tmp_path, ["**"])

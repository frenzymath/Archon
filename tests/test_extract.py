"""Tests for `archon extract`: cone queries, carve plan, duplication, verify gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from archon.commands.dag.leandag_gaps import (
    compute_carve_plan,
    run_query,
)
from archon.commands.extract.command import ExtractCommand
from archon.commands.extract.duplicate import MANIFEST_NAME, duplicate_project
from archon.commands.extract.verify import verify_sandbox


# ── fixture project ──────────────────────────────────────────────────────────
# Graph:  thm:goal ──uses──▶ def:a1            (cone of thm:goal = both)
#         def:a2, def:d1     out of cone
#         MyLib.c1           lean_aux, no blueprint entry
# Files:  B.lean {goal}      imports A.lean    → keep
#         A.lean {a1, a2}    imports C.lean    → mixed (a2 out)
#         C.lean {c1}        lean_aux only     → imported (A imports it)
#         D.lean {d1}                          → drop


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def fixture_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    _write(proj / "MyLib.lean",
           "import MyLib.A\nimport MyLib.B\nimport MyLib.C\nimport MyLib.D\n")
    _write(proj / "MyLib" / "A.lean",
           "import MyLib.C\n\nnamespace MyLib\n\n"
           "def a1 : Nat := 1\n\ndef a2 : Nat := 2\n\nend MyLib\n")
    _write(proj / "MyLib" / "B.lean",
           "import MyLib.A\n\nnamespace MyLib\n\n"
           "theorem goal : True := trivial\n\nend MyLib\n")
    _write(proj / "MyLib" / "C.lean",
           "namespace MyLib\n\ndef c1 : Nat := 3\n\nend MyLib\n")
    _write(proj / "MyLib" / "D.lean",
           "namespace MyLib\n\ndef d1 : Nat := 4\n\nend MyLib\n")

    _write(proj / "blueprint" / "src" / "content.tex",
           "\\input{chapters/A}\n\\input{chapters/B}\n\\input{chapters/D}\n")
    _write(proj / "blueprint" / "src" / "chapters" / "A.tex",
           "\\chapter{A}\n"
           "\\begin{definition}\\label{def:a1}\\lean{MyLib.a1}\\leanok\n"
           "One.\\end{definition}\n"
           "\\begin{definition}\\label{def:a2}\\lean{MyLib.a2}\\leanok\n"
           "Two.\\end{definition}\n")
    _write(proj / "blueprint" / "src" / "chapters" / "B.tex",
           "\\chapter{B}\n"
           "\\begin{theorem}\\label{thm:goal}\\lean{MyLib.goal}\\uses{def:a1}\n"
           "Goal.\\end{theorem}\n"
           "\\begin{proof}Trivial.\\end{proof}\n")
    _write(proj / "blueprint" / "src" / "chapters" / "D.tex",
           "\\chapter{D}\n"
           "\\begin{definition}\\label{def:d1}\\lean{MyLib.d1}\\leanok\n"
           "Four.\\end{definition}\n")

    # archon state (knowledge + execution state, to test selective copy)
    _write(proj / ".archon" / "PROGRESS.md", "## Current Objectives\n- prove goal\n")
    _write(proj / ".archon" / "STRATEGY.md", "# Strategy\narc\n")
    _write(proj / ".archon" / "task_pending.md", "EXECUTION STATE\n")
    _write(proj / ".archon" / "prompts" / "plan.md", "plan prompt\n")
    _write(proj / ".archon" / "iter" / "iter-001" / "dag.md", "old narrative\n")
    _write(proj / ".archon" / "git-dir" / "HEAD", "ref: refs/heads/main\n")
    _write(proj / ".lake" / "build" / "lib" / "MyLib.olean", "fake-olean")
    _write(proj / "lakefile.toml", 'name = "MyLib"\n')
    _write(proj / "lean-toolchain", "leanprover/lean4:v4.x\n")
    return proj


# ── cone verb ────────────────────────────────────────────────────────────────

def test_cone_closure_includes_seed_and_ancestors(fixture_project: Path):
    res = run_query(fixture_project, "cone", node="thm:goal", limit=0)
    assert res["error"] is None
    ids = {n["id"] for n in res["nodes"]}
    assert ids == {"thm:goal", "def:a1"}


def test_cone_complement_excludes_closure(fixture_project: Path):
    res = run_query(fixture_project, "cone", node="thm:goal",
                    limit=0, complement=True)
    assert res["error"] is None
    ids = {n["id"] for n in res["nodes"]}
    assert "thm:goal" not in ids and "def:a1" not in ids
    assert {"def:a2", "def:d1"} <= ids


def test_cone_multi_seed_and_missing_seed(fixture_project: Path):
    res = run_query(fixture_project, "cone", node="thm:goal,def:d1", limit=0)
    assert res["error"] is None
    assert {n["id"] for n in res["nodes"]} == {"thm:goal", "def:a1", "def:d1"}

    bad = run_query(fixture_project, "cone", node="thm:nope")
    assert bad["error"] and "thm:nope" in bad["error"]


def test_complement_requires_cone_verb(fixture_project: Path):
    res = run_query(fixture_project, "roots", complement=True)
    assert res["error"]


# ── interface / overlap verbs (decomposition) ────────────────────────────────

def test_interface_returns_direct_deps(fixture_project: Path):
    # thm:goal directly \uses def:a1 (and nothing else); def:a1 is the interface.
    res = run_query(fixture_project, "interface", node="thm:goal", limit=0)
    assert res["error"] is None
    assert {n["id"] for n in res["nodes"]} == {"def:a1"}


def test_interface_requires_node(fixture_project: Path):
    assert run_query(fixture_project, "interface")["error"]


def test_overlap_shared_closure(fixture_project: Path):
    res = run_query(fixture_project, "overlap", node="thm:goal",
                    vs="def:a1", limit=0)
    assert res["error"] is None
    assert {n["id"] for n in res["nodes"]} == {"def:a1"}


def test_overlap_disjoint_is_empty(fixture_project: Path):
    res = run_query(fixture_project, "overlap", node="thm:goal",
                    vs="def:d1", limit=0)
    assert res["error"] is None
    assert res["nodes"] == []


def test_overlap_requires_vs(fixture_project: Path):
    res = run_query(fixture_project, "overlap", node="thm:goal")
    assert res["error"] and "vs" in res["error"]


def test_vs_rejected_outside_overlap(fixture_project: Path):
    res = run_query(fixture_project, "cone", node="thm:goal", vs="def:a1")
    assert res["error"]


# ── carve plan ───────────────────────────────────────────────────────────────

def test_carve_plan_statuses(fixture_project: Path):
    plan = compute_carve_plan(fixture_project, ["thm:goal"])
    assert plan["error"] is None
    assert plan["closure_size"] == 2

    lean = {d["file"]: d for d in plan["lean_files"]}
    assert lean["MyLib/B.lean"]["status"] == "keep"
    assert lean["MyLib/A.lean"]["status"] == "mixed"
    assert lean["MyLib/A.lean"]["out_blueprint"] == ["def:a2"]
    # C.lean has no cone node but A.lean (mixed → kept) imports it.
    assert lean["MyLib/C.lean"]["status"] == "imported"
    assert "MyLib/A.lean" in lean["MyLib/C.lean"]["imported_by"]
    assert lean["MyLib/D.lean"]["status"] == "drop"

    tex = {d["file"]: d for d in plan["tex_files"]}
    tex_by_name = {Path(f).name: d for f, d in tex.items()}
    assert tex_by_name["B.tex"]["status"] == "keep"
    assert tex_by_name["A.tex"]["status"] == "mixed"
    assert tex_by_name["D.tex"]["status"] == "drop"


def test_carve_plan_unknown_seed(fixture_project: Path):
    plan = compute_carve_plan(fixture_project, ["thm:nope"])
    assert plan["error"] and "thm:nope" in plan["error"]


# ── duplication ──────────────────────────────────────────────────────────────

def test_duplicate_copies_sources_and_filters_state(fixture_project: Path, tmp_path: Path):
    dest = tmp_path / "sub"
    report = duplicate_project(fixture_project, dest, lake_mode="hardlink")

    # Sources verbatim.
    assert (dest / "MyLib" / "A.lean").read_text() == \
        (fixture_project / "MyLib" / "A.lean").read_text()
    assert (dest / "blueprint" / "src" / "chapters" / "B.tex").is_file()
    assert (dest / "lean-toolchain").is_file()

    # Knowledge carried, execution state left behind.
    assert (dest / ".archon" / "PROGRESS.md").is_file()
    assert (dest / ".archon" / "STRATEGY.md").is_file()
    assert (dest / ".archon" / "prompts" / "plan.md").is_file()
    assert not (dest / ".archon" / "task_pending.md").exists()
    assert not (dest / ".archon" / "iter").exists()

    # Fresh inner git, not the parent's.
    assert (dest / ".archon" / "git-dir").is_dir()
    head = (dest / ".archon" / "git-dir" / "HEAD").read_text()
    assert "refs/heads" in head

    # .lake hardlinked (same inode ⇒ no extra disk).
    src_olean = fixture_project / ".lake" / "build" / "lib" / "MyLib.olean"
    dst_olean = dest / ".lake" / "build" / "lib" / "MyLib.olean"
    assert dst_olean.is_file()
    assert dst_olean.stat().st_ino == src_olean.stat().st_ino

    # Manifest written with provenance + hashes.
    manifest = json.loads(report.manifest_path.read_text())
    assert manifest["parent"] == str(fixture_project)
    assert manifest["seeds"] == []
    assert "MyLib/A.lean" in manifest["files"]
    assert ".lake" not in " ".join(manifest["files"])


def test_duplicate_refuses_nonempty_dest(fixture_project: Path, tmp_path: Path):
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "x").write_text("y")
    with pytest.raises(FileExistsError):
        duplicate_project(fixture_project, dest)


def test_duplicate_refuses_nested_dest(fixture_project: Path):
    with pytest.raises(ValueError):
        duplicate_project(fixture_project, fixture_project / "inner")


# ── verify gate ──────────────────────────────────────────────────────────────

def _carved_sandbox(fixture_project: Path, tmp_path: Path) -> Path:
    """Duplicate + minimal carve (drop D, record scope in manifest)."""
    dest = tmp_path / "carved"
    report = duplicate_project(fixture_project, dest, lake_mode="none")
    manifest = json.loads(report.manifest_path.read_text())
    manifest["seeds"] = ["thm:goal"]
    manifest["closure"] = ["thm:goal", "def:a1"]
    report.manifest_path.write_text(json.dumps(manifest))
    # the carve: drop D.lean + D.tex, fix content.tex
    (dest / "MyLib" / "D.lean").unlink()
    (dest / "blueprint" / "src" / "chapters" / "D.tex").unlink()
    (dest / "blueprint" / "src" / "content.tex").write_text(
        "\\input{chapters/A}\n\\input{chapters/B}\n")
    return dest


def test_verify_gate_passes_on_clean_carve(fixture_project: Path, tmp_path: Path):
    dest = _carved_sandbox(fixture_project, tmp_path)
    res = verify_sandbox(dest)
    assert res.ok, res.problems


def test_verify_gate_fails_without_seeds(fixture_project: Path, tmp_path: Path):
    dest = tmp_path / "noseeds"
    duplicate_project(fixture_project, dest, lake_mode="none")
    res = verify_sandbox(dest)
    assert not res.ok
    assert any("no seeds" in p for p in res.problems)


def test_verify_gate_fails_when_closure_node_lost(fixture_project: Path, tmp_path: Path):
    dest = _carved_sandbox(fixture_project, tmp_path)
    # Overcarve: delete def:a1's blueprint block (cuts INTO the cone).
    a_tex = dest / "blueprint" / "src" / "chapters" / "A.tex"
    text = a_tex.read_text()
    start = text.index("\\begin{definition}\\label{def:a1}")
    end = text.index("\\end{definition}", start) + len("\\end{definition}")
    a_tex.write_text(text[:start] + text[end:])

    res = verify_sandbox(dest)
    assert not res.ok
    joined = " ".join(res.problems)
    assert "def:a1" in joined


# ── merge mode ───────────────────────────────────────────────────────────────

def test_duplicate_merge_mode_records_fields(fixture_project: Path, tmp_path: Path):
    # A second archon project to merge from (read-only; never copied).
    source = tmp_path / "source"
    source.mkdir()
    (source / ".archon").mkdir()
    dest = tmp_path / "merged"

    report = duplicate_project(
        fixture_project, dest, lake_mode="none",
        mode="merge", merge_source=source, union=True, prefer="target",
    )
    manifest = json.loads(report.manifest_path.read_text())
    assert manifest["mode"] == "merge"
    assert manifest["merge_source"] == str(source)
    assert manifest["union"] is True
    assert manifest["prefer"] == "target"
    assert manifest["overlaps"] == []
    # The source is mounted read-only — duplication must not copy it in.
    assert not (dest / "source").exists()


def test_extract_mode_manifest_has_merge_defaults(fixture_project: Path, tmp_path: Path):
    dest = tmp_path / "sub"
    report = duplicate_project(fixture_project, dest, lake_mode="none")
    manifest = json.loads(report.manifest_path.read_text())
    assert manifest["mode"] == "extract"
    assert manifest["merge_source"] is None
    assert manifest["union"] is False
    assert manifest["prefer"] == "source"


def test_verify_merge_mode_message_on_lost_closure(fixture_project: Path, tmp_path: Path):
    dest = _carved_sandbox(fixture_project, tmp_path)
    a_tex = dest / "blueprint" / "src" / "chapters" / "A.tex"
    text = a_tex.read_text()
    start = text.index("\\begin{definition}\\label{def:a1}")
    end = text.index("\\end{definition}", start) + len("\\end{definition}")
    a_tex.write_text(text[:start] + text[end:])

    res = verify_sandbox(dest, mode="merge")
    assert not res.ok
    joined = " ".join(res.problems)
    assert "missing after import" in joined
    assert "lost in the carve" not in joined


def test_command_mode_derived_from_merge_source(tmp_path: Path):
    extract_cmd = ExtractCommand("a", "b")
    assert extract_cmd.mode == "extract"
    merge_cmd = ExtractCommand("a", "b", merge_source="c", union=True, prefer="target")
    assert merge_cmd.mode == "merge"
    assert merge_cmd.union is True
    assert merge_cmd.prefer == "target"


def test_extract_command_passes_harness_override(monkeypatch, tmp_path: Path):
    seen = {}

    class DummyRunner:
        def run_interactive(self, prompt, *, cwd):
            seen["prompt"] = prompt
            seen["cwd"] = cwd
            return 0

    def fake_build_runner(**kwargs):
        seen["runner_kwargs"] = kwargs
        return DummyRunner()

    monkeypatch.setattr("archon.commands.extract.command.build_runner", fake_build_runner)
    monkeypatch.setattr(ExtractCommand, "_preflight", lambda self: None)
    monkeypatch.setattr(ExtractCommand, "_build_prompt", lambda self: "PROMPT")
    monkeypatch.setattr(ExtractCommand, "_gate_and_finalize", lambda self: None)

    cmd = ExtractCommand(
        str(tmp_path / "parent"),
        str(tmp_path / "dest"),
        resume=True,
        harness="codex",
    )
    cmd.run()

    assert seen["runner_kwargs"]["role"] == "extract"
    assert seen["runner_kwargs"]["harness"] == "codex"
    assert seen["prompt"] == "PROMPT"
    assert seen["cwd"] == tmp_path / "dest"


def test_merge_command_registered():
    from archon.commands.extract import merge
    assert callable(merge)


# ── sibling-extract discovery (decomposition) ────────────────────────────────

def _make_extract_dir(root: Path, name: str, parent: Path, seeds, closure):
    d = root / name
    (d / ".archon").mkdir(parents=True)
    (d / ".archon" / MANIFEST_NAME).write_text(json.dumps({
        "mode": "extract", "parent": str(parent),
        "seeds": seeds, "closure": closure,
    }))
    return d


def test_find_sibling_extracts(tmp_path: Path):
    from archon.commands.extract.command import find_sibling_extracts
    parent = tmp_path / "parent"
    parent.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    me = _make_extract_dir(work, "mine", parent, ["thm:a"], ["thm:a", "def:b"])
    _make_extract_dir(work, "cech", parent, ["thm:cech"], ["thm:cech"])
    # An extract of a DIFFERENT parent must not be picked up.
    _make_extract_dir(work, "other", tmp_path / "elsewhere", ["thm:z"], ["thm:z"])

    sibs = find_sibling_extracts(parent, exclude=me)
    names = {s["name"] for s in sibs}
    assert names == {"cech"}                       # excludes self + foreign parent
    cech = next(s for s in sibs if s["name"] == "cech")
    assert cech["seeds"] == ["thm:cech"] and cech["closure_size"] == 1


def test_find_sibling_extracts_none(tmp_path: Path):
    from archon.commands.extract.command import find_sibling_extracts
    parent = tmp_path / "p"
    parent.mkdir()
    dest = tmp_path / "d"
    dest.mkdir()
    assert find_sibling_extracts(parent, exclude=dest) == []


# ── declaration-level carve (rider-decls, dead riders, other files) ──────────
# A second fixture that exercises Lean symbol dependencies the blueprint graph
# cannot see:
#   thm:goal ──uses──▶ def:a1            (cone)
#   def:a1's Lean body calls MyLib.a2 (out of cone) and MyLib.helper (lean_aux)
#   C.lean has helper (referenced ⇒ live) and junk (referenced by nobody ⇒ dead)
# So a2 is a *rider-decl* (keep code, drop blueprint block), helper rides along,
# junk is a *dead rider*, and MyLib.lean (root, no decls) is an *aggregator*.

@pytest.fixture()
def decl_fixture(tmp_path: Path) -> Path:
    proj = tmp_path / "decl"
    _write(proj / "MyLib.lean", "import MyLib.A\nimport MyLib.B\nimport MyLib.C\n")
    _write(proj / "MyLib" / "A.lean",
           "import MyLib.C\n\nnamespace MyLib\n\n"
           "def a1 : Nat := MyLib.a2 + MyLib.helper\n\n"
           "def a2 : Nat := 2\n\nend MyLib\n")
    _write(proj / "MyLib" / "B.lean",
           "import MyLib.A\n\nnamespace MyLib\n\n"
           "theorem goal : True := trivial\n\nend MyLib\n")
    _write(proj / "MyLib" / "C.lean",
           "namespace MyLib\n\ndef helper : Nat := 7\n\n"
           "def junk : Nat := 99\n\nend MyLib\n")

    _write(proj / "blueprint" / "src" / "content.tex",
           "\\input{chapters/A}\n\\input{chapters/B}\n")
    _write(proj / "blueprint" / "src" / "chapters" / "A.tex",
           "\\chapter{A}\n"
           "\\begin{definition}\\label{def:a1}\\lean{MyLib.a1}\\leanok\n"
           "One.\\end{definition}\n"
           "\\begin{definition}\\label{def:a2}\\lean{MyLib.a2}\\leanok\n"
           "Two.\\end{definition}\n")
    _write(proj / "blueprint" / "src" / "chapters" / "B.tex",
           "\\chapter{B}\n"
           "\\begin{theorem}\\label{thm:goal}\\lean{MyLib.goal}\\uses{def:a1}\n"
           "Goal.\\end{theorem}\n"
           "\\begin{proof}Trivial.\\end{proof}\n")
    _write(proj / "lakefile.toml", 'name = "MyLib"\n')
    _write(proj / "lean-toolchain", "leanprover/lean4:v4.x\n")
    return proj


def test_rider_decl_kept_not_carved(decl_fixture: Path):
    plan = compute_carve_plan(decl_fixture, ["thm:goal"])
    assert plan["error"] is None
    a = {d["file"]: d for d in plan["lean_files"]}["MyLib/A.lean"]
    assert a["status"] == "mixed"
    # def:a2 is out of cone but def:a1 (kept) calls it in Lean → keep the code,
    # so it must NOT be in the true-carve list, it must be a rider-decl.
    assert a["out_blueprint"] == []
    assert a["rider_decls"] == ["def:a2"]


def test_dead_rider_flagged_live_aux_spared(decl_fixture: Path):
    plan = compute_carve_plan(decl_fixture, ["thm:goal"])
    c = {d["file"]: d for d in plan["lean_files"]}["MyLib/C.lean"]
    assert c["status"] == "imported"           # A.lean imports it
    assert c["dead_riders"] == ["lean:MyLib.junk"]   # referenced by nobody
    # helper IS referenced by a1, so it must not be flagged dead.
    assert "lean:MyLib.helper" not in c["dead_riders"]


def test_lean_closure_size_reported(decl_fixture: Path):
    plan = compute_carve_plan(decl_fixture, ["thm:goal"])
    # thm:goal, def:a1, def:a2 (rider), lean:MyLib.helper (aux) = 4.
    assert plan["lean_closure_size"] == 4


def test_other_lean_files_root_is_aggregator(decl_fixture: Path):
    plan = compute_carve_plan(decl_fixture, ["thm:goal"])
    other = {d["file"]: d for d in plan["other_lean_files"]}
    assert "MyLib.lean" in other  # root file leandag makes no node for
    assert other["MyLib.lean"]["status"] == "aggregator"


def test_other_lean_files_dead_barrel_dropped(decl_fixture: Path):
    # A shim importing only a dropped/unknown module is a drop, not freehanded.
    _write(decl_fixture / "MyLib" / "Orphan.lean",
           "import MyLib.Nonexistent\n")
    plan = compute_carve_plan(decl_fixture, ["thm:goal"])
    other = {d["file"]: d for d in plan["other_lean_files"]}
    assert other["MyLib/Orphan.lean"]["status"] == "drop"


def test_carve_plan_text_renders_riders_and_others(decl_fixture: Path):
    from archon.commands.dag.leandag_gaps import format_carve_plan_text
    txt = format_carve_plan_text(compute_carve_plan(decl_fixture, ["thm:goal"]))
    assert "rider" in txt.lower()
    assert "Other Lean files" in txt
    assert "lean-needed" in txt


# ── query truncation must be loud, not silent ────────────────────────────────

def test_run_query_marks_truncation(fixture_project: Path):
    res = run_query(fixture_project, "cone", node="thm:goal", limit=1)
    assert res["total"] == 2 and res["count"] == 1
    assert res["truncated"] is True


def test_run_query_untruncated_when_unlimited(fixture_project: Path):
    res = run_query(fixture_project, "cone", node="thm:goal", limit=0)
    assert res["truncated"] is False


def test_query_text_shouts_truncation(fixture_project: Path):
    from archon.commands.dag.leandag_gaps import format_query_text
    txt = format_query_text(run_query(fixture_project, "cone",
                                      node="thm:goal", limit=1))
    assert "TRUNCATED" in txt


# ── parent-vs-child quality regression gate ──────────────────────────────────

def _parent_graph():
    # thm:x finite-effort & sorry-free upstream; thm:y likewise (the control).
    return {"nodes": [
        {"id": "thm:x", "type": "theorem", "lean_name": "Lib.x",
         "effort_local": 12, "has_sorry": False},
        {"id": "thm:y", "type": "theorem", "lean_name": "Lib.y",
         "effort_local": 5, "has_sorry": False},
    ], "edges": [], "meta": {}, "error": None}


def _child(type_, eff, sorry, *, lean_name, id_):
    from types import SimpleNamespace
    return SimpleNamespace(id=id_, type=type_, lean_name=lean_name,
                           effort_local=eff, has_sorry=sorry)


def test_parent_regression_detects_degradation(tmp_path: Path, monkeypatch):
    from archon.commands.extract import verify as V
    monkeypatch.setattr(V, "build_graph_at_commit", lambda p, s: _parent_graph())
    # x degraded to a sorried lean_aux (chapter dropped); y is healthy. x is
    # IN SCOPE (a recorded seed) so the degradation is a real regression.
    child = [
        _child("lean_aux", None, True, lean_name="Lib.x", id_="lean:Lib.x"),
        _child("theorem", 5, False, lean_name="Lib.y", id_="thm:y"),
    ]
    manifest = {"parent": str(tmp_path), "parent_inner_head": "deadbeef",
                "seeds": ["thm:x"], "closure": ["thm:y"]}
    probs, compared = V._parent_regressions(tmp_path, manifest, child)
    assert compared is True
    assert any("Lib.x" in p for p in probs)
    assert not any("Lib.y" in p for p in probs)


def test_parent_regression_respects_rider_whitelist(tmp_path: Path, monkeypatch):
    from archon.commands.extract import verify as V
    monkeypatch.setattr(V, "build_graph_at_commit", lambda p, s: _parent_graph())
    child = [_child("lean_aux", None, True, lean_name="Lib.x", id_="lean:Lib.x")]
    # x is in scope AND whitelisted — the rider whitelist suppresses it.
    manifest = {"parent": str(tmp_path), "parent_inner_head": "d",
                "seeds": ["thm:x"], "riders": ["Lib.x"]}
    probs, compared = V._parent_regressions(tmp_path, manifest, child)
    assert compared is True and probs == []


def test_parent_regression_ignores_out_of_scope_rider(tmp_path: Path, monkeypatch):
    """An out-of-scope dependency kept as compile-time Lean degrading to a bare
    lean_aux (chapter dropped) is the expected outcome of a focused extract —
    it must NOT trip the gate even though it degraded vs the parent."""
    from archon.commands.extract import verify as V
    monkeypatch.setattr(V, "build_graph_at_commit", lambda p, s: _parent_graph())
    # x degraded, but only y is in the agreed scope. x is an out-of-scope rider.
    child = [
        _child("lean_aux", None, True, lean_name="Lib.x", id_="lean:Lib.x"),
        _child("theorem", 5, False, lean_name="Lib.y", id_="thm:y"),
    ]
    manifest = {"parent": str(tmp_path), "parent_inner_head": "d",
                "seeds": ["thm:y"]}
    probs, compared = V._parent_regressions(tmp_path, manifest, child)
    assert compared is True
    assert probs == []  # x degradation is expected, not flagged


def test_parent_regression_skips_when_no_parent_commit(tmp_path: Path):
    from archon.commands.extract import verify as V
    probs, compared = V._parent_regressions(tmp_path, {"parent": str(tmp_path)}, [])
    assert compared is False and probs == []


def test_verify_gate_degrades_gracefully_without_real_parent_git(
    fixture_project: Path, tmp_path: Path,
):
    # The fixture's inner git is a stub, so the regression compare can't run —
    # the gate must still pass and record that no comparison happened.
    dest = _carved_sandbox(fixture_project, tmp_path)
    res = verify_sandbox(dest)
    assert res.ok, res.problems
    assert res.stats.get("parent_compared") is False

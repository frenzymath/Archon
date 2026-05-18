"""Tests for the subagent registry + descriptor parsing.

Covers:

* YAML frontmatter parsing and required-field enforcement
* Filename / frontmatter name agreement
* Project-local overrides shadow built-in defaults
* ``enabled``-list filter and ``default_enabled`` fallback
* CLI surfacing of unknown-name errors
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.subagents.base import SubagentDescriptor
from archon.subagents.registry import (
    SubagentRegistry,
    build_registry,
    load_descriptors_from_dir,
    parse_descriptor_file,
)


# ── descriptor parsing ──────────────────────────────────────────────


def _write_descriptor(
    dir: Path, name: str, frontmatter: dict | str | None = None,
    body: str = "Body content.",
) -> Path:
    """Write a descriptor file. ``frontmatter`` may be a dict (rendered to
    YAML), a raw string (inserted between the ``---`` lines), or None (no
    frontmatter block at all)."""
    import yaml as _yaml
    path = dir / f"{name}.md"
    if frontmatter is None:
        content = body
    elif isinstance(frontmatter, dict):
        content = "---\n" + _yaml.safe_dump(frontmatter) + "---\n\n" + body
    else:
        content = "---\n" + frontmatter.rstrip() + "\n---\n\n" + body
    path.write_text(content, encoding="utf-8")
    return path


class ParseDescriptorTest(unittest.TestCase):
    def test_minimal_descriptor(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(
                Path(d), "foo", {"name": "foo", "description": "A test"},
                body="Hello.",
            )
            desc = parse_descriptor_file(p)
            self.assertEqual(desc.name, "foo")
            self.assertEqual(desc.description, "A test")
            self.assertFalse(desc.read_only)
            self.assertFalse(desc.can_spawn)
            self.assertTrue(desc.default_enabled)
            self.assertIsNone(desc.write_domain)
            self.assertIn("Hello.", desc.prompt_body)
            self.assertEqual(desc.source_path, p)

    def test_full_descriptor(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(
                Path(d), "writer",
                {
                    "name": "writer",
                    "description": "Writes things",
                    "write_domain": "Algebra/**",
                    "read_only": False,
                    "can_spawn": True,
                    "default_enabled": False,
                },
                body="Body line 1.",
            )
            desc = parse_descriptor_file(p)
            self.assertEqual(desc.write_domain, "Algebra/**")
            self.assertTrue(desc.can_spawn)
            self.assertFalse(desc.default_enabled)

    def test_no_frontmatter_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(Path(d), "bare", None, body="no fm")
            with self.assertRaises(ValueError):
                parse_descriptor_file(p)

    def test_invalid_yaml_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(Path(d), "bad", "name: foo\n  bad: : :", body="")
            with self.assertRaises(ValueError):
                parse_descriptor_file(p)

    def test_missing_name_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(Path(d), "nomane", {"description": "x"})
            with self.assertRaises(ValueError):
                parse_descriptor_file(p)

    def test_name_disagrees_with_filename(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(
                Path(d), "filename", {"name": "different"},
            )
            with self.assertRaises(ValueError):
                parse_descriptor_file(p)

    def test_write_domain_must_be_string(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(
                Path(d), "wrong",
                {"name": "wrong", "write_domain": ["a", "b"]},
            )
            with self.assertRaises(ValueError):
                parse_descriptor_file(p)


# ── load_descriptors_from_dir ───────────────────────────────────────


class LoadFromDirTest(unittest.TestCase):
    def test_missing_dir_returns_empty(self):
        out = load_descriptors_from_dir(Path("/nonexistent/path/we/dont/have"))
        self.assertEqual(out, {})

    def test_loads_multiple(self):
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            _write_descriptor(sd, "a", {"name": "a"})
            _write_descriptor(sd, "b", {"name": "b"})
            out = load_descriptors_from_dir(sd)
            self.assertEqual(set(out.keys()), {"a", "b"})

    def test_bad_file_skipped_with_warning(self):
        """One malformed descriptor doesn't poison the whole directory."""
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            _write_descriptor(sd, "ok", {"name": "ok"})
            _write_descriptor(sd, "broken", None, body="no frontmatter")
            out = load_descriptors_from_dir(sd)
            self.assertIn("ok", out)
            self.assertNotIn("broken", out)


# ── build_registry: precedence + filtering ──────────────────────────


class BuildRegistryTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.project = Path(self._td.name)
        self.project_subdir = self.project / ".archon" / "subagents"
        self.project_subdir.mkdir(parents=True)
        # An "extra" dir we can pass via extra_dirs to simulate built-in.
        self.builtin = Path(self._td.name + "-builtin")
        self.builtin.mkdir()

    def tearDown(self):
        self._td.cleanup()
        import shutil
        shutil.rmtree(self.builtin, ignore_errors=True)

    def test_project_overrides_builtin_by_name(self):
        _write_descriptor(self.builtin, "shared", {
            "name": "shared", "description": "BUILTIN VERSION",
        })
        _write_descriptor(self.project_subdir, "shared", {
            "name": "shared", "description": "PROJECT VERSION",
        })
        # Bypass the actual <built-in> lookup by passing builtin via
        # extra_dirs first; project_subdir comes after via the default
        # source list and wins.
        r = build_registry(self.project, extra_dirs=[self.builtin])
        self.assertEqual(r["shared"].description, "PROJECT VERSION")

    def test_default_enabled_filter(self):
        _write_descriptor(self.project_subdir, "on", {
            "name": "on", "default_enabled": True,
        })
        _write_descriptor(self.project_subdir, "off", {
            "name": "off", "default_enabled": False,
        })
        r = build_registry(self.project)
        self.assertIn("on", r)
        self.assertNotIn("off", r)

    def test_enabled_allowlist_overrides_default(self):
        _write_descriptor(self.project_subdir, "on", {
            "name": "on", "default_enabled": True,
        })
        _write_descriptor(self.project_subdir, "off", {
            "name": "off", "default_enabled": False,
        })
        r = build_registry(self.project, enabled=["off"])
        self.assertIn("off", r)
        self.assertNotIn("on", r)

    def test_enabled_empty_list_loads_nothing(self):
        """Explicit empty list ≠ None — honored as 'no subagents'."""
        _write_descriptor(self.project_subdir, "a", {"name": "a"})
        _write_descriptor(self.project_subdir, "b", {"name": "b"})
        r = build_registry(self.project, enabled=[])
        self.assertEqual(len(r), 0)

    def test_registry_interface(self):
        _write_descriptor(self.project_subdir, "x", {"name": "x"})
        _write_descriptor(self.project_subdir, "y", {"name": "y"})
        # Scope via `enabled` so shipped built-ins don't leak into the
        # assertion; we only care about the project-local pair here.
        r = build_registry(self.project, enabled=["x", "y"])
        self.assertEqual(r.names(), ["x", "y"])
        self.assertEqual([d.name for d in r.descriptors()], ["x", "y"])
        self.assertIsNone(r.get("missing"))
        self.assertIsInstance(r["x"], SubagentDescriptor)


# ── config resolver ─────────────────────────────────────────────────


class ResolveSubagentsEnabledTest(unittest.TestCase):
    def test_no_section_returns_none(self):
        from archon.commands.tooling.project_config import (
            ProjectConfig,
            resolve_subagents_enabled,
        )
        cfg = ProjectConfig()
        self.assertIsNone(resolve_subagents_enabled(cfg))

    def test_null_enabled_returns_none(self):
        from archon.commands.tooling.project_config import (
            ProjectConfig,
            resolve_subagents_enabled,
        )
        cfg = ProjectConfig(raw={"subagents": {"enabled": None}})
        self.assertIsNone(resolve_subagents_enabled(cfg))

    def test_list_returns_list(self):
        from archon.commands.tooling.project_config import (
            ProjectConfig,
            resolve_subagents_enabled,
        )
        cfg = ProjectConfig(raw={"subagents": {"enabled": ["a", "b"]}})
        self.assertEqual(resolve_subagents_enabled(cfg), ["a", "b"])

    def test_empty_list_returns_empty_list(self):
        from archon.commands.tooling.project_config import (
            ProjectConfig,
            resolve_subagents_enabled,
        )
        cfg = ProjectConfig(raw={"subagents": {"enabled": []}})
        self.assertEqual(resolve_subagents_enabled(cfg), [])

    def test_non_list_returns_none(self):
        from archon.commands.tooling.project_config import (
            ProjectConfig,
            resolve_subagents_enabled,
        )
        cfg = ProjectConfig(raw={"subagents": {"enabled": "oops"}})
        self.assertIsNone(resolve_subagents_enabled(cfg))


# ── per-subagent model resolution (new dict shape + legacy string) ──


class ResolveSubagentModelTest(unittest.TestCase):
    def test_dict_shape_with_model(self):
        from archon.commands.tooling.project_config import (
            ProjectConfig,
            resolve_subagent_model,
        )
        cfg = ProjectConfig(raw={
            "subagents": {"refactor": {"model": "sonnet"}},
        })
        self.assertEqual(
            resolve_subagent_model(cfg, "refactor", fallback="opus"),
            "sonnet",
        )

    def test_legacy_bare_string(self):
        from archon.commands.tooling.project_config import (
            ProjectConfig,
            resolve_subagent_model,
        )
        cfg = ProjectConfig(raw={"subagents": {"refactor": "kimi"}})
        self.assertEqual(
            resolve_subagent_model(cfg, "refactor", fallback="opus"),
            "kimi",
        )

    def test_falls_back_to_loop_model(self):
        from archon.commands.tooling.project_config import (
            ProjectConfig,
            resolve_subagent_model,
        )
        cfg = ProjectConfig(raw={"loop": {"model": "haiku"}})
        self.assertEqual(
            resolve_subagent_model(cfg, "anything", fallback="opus"),
            "haiku",
        )

    def test_final_fallback(self):
        from archon.commands.tooling.project_config import (
            ProjectConfig,
            resolve_subagent_model,
        )
        cfg = ProjectConfig()
        self.assertEqual(
            resolve_subagent_model(cfg, "anything", fallback="opus"),
            "opus",
        )


# ── mandatory frontmatter parsing ───────────────────────────────────


class MandatoryFieldTest(unittest.TestCase):
    def test_missing_defaults_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(Path(d), "foo", {"name": "foo"})
            desc = parse_descriptor_file(p)
            self.assertEqual(desc.mandatory, ())
            self.assertFalse(desc.is_mandatory_for("plan"))

    def test_single_string_coerced_to_tuple(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(
                Path(d), "foo", {"name": "foo", "mandatory": "plan"},
            )
            desc = parse_descriptor_file(p)
            self.assertEqual(desc.mandatory, ("plan",))
            self.assertTrue(desc.is_mandatory_for("plan"))
            self.assertFalse(desc.is_mandatory_for("review"))

    def test_list_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(
                Path(d), "foo", {"name": "foo", "mandatory": ["plan", "review"]},
            )
            desc = parse_descriptor_file(p)
            self.assertEqual(desc.mandatory, ("plan", "review"))
            self.assertTrue(desc.is_mandatory_for("plan"))
            self.assertTrue(desc.is_mandatory_for("review"))

    def test_bad_shape_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(
                Path(d), "foo", {"name": "foo", "mandatory": 42},
            )
            with self.assertRaises(ValueError):
                parse_descriptor_file(p)

    def test_empty_string_in_list_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(
                Path(d), "foo", {"name": "foo", "mandatory": ["plan", ""]},
            )
            with self.assertRaises(ValueError):
                parse_descriptor_file(p)


# ── dispatcher_notes frontmatter parsing ────────────────────────────


class DispatcherNotesFieldTest(unittest.TestCase):
    def test_missing_defaults_to_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(Path(d), "foo", {"name": "foo"})
            desc = parse_descriptor_file(p)
            self.assertEqual(desc.dispatcher_notes, "")

    def test_string_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(
                Path(d), "foo",
                {"name": "foo", "dispatcher_notes": "- rule one\n- rule two"},
            )
            desc = parse_descriptor_file(p)
            self.assertIn("rule one", desc.dispatcher_notes)
            self.assertIn("rule two", desc.dispatcher_notes)

    def test_list_joined(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(
                Path(d), "foo",
                {"name": "foo", "dispatcher_notes": ["- one", "- two"]},
            )
            desc = parse_descriptor_file(p)
            self.assertEqual(desc.dispatcher_notes, "- one\n- two")

    def test_bad_shape_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write_descriptor(
                Path(d), "foo", {"name": "foo", "dispatcher_notes": 42},
            )
            with self.assertRaises(ValueError):
                parse_descriptor_file(p)


# ── catalog rendering ───────────────────────────────────────────────


class CatalogBlockTest(unittest.TestCase):
    """Tests for prompts._subagent_catalog_block.

    Each test seeds project-local descriptors and writes a config.json
    whose ``subagents.enabled`` lists exactly those names so shipped
    built-ins don't leak into the assertions.
    """

    def setUp(self):
        import json
        self._td = tempfile.TemporaryDirectory()
        self.project = Path(self._td.name)
        (self.project / ".archon" / "subagents").mkdir(parents=True)
        # Default to "no subagents enabled" — each test extends this.
        self._enabled: list[str] = []
        self._json = json

    def tearDown(self):
        self._td.cleanup()

    def _write_subagent(self, name: str, **fm):
        fm.setdefault("name", name)
        _write_descriptor(self.project / ".archon" / "subagents", name, fm)
        self._enabled.append(name)

    def _commit_config(self) -> None:
        (self.project / ".archon" / "config.json").write_text(
            self._json.dumps({"subagents": {"enabled": self._enabled}}),
        )

    def _render(self, role: str) -> str:
        from archon.prompts import _subagent_catalog_block
        self._commit_config()
        return _subagent_catalog_block(self.project, role=role)

    def test_no_subagents_emits_explanatory_block(self):
        out = self._render("plan")
        self.assertIn("Available subagents", out)
        # When nothing is enabled, the hint must (a) flag that no
        # subagents are active and (b) point the user at the config
        # they can edit to turn them on.
        self.assertIn("None are currently", out)
        self.assertIn("config.json", out)

    def test_lists_enabled_descriptors_sorted(self):
        self._write_subagent("zebra", description="last alphabetically")
        self._write_subagent("alpha", description="first alphabetically")
        out = self._render("plan")
        self.assertLess(out.index("**alpha**"), out.index("**zebra**"))

    def test_mandatory_tag_for_calling_phase(self):
        self._write_subagent(
            "reviewer", description="audit", mandatory=["plan"], read_only=True,
        )
        plan_out = self._render("plan")
        review_out = self._render("review")
        self.assertIn("MANDATORY", plan_out)
        self.assertNotIn("MANDATORY", review_out)

    def test_mandatory_footer_when_phase_has_mandatory(self):
        self._write_subagent("reviewer", mandatory=["plan"])
        out = self._render("plan")
        self.assertIn("You MUST dispatch", out)
        self.assertIn("`reviewer`", out)

    def test_no_mandatory_footer_when_optional_only(self):
        self._write_subagent("writer", description="optional", mandatory=[])
        out = self._render("plan")
        self.assertNotIn("You MUST dispatch", out)

    def test_read_only_and_can_spawn_tags(self):
        self._write_subagent("ro", description="r", read_only=True)
        self._write_subagent("ws", description="w", can_spawn=True)
        out = self._render("plan")
        self.assertIn("read-only", out)
        self.assertIn("can spawn children", out)

    def test_description_truncated_when_long(self):
        long = "x" * 500
        self._write_subagent("big", description=long)
        out = self._render("plan")
        self.assertNotIn("x" * 300, out)
        self.assertIn("...", out)

    def test_workflow_guidance_section_appears(self):
        self._write_subagent(
            "reviewer", description="audit",
            dispatcher_notes="- Dispatch me before any Lean work.",
        )
        out = self._render("plan")
        self.assertIn("Workflow guidance from active subagents", out)
        self.assertIn("Dispatch me before any Lean work", out)
        self.assertIn("### reviewer", out)

    def test_workflow_guidance_section_omitted_when_no_notes(self):
        self._write_subagent("plain", description="no notes")
        out = self._render("plan")
        self.assertNotIn("Workflow guidance from active subagents", out)

    def test_workflow_guidance_aggregates_multiple(self):
        self._write_subagent(
            "a", description="A",
            dispatcher_notes="- A rule.",
        )
        self._write_subagent(
            "b", description="B",
            dispatcher_notes="- B rule.",
        )
        out = self._render("plan")
        self.assertIn("### a", out)
        self.assertIn("### b", out)
        self.assertIn("A rule.", out)
        self.assertIn("B rule.", out)


# ── post-phase mandatory audit ──────────────────────────────────────


class MandatoryAuditTest(unittest.TestCase):
    """Tests for archon.subagents.audit.check_mandatory_dispatched.

    Tests pass ``enabled=[<test descriptors>]`` so built-in
    mandatory-for-plan subagents (e.g. blueprint-reviewer) don't leak
    into per-test assertions.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.project = Path(self._td.name)
        self.state = self.project / ".archon"
        (self.state / "subagents").mkdir(parents=True)
        self.iter_dir = self.state / "logs" / "iter-007"
        self.iter_dir.mkdir(parents=True)
        self._enabled: list[str] = []

    def tearDown(self):
        self._td.cleanup()

    def _write_subagent(self, name: str, **fm):
        fm.setdefault("name", name)
        _write_descriptor(self.state / "subagents", name, fm)
        self._enabled.append(name)

    def _record_dispatch(self, role: str, slug: str = "x"):
        from archon.subagents.base import _append_dispatch_jsonl
        _append_dispatch_jsonl(self.iter_dir / "dispatch.jsonl", {
            "event": "dispatch_start", "role": role, "slug": slug,
        })

    def test_no_mandatory_returns_empty(self):
        from archon.subagents.audit import check_mandatory_dispatched
        self._enabled = []
        self._write_subagent("optional", mandatory=[])
        missing = check_mandatory_dispatched(
            self.project, self.state, 7, "plan", enabled=self._enabled,
        )
        self.assertEqual(missing, [])

    def test_mandatory_dispatched_returns_empty(self):
        from archon.subagents.audit import check_mandatory_dispatched
        self._enabled = []
        self._write_subagent("auditor", mandatory=["plan"])
        self._record_dispatch("auditor", "now")
        missing = check_mandatory_dispatched(
            self.project, self.state, 7, "plan", enabled=self._enabled,
        )
        self.assertEqual(missing, [])

    def test_mandatory_missing_reported(self):
        from archon.subagents.audit import check_mandatory_dispatched
        self._enabled = []
        self._write_subagent("auditor", mandatory=["plan"])
        # No dispatch.jsonl entries → missing
        missing = check_mandatory_dispatched(
            self.project, self.state, 7, "plan", enabled=self._enabled,
        )
        self.assertEqual(missing, ["auditor"])

    def test_mandatory_for_other_phase_not_required_here(self):
        from archon.subagents.audit import check_mandatory_dispatched
        self._enabled = []
        self._write_subagent("auditor", mandatory=["review"])
        # No review-phase dispatch yet; calling for plan should pass
        missing = check_mandatory_dispatched(
            self.project, self.state, 7, "plan", enabled=self._enabled,
        )
        self.assertEqual(missing, [])


# ── built-in descriptors smoke ──────────────────────────────────────


class BuiltInRegistryTest(unittest.TestCase):
    """Tests that the shipped built-in descriptors parse and surface.

    Subagents ship with ``default_enabled: false`` (retrocompat: users
    opt in via ``subagents.enabled``). These smoke tests therefore
    pass an explicit ``enabled=[name]`` so the descriptor actually
    loads into the registry; the assertions are about descriptor
    content (write_domain, mandatory, can_spawn), not the default
    enable state.
    """

    def test_reference_retriever_loads(self):
        with tempfile.TemporaryDirectory() as d:
            r = build_registry(Path(d), enabled=["reference-retriever"])
            self.assertIn("reference-retriever", r)
            desc = r["reference-retriever"]
            self.assertEqual(desc.write_domain, "references/**")
            self.assertFalse(desc.read_only)
            self.assertFalse(desc.can_spawn)
            self.assertEqual(desc.mandatory, ())
            # Ships off by default — retrocompat with pre-subagent Archon.
            self.assertFalse(desc.default_enabled)

    def test_blueprint_writer_can_spawn(self):
        with tempfile.TemporaryDirectory() as d:
            r = build_registry(Path(d), enabled=["blueprint-writer"])
            self.assertIn("blueprint-writer", r)
            self.assertTrue(r["blueprint-writer"].can_spawn)

    def test_blueprint_reviewer_still_mandatory_for_plan(self):
        with tempfile.TemporaryDirectory() as d:
            r = build_registry(Path(d), enabled=["blueprint-reviewer"])
            self.assertTrue(r["blueprint-reviewer"].is_mandatory_for("plan"))
            self.assertFalse(r["blueprint-reviewer"].is_mandatory_for("review"))

    def test_lean_auditor_still_mandatory_for_review(self):
        with tempfile.TemporaryDirectory() as d:
            r = build_registry(Path(d), enabled=["lean-auditor"])
            self.assertTrue(r["lean-auditor"].is_mandatory_for("review"))
            self.assertFalse(r["lean-auditor"].is_mandatory_for("plan"))

    def test_lean_vs_blueprint_checker_mandatory_for_review(self):
        with tempfile.TemporaryDirectory() as d:
            r = build_registry(Path(d), enabled=["lean-vs-blueprint-checker"])
            self.assertIn("lean-vs-blueprint-checker", r)
            self.assertTrue(
                r["lean-vs-blueprint-checker"].is_mandatory_for("review"),
            )

    def test_mathlib_analogist_loads(self):
        with tempfile.TemporaryDirectory() as d:
            r = build_registry(Path(d), enabled=["mathlib-analogist"])
            self.assertIn("mathlib-analogist", r)
            desc = r["mathlib-analogist"]
            self.assertTrue(desc.read_only)
            self.assertEqual(desc.mandatory, ())  # advisor, not mandatory

    def test_strategy_critic_mandatory_for_plan(self):
        with tempfile.TemporaryDirectory() as d:
            r = build_registry(Path(d), enabled=["strategy-critic"])
            self.assertIn("strategy-critic", r)
            desc = r["strategy-critic"]
            self.assertTrue(desc.read_only)
            self.assertTrue(desc.is_mandatory_for("plan"))
            self.assertFalse(desc.is_mandatory_for("review"))

    def test_progress_critic_mandatory_for_plan(self):
        with tempfile.TemporaryDirectory() as d:
            r = build_registry(Path(d), enabled=["progress-critic"])
            self.assertIn("progress-critic", r)
            desc = r["progress-critic"]
            self.assertTrue(desc.read_only)
            self.assertTrue(desc.is_mandatory_for("plan"))
            self.assertFalse(desc.is_mandatory_for("review"))

    def test_nothing_enabled_by_default(self):
        """Sanity: ``build_registry`` with no config yields empty.

        Confirms the retrocompat default — no subagents fire until the
        user explicitly enables them via ``.archon/config.json``.
        """
        with tempfile.TemporaryDirectory() as d:
            r = build_registry(Path(d))
            self.assertEqual(len(r), 0)


if __name__ == "__main__":
    unittest.main()

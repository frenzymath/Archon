"""Tests for fresh-init harness selection config."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.agent import ClaudeAgent, build_runner
from archon.agents.codex import CodexAgent
from archon.commands.init.steps.harness_select import selection_from_choice
from archon.commands.loop.preflight import _loop_requires_claude
from archon.commands.tooling.project_config import (
    DEFAULT_HARNESS,
    ProjectConfig,
    apply_harness_selection,
    default_config,
    load_project_config,
    resolve_role_harness,
    write_default_config,
)


class DefaultConfigHarnessesTest(unittest.TestCase):
    def test_harnesses_block_is_inert_by_default(self):
        cfg = ProjectConfig(raw=default_config())
        self.assertIn("codex", cfg.raw["harnesses"])
        self.assertNotIn("claude-code", cfg.raw["harnesses"])
        for role in ("plan", "prover", "review"):
            self.assertEqual(resolve_role_harness(cfg, role), DEFAULT_HARNESS)
            runner = build_runner(role=role, model="opus", cfg=cfg)
            self.assertIsInstance(runner, ClaudeAgent)
            self.assertEqual(runner.model, "opus")
            self.assertEqual(runner.role, role)


class ApplyHarnessSelectionTest(unittest.TestCase):
    def test_default_selection_writes_no_loop_override(self):
        cfg = default_config()
        apply_harness_selection(cfg, None)
        self.assertNotIn("harness", cfg["loop"])
        self.assertNotIn("roles", cfg["loop"])

    def test_codex_selection_sets_loop_harness(self):
        cfg = default_config()
        apply_harness_selection(cfg, "codex")
        self.assertEqual(cfg["loop"]["harness"], "codex")

    def test_mixed_selection_writes_only_nondefault_roles(self):
        cfg = default_config()
        apply_harness_selection(
            cfg,
            {"plan": "claude-code", "prover": "codex", "review": "claude-code"},
        )
        self.assertEqual(cfg["loop"]["roles"], {"prover": "codex"})



    def test_codex_harness_works_without_harnesses_block(self):
        cfg = ProjectConfig(raw={"loop": {"harness": "codex"}})
        runner = build_runner(role="extract", model="opus", cfg=cfg)
        self.assertIsInstance(runner, CodexAgent)
        self.assertIsNone(runner.model)

class WriteDefaultConfigHarnessTest(unittest.TestCase):
    def test_codex_selection_round_trips_to_codex_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self.assertTrue(write_default_config(project, harness_selection="codex"))
            cfg = load_project_config(project)
            self.assertEqual(resolve_role_harness(cfg, "prover"), "codex")
            self.assertIsInstance(build_runner(role="prover", model="opus", cfg=cfg), CodexAgent)

    def test_mixed_selection_routes_only_prover(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_default_config(
                project,
                harness_selection={
                    "plan": "claude-code",
                    "prover": "codex",
                    "review": "claude-code",
                },
            )
            cfg = load_project_config(project)
            self.assertIsInstance(build_runner(role="prover", model="opus", cfg=cfg), CodexAgent)
            runner = build_runner(role="plan", model="opus", cfg=cfg)
            self.assertIsInstance(runner, ClaudeAgent)
            self.assertEqual(runner.model, "opus")
            self.assertEqual(runner.role, "plan")


class LoopPreflightHarnessTest(unittest.TestCase):
    def test_codex_loop_harness_does_not_require_claude(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_default_config(project, harness_selection="codex")
            self.assertFalse(_loop_requires_claude(project))

    def test_mixed_loop_still_requires_claude(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_default_config(
                project,
                harness_selection={
                    "plan": "claude-code",
                    "prover": "codex",
                    "review": "claude-code",
                },
            )
            self.assertTrue(_loop_requires_claude(project))


class SelectionFromChoiceTest(unittest.TestCase):
    def test_choices(self):
        self.assertIsNone(selection_from_choice("1"))
        self.assertEqual(selection_from_choice("2"), "codex")
        self.assertEqual(selection_from_choice("3"), "antigravity")
        self.assertEqual(
            selection_from_choice(
                "4",
                {"plan": "claude-code", "prover": "codex", "review": "claude-code"},
            ),
            {"plan": "claude-code", "prover": "codex", "review": "claude-code"},
        )


if __name__ == "__main__":
    unittest.main()

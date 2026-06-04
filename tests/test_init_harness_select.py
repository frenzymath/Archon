"""Tests for the `archon init` harness selection feature.

Covers three layers:

* the config helpers in ``project_config`` — the shipped (inert)
  ``harnesses`` block, ``apply_harness_selection``, and the
  ``harness_selection``-aware ``write_default_config``;
* the **zero-regression invariant** — the shipped default config must not
  trip the ``build_runner`` short-circuit (every role still builds exactly
  the legacy ``ClaudeAgent``);
* the pure menu mapping ``selection_from_choice`` and the non-interactive
  resolution in ``harness_select`` (no stdin driven).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import typer

from archon.agent import ClaudeAgent, build_runner
from archon.agents.codex import CodexAgent
from archon.commands.init.steps.harness_select import (
    resolve_harness_selection,
    selection_from_choice,
)
from archon.commands.tooling.project_config import (
    DEFAULT_HARNESS,
    ProjectConfig,
    apply_harness_selection,
    default_config,
    load_harness_descriptor,
    load_project_config,
    resolve_role_harness,
    write_default_config,
)


# ── shipped default config ────────────────────────────────────────────


class DefaultConfigHarnessesTest(unittest.TestCase):
    def test_harnesses_block_is_shipped(self):
        dc = default_config()
        self.assertIn("harnesses", dc)
        # The codex descriptor ships; no claude-code descriptor (so the
        # zero-config fast path is preserved — see below).
        self.assertIn("codex-gpt", dc["harnesses"])
        self.assertNotIn("claude-code", dc["harnesses"])

    def test_shipped_codex_descriptor_is_native_login(self):
        cfg = ProjectConfig(raw=default_config())
        d = load_harness_descriptor(cfg, "codex-gpt")
        self.assertEqual(d.runner, "codex")
        self.assertEqual(d.model, "gpt-5.5")
        self.assertEqual(d.mcp, ("lean-lsp",))
        self.assertEqual(d.prompt_variant, "codex")
        # Native ~/.codex login: no gateway env vars configured.
        self.assertIsNone(d.base_url_env)
        self.assertIsNone(d.key_env)

    def test_zero_regression_with_shipped_default_config(self):
        # The shipped harnesses block is inert: with no loop.harness /
        # loop.roles key, every role still builds exactly ClaudeAgent.
        cfg = ProjectConfig(raw=default_config())
        for role in ("plan", "prover", "review"):
            with self.subTest(role=role):
                self.assertEqual(resolve_role_harness(cfg, role), DEFAULT_HARNESS)
                self.assertEqual(
                    build_runner(role=role, model="opus", cfg=cfg),
                    ClaudeAgent(model="opus", role=role),
                )


# ── apply_harness_selection ───────────────────────────────────────────


class ApplyHarnessSelectionTest(unittest.TestCase):
    def _loop(self, selection):
        cfg = default_config()
        apply_harness_selection(cfg, selection)
        return cfg["loop"]

    def test_none_is_noop(self):
        loop = self._loop(None)
        self.assertNotIn("harness", loop)
        self.assertNotIn("roles", loop)

    def test_claude_code_string_is_noop(self):
        loop = self._loop("claude-code")
        self.assertNotIn("harness", loop)
        self.assertNotIn("roles", loop)

    def test_codex_string_sets_loop_harness(self):
        loop = self._loop("codex-gpt")
        self.assertEqual(loop["harness"], "codex-gpt")
        self.assertNotIn("roles", loop)

    def test_mixed_dict_sets_only_nondefault_roles(self):
        loop = self._loop(
            {"plan": "claude-code", "prover": "codex-gpt", "review": "claude-code"}
        )
        self.assertNotIn("harness", loop)
        self.assertEqual(loop["roles"], {"prover": "codex-gpt"})

    def test_all_default_mixed_writes_nothing(self):
        loop = self._loop(
            {"plan": "claude-code", "prover": "claude-code", "review": "claude-code"}
        )
        self.assertNotIn("roles", loop)

    def test_unknown_roles_ignored(self):
        loop = self._loop({"bogus": "codex-gpt", "prover": "codex-gpt"})
        self.assertEqual(loop["roles"], {"prover": "codex-gpt"})

    def test_bad_type_raises(self):
        with self.assertRaises(TypeError):
            apply_harness_selection(default_config(), 123)


# ── write_default_config round-trip ───────────────────────────────────


class WriteDefaultConfigTest(unittest.TestCase):
    def test_codex_selection_round_trips_to_codex_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            wrote = write_default_config(project, harness_selection="codex-gpt")
            self.assertTrue(wrote)
            cfg = load_project_config(project)
            self.assertEqual(cfg.raw["loop"]["harness"], "codex-gpt")
            for role in ("plan", "prover", "review"):
                self.assertEqual(resolve_role_harness(cfg, role), "codex-gpt")
            runner = build_runner(role="prover", model="opus", cfg=cfg)
            self.assertIsInstance(runner, CodexAgent)

    def test_mixed_selection_routes_only_prover(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_default_config(
                project,
                harness_selection={
                    "plan": "claude-code",
                    "prover": "codex-gpt",
                    "review": "claude-code",
                },
            )
            cfg = load_project_config(project)
            self.assertIsInstance(
                build_runner(role="prover", model="opus", cfg=cfg), CodexAgent
            )
            self.assertEqual(
                build_runner(role="plan", model="opus", cfg=cfg),
                ClaudeAgent(model="opus", role="plan"),
            )

    def test_default_selection_is_plain_claude_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_default_config(project, harness_selection=None)
            cfg = load_project_config(project)
            self.assertNotIn("harness", cfg.raw["loop"])
            self.assertNotIn("roles", cfg.raw["loop"])

    def test_existing_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_default_config(project, harness_selection="codex-gpt")
            # Second write (different selection) must be a no-op.
            wrote = write_default_config(project, harness_selection=None)
            self.assertFalse(wrote)
            cfg = load_project_config(project)
            self.assertEqual(cfg.raw["loop"]["harness"], "codex-gpt")


# ── selection_from_choice (pure menu mapping) ─────────────────────────


class SelectionFromChoiceTest(unittest.TestCase):
    def test_choice_one_is_default(self):
        self.assertIsNone(selection_from_choice("1"))

    def test_choice_two_is_codex(self):
        self.assertEqual(selection_from_choice("2"), "codex-gpt")

    def test_choice_three_returns_role_choices_filtered_to_known_roles(self):
        sel = selection_from_choice(
            "3",
            {"plan": "claude-code", "prover": "codex-gpt", "review": "claude-code",
             "bogus": "codex-gpt"},
        )
        self.assertEqual(
            sel,
            {"plan": "claude-code", "prover": "codex-gpt", "review": "claude-code"},
        )

    def test_named_aliases(self):
        self.assertIsNone(selection_from_choice("claude-code"))
        self.assertEqual(selection_from_choice("codex-gpt"), "codex-gpt")

    def test_unknown_choice_raises(self):
        with self.assertRaises(ValueError):
            selection_from_choice("9")


# ── resolve_harness_selection ─────────────────────────────────────────
#
# A ``--harness`` flag resolves without any prompt; with no flag the menu
# is always shown (init always runs with a human present), so those cases
# mock the prompt functions rather than driving stdin.


class ResolveHarnessSelectionTest(unittest.TestCase):
    def test_no_flag_shows_menu(self):
        with mock.patch(
            "archon.commands.init.steps.harness_select.prompt_harness_selection",
            return_value="codex-gpt",
        ) as menu:
            self.assertEqual(
                resolve_harness_selection(SimpleNamespace(harness=None)),
                "codex-gpt",
            )
            menu.assert_called_once()

    def test_flag_codex_resolves_without_prompt(self):
        self.assertEqual(
            resolve_harness_selection(SimpleNamespace(harness="codex-gpt")),
            "codex-gpt",
        )

    def test_flag_claude_code_is_none(self):
        self.assertIsNone(
            resolve_harness_selection(SimpleNamespace(harness="claude-code"))
        )

    def test_flag_mixed_prompts_per_role(self):
        with mock.patch(
            "archon.commands.init.steps.harness_select._prompt_role_choices",
            return_value={"plan": "claude-code", "prover": "codex-gpt",
                          "review": "claude-code"},
        ):
            sel = resolve_harness_selection(SimpleNamespace(harness="mixed"))
        self.assertEqual(
            sel,
            {"plan": "claude-code", "prover": "codex-gpt", "review": "claude-code"},
        )

    def test_bad_flag_errors(self):
        with self.assertRaises(typer.Exit):
            resolve_harness_selection(SimpleNamespace(harness="gpt5"))


if __name__ == "__main__":
    unittest.main()

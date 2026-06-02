"""Tests for the Phase-1 harness router seam.

Covers the responsibility → harness routing layer introduced as a pure
refactor: the per-role / per-subagent harness resolvers, the
``build_runner`` factory (including the zero-regression invariant and
the unknown-harness error), the harness descriptor loader, the optional
subagent ``harness`` frontmatter, and the forward-compat
``LaneConfig.harness`` field.

In Phase 1 the only runner is ``"claude-code"``; with no new config keys
present every path must resolve to it, and the factory must return
exactly the legacy ``ClaudeAgent`` object.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.agent import (
    DEFAULT_HARNESS,
    AgentRunner,
    ClaudeAgent,
    UnknownHarnessError,
    build_runner,
)
from archon.commands.tooling.project_config import (
    HarnessDescriptor,
    ProjectConfig,
    has_explicit_harness_override,
    load_harness_descriptor,
    resolve_role_harness,
    resolve_subagent_harness,
)
from archon.multilane.config import (
    multilane_config_from_simple,
)
from archon.subagents.registry import parse_descriptor_file


# ── resolve_role_harness ─────────────────────────────────────────────


class ResolveRoleHarnessTest(unittest.TestCase):
    def test_empty_config_is_claude_code(self):
        self.assertEqual(resolve_role_harness(ProjectConfig(), "plan"), "claude-code")

    def test_loop_harness_applies_to_all_roles(self):
        cfg = ProjectConfig(raw={"loop": {"harness": "h-loop"}})
        self.assertEqual(resolve_role_harness(cfg, "plan"), "h-loop")
        self.assertEqual(resolve_role_harness(cfg, "review"), "h-loop")

    def test_role_override_wins_over_loop(self):
        cfg = ProjectConfig(
            raw={"loop": {"harness": "h-loop", "roles": {"plan": "h-plan"}}}
        )
        # loop.roles.<role> > loop.harness
        self.assertEqual(resolve_role_harness(cfg, "plan"), "h-plan")
        # unlisted role falls through to loop.harness
        self.assertEqual(resolve_role_harness(cfg, "review"), "h-loop")

    def test_role_as_dict_with_harness_key(self):
        cfg = ProjectConfig(
            raw={"loop": {"roles": {"prover": {"harness": "h-prover"}}}}
        )
        self.assertEqual(resolve_role_harness(cfg, "prover"), "h-prover")

    def test_role_dict_without_harness_falls_through(self):
        cfg = ProjectConfig(
            raw={"loop": {"harness": "h-loop", "roles": {"prover": {"model": "opus"}}}}
        )
        self.assertEqual(resolve_role_harness(cfg, "prover"), "h-loop")

    def test_custom_fallback_honored(self):
        self.assertEqual(
            resolve_role_harness(ProjectConfig(), "plan", fallback="x"), "x"
        )


# ── resolve_subagent_harness ─────────────────────────────────────────


class ResolveSubagentHarnessTest(unittest.TestCase):
    def test_empty_config_is_claude_code(self):
        self.assertEqual(
            resolve_subagent_harness(ProjectConfig(), "lean-auditor"), "claude-code"
        )

    def test_subagent_override_wins(self):
        cfg = ProjectConfig(
            raw={
                "subagents": {"lean-auditor": {"harness": "h-sub"}},
                "loop": {"harness": "h-loop"},
            }
        )
        self.assertEqual(
            resolve_subagent_harness(
                cfg, "lean-auditor", descriptor_harness="h-desc"
            ),
            "h-sub",
        )

    def test_loop_harness_beats_descriptor(self):
        cfg = ProjectConfig(raw={"loop": {"harness": "h-loop"}})
        self.assertEqual(
            resolve_subagent_harness(cfg, "x", descriptor_harness="h-desc"),
            "h-loop",
        )

    def test_descriptor_frontmatter_used_when_no_config(self):
        self.assertEqual(
            resolve_subagent_harness(
                ProjectConfig(), "x", descriptor_harness="h-desc"
            ),
            "h-desc",
        )

    def test_bare_string_subagent_is_model_not_harness(self):
        # A bare string under subagents.<name> is the model alias for
        # backward compat (resolve_subagent_model), NOT a harness. It
        # must not be picked up as a harness name.
        cfg = ProjectConfig(raw={"subagents": {"lean-auditor": "haiku"}})
        self.assertEqual(
            resolve_subagent_harness(cfg, "lean-auditor"), "claude-code"
        )


# ── HarnessDescriptor loader ─────────────────────────────────────────


class LoadHarnessDescriptorTest(unittest.TestCase):
    def test_absent_section_returns_builtin(self):
        d = load_harness_descriptor(ProjectConfig(), "claude-code")
        self.assertIsInstance(d, HarnessDescriptor)
        self.assertEqual(d.name, "claude-code")
        self.assertEqual(d.runner, "claude-code")
        self.assertIsNone(d.model)

    def test_explicit_descriptor_parsed(self):
        cfg = ProjectConfig(
            raw={
                "harnesses": {
                    "claude-code": {"runner": "claude-code", "model": "haiku"}
                }
            }
        )
        d = load_harness_descriptor(cfg, "claude-code")
        self.assertEqual(d.runner, "claude-code")
        self.assertEqual(d.model, "haiku")

    def test_runner_defaults_to_harness_name(self):
        cfg = ProjectConfig(raw={"harnesses": {"claude-code": {"model": "opus"}}})
        d = load_harness_descriptor(cfg, "claude-code")
        self.assertEqual(d.runner, "claude-code")

    def test_has_explicit_harness_override(self):
        self.assertFalse(has_explicit_harness_override(ProjectConfig(), "claude-code"))
        cfg = ProjectConfig(raw={"harnesses": {"claude-code": {"runner": "claude-code"}}})
        self.assertTrue(has_explicit_harness_override(cfg, "claude-code"))


# ── build_runner ──────────────────────────────────────────────────────


class BuildRunnerTest(unittest.TestCase):
    def test_default_returns_claude_agent(self):
        r = build_runner(role="plan", model="opus")
        self.assertIsInstance(r, ClaudeAgent)
        self.assertIsInstance(r, AgentRunner)
        self.assertEqual(r.model, "opus")
        self.assertEqual(r.role, "plan")

    def test_zero_regression_invariant_empty_config(self):
        # Empty config, role X => exactly ClaudeAgent(model=m, role=X).
        # ClaudeAgent is a dataclass, so == compares all fields, proving
        # the factory returns the same object the call site built before
        # this PR.
        for role in ("plan", "prover", "review"):
            with self.subTest(role=role):
                got = build_runner(role=role, model="opus", cfg=ProjectConfig())
                self.assertEqual(got, ClaudeAgent(model="opus", role=role))

    def test_zero_regression_invariant_no_cfg(self):
        # No cfg at all => same short-circuit.
        got = build_runner(role="prover", model="sonnet")
        self.assertEqual(got, ClaudeAgent(model="sonnet", role="prover"))

    def test_explicit_claude_code_descriptor_model_override(self):
        cfg = ProjectConfig(
            raw={"harnesses": {"claude-code": {"runner": "claude-code", "model": "haiku"}}}
        )
        r = build_runner(role="review", model="opus", cfg=cfg)
        self.assertIsInstance(r, ClaudeAgent)
        # Descriptor's model wins when set.
        self.assertEqual(r.model, "haiku")

    def test_unknown_harness_raises(self):
        # `gemini` is named but has no runner implementation in this
        # version (codex now does — see CodexBuildRunnerTest).
        cfg = ProjectConfig(
            raw={
                "harnesses": {"gem": {"runner": "gemini"}},
                "loop": {"harness": "gem"},
            }
        )
        with self.assertRaises(UnknownHarnessError) as cm:
            build_runner(role="prover", model="opus", cfg=cfg)
        msg = str(cm.exception)
        self.assertIn("gemini", msg)
        self.assertIn(DEFAULT_HARNESS, msg)

    def test_unknown_harness_passed_directly_raises(self):
        # The harness name can be passed pre-resolved (e.g. by the prover
        # pool worker). An unknown one must still raise even with no cfg.
        with self.assertRaises(UnknownHarnessError):
            build_runner(role="prover", model="opus", harness="gemini-cli")

    def test_direct_claude_code_harness_short_circuits(self):
        got = build_runner(role="prover", model="opus", harness="claude-code")
        self.assertEqual(got, ClaudeAgent(model="opus", role="prover"))


# ── subagent descriptor harness frontmatter ──────────────────────────


def _write(dir: Path, name: str, frontmatter: str, body: str = "Body.") -> Path:
    path = dir / f"{name}.md"
    path.write_text("---\n" + frontmatter.rstrip() + "\n---\n\n" + body, encoding="utf-8")
    return path


class SubagentHarnessFrontmatterTest(unittest.TestCase):
    def test_harness_defaults_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d), "foo", "name: foo\ndescription: t")
            desc = parse_descriptor_file(p)
            self.assertEqual(desc.harness, "claude-code")

    def test_harness_parsed_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d), "foo", "name: foo\nharness: codex-gpt")
            desc = parse_descriptor_file(p)
            self.assertEqual(desc.harness, "codex-gpt")

    def test_unknown_frontmatter_keys_still_ignored(self):
        # Additive: a descriptor with both the new harness key and some
        # never-seen key parses fine (unknown keys ignored).
        with tempfile.TemporaryDirectory() as d:
            p = _write(
                Path(d), "foo",
                "name: foo\nharness: claude-code\nwhatever: 123",
            )
            desc = parse_descriptor_file(p)
            self.assertEqual(desc.name, "foo")
            self.assertEqual(desc.harness, "claude-code")

    def test_non_string_harness_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d), "foo", "name: foo\nharness: [a, b]")
            with self.assertRaises(ValueError):
                parse_descriptor_file(p)


# ── LaneConfig.harness forward-compat ────────────────────────────────


class LaneConfigHarnessTest(unittest.TestCase):
    def test_simple_shape_defaults_to_claude_code(self):
        cfg = multilane_config_from_simple(
            {"enabled": True, "lanes": [{"lane_id": "anthropic", "provider": "anthropic"}]}
        )
        self.assertEqual(cfg.lanes[0].harness, "claude-code")

    def test_simple_shape_parses_harness(self):
        cfg = multilane_config_from_simple(
            {
                "enabled": True,
                "lanes": [
                    {"lane_id": "codex", "provider": "anthropic", "harness": "codex-gpt"}
                ],
            }
        )
        self.assertEqual(cfg.lanes[0].harness, "codex-gpt")

    def test_full_config_file_shape_parses_harness(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "lanes.json"
            path.write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "version": 1,
                        "base_ref": "main",
                        "lanes": [
                            {
                                "lane_id": "codex",
                                "provider": "anthropic",
                                "harness": "codex-gpt",
                                "worktree": {"path": "p", "branch": "b"},
                            },
                            {
                                "lane_id": "anthropic",
                                "provider": "anthropic",
                                "worktree": {"path": "p2", "branch": "b2"},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            from archon.multilane.config import load_multilane_config

            cfg = load_multilane_config(path)
            by_id = {lane.lane_id: lane for lane in cfg.lanes}
            self.assertEqual(by_id["codex"].harness, "codex-gpt")
            # Absent => default.
            self.assertEqual(by_id["anthropic"].harness, "claude-code")


if __name__ == "__main__":
    unittest.main()

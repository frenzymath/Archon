"""Tests for the harness router seam (two layers: engine × claude backend).

Covers the responsibility → harness routing layer:

* the per-role / per-subagent harness resolvers,
* the ``HarnessDescriptor`` loader (including the new ``backend`` field),
* the ``build_runner`` factory: the zero-regression invariant, the
  unknown-harness error, and — specific to this branch — that the
  loop-wide claude ``backend`` (claude-p / vscode / …) is threaded into
  the claude-code engine and that a per-harness ``backend`` overrides it,
* the optional subagent ``harness`` frontmatter,
* the forward-compat ``LaneConfig.harness`` field,
* that a ``HarnessDescriptor`` round-trips through ``pickle`` (it is
  threaded into the prover ``ProcessPoolExecutor`` worker).

With no new config keys present every path must resolve to the built-in
``"claude-code"`` runner, and the factory must return the same
``ClaudeAgent`` object the call site built before the router — carrying
whatever loop-wide backend it was given.
"""

from __future__ import annotations

import os
import pickle
import tempfile
import unittest
from pathlib import Path

from archon.agent import (
    DEFAULT_HARNESS,
    AgentRunner,
    ClaudeAgent,
    ClaudeBackend,
    ClaudePBackend,
    EntrypointBackend,
    UnknownHarnessError,
    build_runner,
)
from archon.commands.tooling.project_config import (
    CLAUDE_BACKEND_ENV,
    CLAUDE_P_CONFIG_DIR_ENV,
    HarnessDescriptor,
    ProjectConfig,
    has_explicit_harness_override,
    load_harness_descriptor,
    resolve_claude_backend,
    resolve_role_harness,
    resolve_subagent_harness,
)
from archon.multilane.config import multilane_config_from_simple
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
        self.assertEqual(resolve_role_harness(cfg, "plan"), "h-plan")
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
            resolve_subagent_harness(cfg, "lean-auditor", descriptor_harness="h-desc"),
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
            resolve_subagent_harness(ProjectConfig(), "x", descriptor_harness="h-desc"),
            "h-desc",
        )

    def test_bare_string_subagent_is_model_not_harness(self):
        # A bare string under subagents.<name> is the model alias for
        # backward compat (resolve_subagent_model), NOT a harness.
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
        self.assertIsNone(d.backend)

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

    def test_backend_field_parsed(self):
        cfg = ProjectConfig(
            raw={"harnesses": {"cc-p": {"runner": "claude-code", "backend": "claude-p"}}}
        )
        d = load_harness_descriptor(cfg, "cc-p")
        self.assertEqual(d.backend, "claude-p")

    def test_codex_fields_parsed(self):
        cfg = ProjectConfig(
            raw={
                "harnesses": {
                    "codex": {
                        "runner": "codex",
                        "model": "gpt-5.1-codex",
                        "effort": "xhigh",
                        "mcp": "lean-lsp",
                        "base_url_env": "CODEX_BASE_URL",
                        "key_env": "CZ_API_KEY",
                    }
                }
            }
        )
        d = load_harness_descriptor(cfg, "codex")
        self.assertEqual(d.runner, "codex")
        self.assertEqual(d.effort, "xhigh")
        self.assertEqual(d.mcp, ("lean-lsp",))
        self.assertEqual(d.base_url_env, "CODEX_BASE_URL")


    def test_shipped_codex_descriptor_available_without_config_block(self):
        d = load_harness_descriptor(ProjectConfig(), "codex")
        self.assertEqual(d.runner, "codex")
        self.assertIsNone(d.model)
        self.assertEqual(d.effort, "xhigh")
        self.assertEqual(d.mcp, ("lean-lsp",))

    def test_has_explicit_harness_override(self):
        self.assertFalse(has_explicit_harness_override(ProjectConfig(), "claude-code"))
        cfg = ProjectConfig(raw={"harnesses": {"claude-code": {"runner": "claude-code"}}})
        self.assertTrue(has_explicit_harness_override(cfg, "claude-code"))

    def test_descriptor_is_picklable(self):
        # The descriptor is threaded into the prover ProcessPoolExecutor
        # worker, so it must round-trip through pickle unchanged.
        cfg = ProjectConfig(
            raw={"harnesses": {"codex": {"runner": "codex", "mcp": ["lean-lsp"]}}}
        )
        d = load_harness_descriptor(cfg, "codex")
        self.assertEqual(pickle.loads(pickle.dumps(d)), d)


# ── build_runner ──────────────────────────────────────────────────────


class BuildRunnerTest(unittest.TestCase):
    def test_default_returns_claude_agent(self):
        r = build_runner(role="plan", model="opus")
        self.assertIsInstance(r, ClaudeAgent)
        self.assertIsInstance(r, AgentRunner)
        self.assertEqual(r.model, "opus")
        self.assertEqual(r.role, "plan")

    def _assert_legacy_claude_agent(self, got, *, model, role):
        # The legacy object the call site built before the router:
        # ClaudeAgent(model, role) with a plain default ClaudeBackend.
        # (We compare fields rather than whole-object == because the
        # backend instances have no value-equality — identity differs.)
        self.assertIsInstance(got, ClaudeAgent)
        self.assertEqual(got.model, model)
        self.assertEqual(got.role, role)
        self.assertIs(type(got.backend), ClaudeBackend)

    def test_zero_regression_invariant_empty_config(self):
        for role in ("plan", "prover", "review"):
            with self.subTest(role=role):
                got = build_runner(role=role, model="opus", cfg=ProjectConfig())
                self._assert_legacy_claude_agent(got, model="opus", role=role)

    def test_zero_regression_invariant_no_cfg(self):
        got = build_runner(role="prover", model="sonnet")
        self._assert_legacy_claude_agent(got, model="sonnet", role="prover")

    def test_explicit_claude_code_descriptor_model_override(self):
        cfg = ProjectConfig(
            raw={"harnesses": {"claude-code": {"runner": "claude-code", "model": "haiku"}}}
        )
        r = build_runner(role="review", model="opus", cfg=cfg)
        self.assertIsInstance(r, ClaudeAgent)
        self.assertEqual(r.model, "haiku")

    def test_unknown_harness_raises(self):
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
        with self.assertRaises(UnknownHarnessError):
            build_runner(role="prover", model="opus", harness="gemini-cli")

    def test_direct_claude_code_harness_short_circuits(self):
        got = build_runner(role="prover", model="opus", harness="claude-code")
        self.assertIsInstance(got, ClaudeAgent)
        self.assertEqual(got.model, "opus")
        self.assertEqual(got.role, "prover")
        self.assertIs(type(got.backend), ClaudeBackend)


# ── build_runner × claude backend (layer 2) ──────────────────────────


class BuildRunnerBackendTest(unittest.TestCase):
    """The reconciliation between the engine layer and this branch's
    pre-existing claude-backend layer (claude-p / vscode / desktop)."""

    def test_loop_wide_backend_threaded_on_default_path(self):
        # The short-circuit must still carry the loop-wide backend, or the
        # billing-change backends (claude-p, …) would silently be lost.
        b = ClaudePBackend()
        r = build_runner(role="prover", model="opus", backend=b)
        self.assertIsInstance(r, ClaudeAgent)
        self.assertIs(r.backend, b)

    def test_default_backend_is_plain_when_none_passed(self):
        r = build_runner(role="prover", model="opus")
        self.assertIsInstance(r.backend, ClaudeBackend)
        self.assertNotIsInstance(r.backend, (ClaudePBackend, EntrypointBackend))

    def test_descriptor_backend_overrides_loop_wide(self):
        # A per-harness backend override beats the threaded loop default.
        d = HarnessDescriptor(name="cc-vscode", runner="claude-code", backend="vscode")
        r = build_runner(role="prover", descriptor=d, backend=ClaudePBackend())
        self.assertIsInstance(r.backend, EntrypointBackend)
        self.assertEqual(r.backend.entrypoint, "claude-vscode")

    def test_descriptor_backend_claude_p(self):
        d = HarnessDescriptor(name="cc-p", runner="claude-code", backend="claude-p")
        r = build_runner(role="prover", descriptor=d)
        self.assertIsInstance(r.backend, ClaudePBackend)

    def test_unknown_descriptor_backend_falls_back_to_default(self):
        d = HarnessDescriptor(name="cc-x", runner="claude-code", backend="nonsense")
        r = build_runner(role="prover", descriptor=d)
        self.assertIsInstance(r.backend, ClaudeBackend)
        self.assertNotIsInstance(r.backend, (ClaudePBackend, EntrypointBackend))

    def test_descriptor_without_backend_uses_loop_default(self):
        b = EntrypointBackend("claude-desktop")
        d = HarnessDescriptor(name="claude-code", runner="claude-code")
        r = build_runner(role="prover", descriptor=d, backend=b)
        self.assertIs(r.backend, b)


# ── subagent descriptor harness frontmatter ──────────────────────────


def _write(dir: Path, name: str, frontmatter: str, body: str = "Body.") -> Path:
    path = dir / f"{name}.md"
    path.write_text("---\n" + frontmatter.rstrip() + "\n---\n\n" + body, encoding="utf-8")
    return path


class SubagentHarnessFrontmatterTest(unittest.TestCase):
    def test_harness_defaults_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d), "foo", "name: foo\ndescription: t")
            self.assertEqual(parse_descriptor_file(p).harness, "claude-code")

    def test_harness_parsed_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d), "foo", "name: foo\nharness: codex-gpt")
            self.assertEqual(parse_descriptor_file(p).harness, "codex-gpt")

    def test_unknown_frontmatter_keys_still_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            p = _write(
                Path(d), "foo", "name: foo\nharness: claude-code\nwhatever: 123",
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


# ── claude backend propagation to subagents ──────────────────────────


class BackendPropagationTest(unittest.TestCase):
    """A parent agent exports its claude backend so nested ``archon
    subagent`` dispatches (which pass no ``--claude-backend`` flag)
    re-resolve to the same backend instead of dropping to ``default``.
    """

    def setUp(self):
        # Snapshot/clear the propagation env vars so each test is isolated.
        self._saved = {
            k: os.environ.pop(k, None)
            for k in (CLAUDE_BACKEND_ENV, CLAUDE_P_CONFIG_DIR_ENV)
        }

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_backend_name_attributes(self):
        self.assertEqual(ClaudeBackend().name, "default")
        self.assertEqual(ClaudePBackend().name, "claude-p")
        self.assertEqual(EntrypointBackend("claude-vscode").name, "vscode")
        self.assertEqual(EntrypointBackend("claude-desktop").name, "desktop")
        self.assertEqual(EntrypointBackend(None).name, "default")

    def test_claude_p_agent_exports_backend_and_config_dir(self):
        ag = ClaudeAgent(
            model="opus", role="plan",
            backend=ClaudePBackend(config_dir="/tmp/cfgX"),
        )
        env = ag._build_env(None)
        self.assertEqual(env[CLAUDE_BACKEND_ENV], "claude-p")
        self.assertEqual(env[CLAUDE_P_CONFIG_DIR_ENV], "/tmp/cfgX")

    def test_default_agent_exports_default_without_config_dir(self):
        ag = ClaudeAgent(model="opus", role="plan", backend=ClaudeBackend())
        env = ag._build_env(None)
        self.assertEqual(env[CLAUDE_BACKEND_ENV], "default")
        self.assertNotIn(CLAUDE_P_CONFIG_DIR_ENV, env)

    def test_subagent_reresolves_backend_from_env(self):
        os.environ[CLAUDE_BACKEND_ENV] = "claude-p"
        os.environ[CLAUDE_P_CONFIG_DIR_ENV] = "/tmp/cfgX"
        b = resolve_claude_backend(ProjectConfig(), cli_value=None)
        self.assertIsInstance(b, ClaudePBackend)
        self.assertEqual(b.config_dir, "/tmp/cfgX")

    def test_explicit_cli_beats_env(self):
        os.environ[CLAUDE_BACKEND_ENV] = "claude-p"
        b = resolve_claude_backend(ProjectConfig(), cli_value="default")
        self.assertIs(type(b), ClaudeBackend)

    def test_config_used_when_env_absent(self):
        cfg = ProjectConfig(raw={"loop": {"claude_backend": "vscode"}})
        b = resolve_claude_backend(cfg, cli_value=None)
        self.assertIsInstance(b, EntrypointBackend)
        self.assertEqual(b.name, "vscode")


if __name__ == "__main__":
    unittest.main()

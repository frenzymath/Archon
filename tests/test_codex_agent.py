"""Tests for the Phase-2 codex harness adapter.

Covers, without ever spawning ``codex``:

* ``build_runner`` selects a ``CodexAgent`` for a codex descriptor and
  still short-circuits to ``ClaudeAgent`` when unconfigured (Phase-1
  zero-regression invariant preserved).
* ``CodexAgent`` argv builder — model / reasoning-effort / sandbox /
  gateway ``-c`` overrides / ``--json`` mirror the bash reference runner,
  and the gateway secret never lands in argv.
* ``CodexAgent`` env builder — gateway creds resolved from the configured
  env var names; ``env_overrides`` merged on top.
* ``HarnessDescriptor`` parses codex fields from a ``harnesses.codex-gpt``
  config block.
* The resolved descriptor is picklable (round-trips), locking in the
  process-pool / cross-process threading path.
* (guarded) a live smoke test, skipped unless ``codex`` is on PATH AND
  gateway creds are set.
"""

from __future__ import annotations

import json
import os
import pickle
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import archon.commands.init.utils as init_utils
from archon.agent import ClaudeAgent, build_runner
from archon.agents.codex import (
    _CODEX_STREAM_PARSER,
    CodexAgent,
    LeanLspMcpUnavailableError,
    PartialGatewayConfigError,
    UnknownMcpBundleError,
    resolve_prompt_variant,
)
from archon.commands.tooling.project_config import (
    HarnessDescriptor,
    ProjectConfig,
    load_harness_descriptor,
)


# A representative codex config block, mirroring
# FormalQualBench/harness/configs/codex-gpt-5.5/config.sh.
CODEX_CFG = {
    "harnesses": {
        "codex-gpt": {
            "runner": "codex",
            "model": "gpt-5.5-xhigh",
            "effort": "xhigh",
            "sandbox": "danger-full-access",
            "base_url_env": "CODEX_BASE_URL",
            "key_env": "CZ_API_KEY",
            "wire_api": "responses",
            "extra_args": "-c features.plugins=false",
        }
    },
    "loop": {"roles": {"prover": "codex-gpt"}},
}


def _codex_descriptor() -> HarnessDescriptor:
    return load_harness_descriptor(ProjectConfig(raw=CODEX_CFG), "codex-gpt")


# ── HarnessDescriptor codex fields ───────────────────────────────────


class CodexDescriptorParseTest(unittest.TestCase):
    def test_codex_fields_parsed(self):
        d = _codex_descriptor()
        self.assertEqual(d.runner, "codex")
        self.assertEqual(d.model, "gpt-5.5-xhigh")
        self.assertEqual(d.effort, "xhigh")
        self.assertEqual(d.sandbox, "danger-full-access")
        self.assertEqual(d.base_url_env, "CODEX_BASE_URL")
        self.assertEqual(d.key_env, "CZ_API_KEY")
        self.assertEqual(d.wire_api, "responses")

    def test_defaults_when_codex_fields_absent(self):
        cfg = ProjectConfig(raw={"harnesses": {"c": {"runner": "codex"}}})
        d = load_harness_descriptor(cfg, "c")
        self.assertEqual(d.runner, "codex")
        self.assertIsNone(d.model)
        self.assertIsNone(d.effort)
        # Sane defaults: baseline parity sandbox + responses wire api.
        self.assertEqual(d.sandbox, "danger-full-access")
        self.assertEqual(d.wire_api, "responses")
        self.assertIsNone(d.base_url_env)

    def test_claude_code_descriptor_unaffected(self):
        # The added codex fields must not change claude-code parsing.
        cfg = ProjectConfig(
            raw={"harnesses": {"claude-code": {"runner": "claude-code", "model": "haiku"}}}
        )
        d = load_harness_descriptor(cfg, "claude-code")
        self.assertEqual(d.runner, "claude-code")
        self.assertEqual(d.model, "haiku")
        self.assertIsNone(d.effort)

    def test_descriptor_is_picklable(self):
        # Locks in the process-pool path: the resolved descriptor is
        # threaded into the worker and must survive pickle round-trip.
        d = _codex_descriptor()
        d2 = pickle.loads(pickle.dumps(d))
        self.assertEqual(d, d2)
        self.assertEqual(d2.runner, "codex")
        self.assertEqual(d2.effort, "xhigh")


# ── build_runner dispatch ────────────────────────────────────────────


class CodexBuildRunnerTest(unittest.TestCase):
    def test_codex_selected_for_codex_descriptor(self):
        cfg = ProjectConfig(raw=CODEX_CFG)
        r = build_runner(role="prover", model="opus", cfg=cfg)
        self.assertIsInstance(r, CodexAgent)
        self.assertEqual(r.role, "prover")
        self.assertEqual(r.model, "gpt-5.5-xhigh")

    def test_codex_selected_from_resolved_descriptor(self):
        # The pool worker / lane / subagent path: pass the resolved
        # descriptor directly (no cfg).
        r = build_runner(role="prover", model="opus", descriptor=_codex_descriptor())
        self.assertIsInstance(r, CodexAgent)

    def test_non_prover_role_stays_claude_code(self):
        # loop.roles.prover routes only the prover to codex; plan/review
        # have no override → built-in claude-code.
        cfg = ProjectConfig(raw=CODEX_CFG)
        for role in ("plan", "review"):
            with self.subTest(role=role):
                r = build_runner(role=role, model="opus", cfg=cfg)
                self.assertIsInstance(r, ClaudeAgent)

    def test_zero_regression_invariant_still_holds(self):
        # Empty config => exactly the legacy ClaudeAgent, untouched by the
        # codex plumbing.
        got = build_runner(role="prover", model="opus", cfg=ProjectConfig())
        self.assertEqual(got, ClaudeAgent(model="opus", role="prover"))


# ── CodexAgent argv builder (pure; no subprocess) ────────────────────


class CodexArgvBuilderTest(unittest.TestCase):
    def setUp(self):
        self.agent = CodexAgent(descriptor=_codex_descriptor(), role="prover")
        # Gateway creds supplied via an explicit env_source so the test
        # doesn't depend on the ambient shell.
        self.env_source = {
            "CODEX_BASE_URL": "https://apicz.boyuerichdata.com/v1",
            "CZ_API_KEY": "SECRET-KEY-123",
        }

    def test_core_codex_flags(self):
        argv = self.agent.build_argv("THE PROMPT", env_source=self.env_source)
        self.assertEqual(argv[0], "codex")
        self.assertEqual(argv[1], "exec")
        self.assertIn("--json", argv)
        self.assertIn("--skip-git-repo-check", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--ephemeral", argv)
        # model
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-5.5-xhigh")
        # prompt is the final positional
        self.assertEqual(argv[-1], "THE PROMPT")

    def test_reasoning_effort_override(self):
        argv = self.agent.build_argv("p", env_source=self.env_source)
        self.assertIn('model_reasoning_effort="xhigh"', argv)

    def test_sandbox_flag(self):
        argv = self.agent.build_argv("p", env_source=self.env_source)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "danger-full-access")

    def test_gateway_provider_overrides(self):
        argv = self.agent.build_argv("p", env_source=self.env_source)
        self.assertIn('model_provider="harness-gateway"', argv)
        self.assertIn(
            'model_providers.harness-gateway.base_url="https://apicz.boyuerichdata.com/v1"',
            argv,
        )
        self.assertIn(
            'model_providers.harness-gateway.env_key="CODEX_GATEWAY_API_KEY"', argv
        )
        self.assertIn(
            'model_providers.harness-gateway.wire_api="responses"', argv
        )

    def test_gateway_secret_never_in_argv(self):
        # The key is read by codex from CODEX_GATEWAY_API_KEY in the env,
        # NOT passed on the command line (where it would leak into `ps`).
        argv = self.agent.build_argv("p", env_source=self.env_source)
        self.assertFalse(any("SECRET-KEY-123" in a for a in argv))

    def test_no_gateway_when_unconfigured(self):
        # Descriptor without base_url_env → native login, no provider -c.
        agent = CodexAgent(
            descriptor=HarnessDescriptor(name="c", runner="codex", model="m")
        )
        argv = agent.build_argv("p", env_source={})
        self.assertFalse(any("model_provider" in a for a in argv))

    def test_descriptor_extra_args_appended(self):
        argv = self.agent.build_argv("p", env_source=self.env_source)
        self.assertIn("features.plugins=false", argv)

    def test_call_site_extra_args_after_descriptor(self):
        argv = self.agent.build_argv(
            "p", extra_args=["--add-dir", "/x"], env_source=self.env_source
        )
        prompt_i = argv.index("p")
        add_i = argv.index("--add-dir")
        self.assertLess(add_i, prompt_i)

    def test_last_message_path_flag(self):
        from pathlib import Path

        argv = self.agent.build_argv(
            "p", last_message_path=Path("/t/last.txt"), env_source=self.env_source
        )
        self.assertEqual(argv[argv.index("-o") + 1], "/t/last.txt")


# ── CodexAgent env builder ───────────────────────────────────────────


class CodexEnvBuilderTest(unittest.TestCase):
    def setUp(self):
        self.agent = CodexAgent(descriptor=_codex_descriptor())
        self._saved = {
            k: os.environ.get(k) for k in ("CODEX_BASE_URL", "CZ_API_KEY")
        }
        os.environ["CODEX_BASE_URL"] = "https://gw.example/v1"
        os.environ["CZ_API_KEY"] = "ABC-TOKEN"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_gateway_key_copied_into_codex_gateway_api_key(self):
        env = self.agent.build_env()
        self.assertEqual(env["CODEX_GATEWAY_API_KEY"], "ABC-TOKEN")

    def test_env_overrides_merged_and_win(self):
        # Overrides merge in, and an override of the descriptor's key_env
        # (CZ_API_KEY) wins: gateway creds are resolved from the merged env,
        # so CODEX_GATEWAY_API_KEY is derived from the overridden source key
        # (not the ambient ABC-TOKEN). This keeps build_env consistent with
        # build_argv, which reads the same merged env.
        env = self.agent.build_env({"PARENT_SLUG": "foo", "CZ_API_KEY": "OVERRIDE"})
        self.assertEqual(env["PARENT_SLUG"], "foo")
        self.assertEqual(env["CODEX_GATEWAY_API_KEY"], "OVERRIDE")

    def test_no_gateway_key_when_unconfigured(self):
        agent = CodexAgent(
            descriptor=HarnessDescriptor(name="c", runner="codex")
        )
        env = agent.build_env()
        self.assertNotIn("CODEX_GATEWAY_API_KEY", env)


# ── build_env / build_argv resolve gateway creds from the SAME env ────


class CodexEnvOverrideConsistencyTest(unittest.TestCase):
    """``build_env`` must resolve gateway creds from the *merged* env.

    Regression for PR #10: when a lane supplies gateway creds via
    ``env_overrides`` (e.g. ``LaneConfig.env`` → ``env_overrides``),
    ``build_env`` previously resolved creds from the pre-merge
    ``os.environ`` and never set ``CODEX_GATEWAY_API_KEY``, while
    ``build_argv`` (reading the post-merge env passed by ``run()``) still
    injected the gateway provider that reads that var → auth failure. The
    two methods must agree on a single env snapshot.
    """

    def setUp(self):
        self.agent = CodexAgent(descriptor=_codex_descriptor(), role="prover")
        # Clear ambient gateway env so env_overrides is the only source.
        self._saved = {
            k: os.environ.get(k) for k in ("CODEX_BASE_URL", "CZ_API_KEY")
        }
        os.environ.pop("CODEX_BASE_URL", None)
        os.environ.pop("CZ_API_KEY", None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_creds_only_via_overrides_set_key_and_inject_provider(self):
        # The bug: creds arrive ONLY through env_overrides (nothing in
        # os.environ). build_env must set CODEX_GATEWAY_API_KEY, and
        # build_argv fed that merged env must inject the provider block.
        overrides = {
            "CODEX_BASE_URL": "https://lane.example/v1",
            "CZ_API_KEY": "LANE-KEY-789",
        }
        env = self.agent.build_env(overrides)
        self.assertEqual(env["CODEX_GATEWAY_API_KEY"], "LANE-KEY-789")
        # run() passes this build_env result as env_source to build_argv.
        argv = self.agent.build_argv("p", env_source=env)
        self.assertIn('model_provider="harness-gateway"', argv)
        self.assertIn(
            'model_providers.harness-gateway.base_url="https://lane.example/v1"',
            argv,
        )
        self.assertIn(
            'model_providers.harness-gateway.env_key="CODEX_GATEWAY_API_KEY"',
            argv,
        )

    def test_override_wins_over_os_environ_key(self):
        # A gateway key in os.environ overridden by env_overrides →
        # CODEX_GATEWAY_API_KEY reflects the override value, not the ambient.
        os.environ["CODEX_BASE_URL"] = "https://gw.example/v1"
        os.environ["CZ_API_KEY"] = "AMBIENT-KEY"
        env = self.agent.build_env({"CZ_API_KEY": "OVERRIDE-KEY"})
        self.assertEqual(env["CODEX_GATEWAY_API_KEY"], "OVERRIDE-KEY")

    def test_os_environ_creds_no_overrides_unchanged(self):
        # Regression: the common path (creds in os.environ, no overrides)
        # behaves exactly as before — CODEX_GATEWAY_API_KEY set from ambient.
        os.environ["CODEX_BASE_URL"] = "https://gw.example/v1"
        os.environ["CZ_API_KEY"] = "AMBIENT-KEY"
        env = self.agent.build_env()
        self.assertEqual(env["CODEX_GATEWAY_API_KEY"], "AMBIENT-KEY")

    def test_partial_in_environ_completed_by_override_no_raise(self):
        # base_url in os.environ, key absent there but supplied via
        # env_overrides → the merged env is complete → no raise, key set.
        os.environ["CODEX_BASE_URL"] = "https://gw.example/v1"
        os.environ.pop("CZ_API_KEY", None)
        env = self.agent.build_env({"CZ_API_KEY": "FILLED-IN-KEY"})
        self.assertEqual(env["CODEX_GATEWAY_API_KEY"], "FILLED-IN-KEY")

    def test_still_partial_after_merge_raises(self):
        # base_url set (here via override) but key missing everywhere →
        # still partially configured after the merge → still raises.
        os.environ.pop("CODEX_BASE_URL", None)
        os.environ.pop("CZ_API_KEY", None)
        with self.assertRaises(PartialGatewayConfigError) as cm:
            self.agent.build_env({"CODEX_BASE_URL": "https://gw.example/v1"})
        self.assertIn("CZ_API_KEY", str(cm.exception))


# ── partial gateway config fails loud (mirror run_session.sh) ────────


class CodexPartialGatewayTest(unittest.TestCase):
    """A half-configured gateway must raise, not silently go native.

    Mirrors the bash runner's
    ``: "${CODEX_API_KEY:?CODEX_BASE_URL set but CODEX_API_KEY missing}"``
    guard. The descriptor configures a gateway by naming ``base_url_env``;
    once configured, both env vars must resolve.
    """

    def setUp(self):
        self.agent = CodexAgent(descriptor=_codex_descriptor(), role="prover")

    def test_base_url_set_key_missing_raises_naming_key_env(self):
        # base_url resolves, key env absent → loud failure naming CZ_API_KEY.
        env = {"CODEX_BASE_URL": "https://gw.example/v1"}
        with self.assertRaises(PartialGatewayConfigError) as cm:
            self.agent.build_argv("p", env_source=env)
        self.assertIn("CZ_API_KEY", str(cm.exception))
        self.assertIn("CODEX_BASE_URL", str(cm.exception))

    def test_base_url_set_key_empty_string_raises(self):
        # An exported-but-empty key is treated as missing, like bash's `:?`.
        env = {"CODEX_BASE_URL": "https://gw.example/v1", "CZ_API_KEY": ""}
        with self.assertRaises(PartialGatewayConfigError):
            self.agent.build_argv("p", env_source=env)

    def test_key_set_base_url_missing_raises(self):
        # Symmetric vice-versa: key present, base_url absent → refuse to
        # half-apply the gateway.
        env = {"CZ_API_KEY": "SECRET"}
        with self.assertRaises(PartialGatewayConfigError) as cm:
            self.agent.build_argv("p", env_source=env)
        self.assertIn("CODEX_BASE_URL", str(cm.exception))

    def test_build_env_also_fails_loud_on_partial(self):
        # The env builder shares _gateway_creds, so a partial config is
        # caught at child-env assembly time too (before spawning codex).
        saved = {k: os.environ.get(k) for k in ("CODEX_BASE_URL", "CZ_API_KEY")}
        os.environ.pop("CZ_API_KEY", None)
        os.environ["CODEX_BASE_URL"] = "https://gw.example/v1"
        try:
            with self.assertRaises(PartialGatewayConfigError):
                self.agent.build_env()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_both_set_still_builds_gateway(self):
        # Regression guard: the both-set case is unchanged (no raise).
        env = {
            "CODEX_BASE_URL": "https://gw.example/v1",
            "CZ_API_KEY": "SECRET",
        }
        argv = self.agent.build_argv("p", env_source=env)
        self.assertIn('model_provider="harness-gateway"', argv)

    def test_neither_set_stays_native(self):
        # Gateway configured by descriptor but neither env var present →
        # native login, no raise, no provider -c flags.
        argv = self.agent.build_argv("p", env_source={})
        self.assertFalse(any("model_provider" in a for a in argv))

    def test_unconfigured_descriptor_never_raises(self):
        # No base_url_env at all → gateway not configured; partial-config
        # guard does not apply even if a stray key env happens to be set.
        agent = CodexAgent(
            descriptor=HarnessDescriptor(name="c", runner="codex", model="m")
        )
        argv = agent.build_argv("p", env_source={"CZ_API_KEY": "stray"})
        self.assertFalse(any("model_provider" in a for a in argv))


# ── run_interactive is headless-only ─────────────────────────────────


class CodexInteractiveTest(unittest.TestCase):
    def test_run_interactive_raises(self):
        from pathlib import Path

        agent = CodexAgent(descriptor=_codex_descriptor())
        with self.assertRaises(NotImplementedError) as cm:
            agent.run_interactive("p", cwd=Path("."))
        self.assertIn("headless-only", str(cm.exception))


# ── cross-process descriptor threading (process pool) ────────────────


def _build_runner_kind_in_worker(descriptor: HarnessDescriptor) -> str:
    """Top-level (picklable) worker: rebuild the runner from a descriptor.

    Runs in a ``ProcessPoolExecutor`` worker, exactly like the prover
    pool's ``_run_single_prover`` — the descriptor must pickle across the
    process boundary and yield a CodexAgent on the far side.
    """
    from archon.agent import build_runner

    r = build_runner(role="prover", model="opus", descriptor=descriptor)
    return type(r).__name__


class CodexCrossProcessTest(unittest.TestCase):
    def test_codex_descriptor_yields_codex_agent_in_worker(self):
        from concurrent.futures import ProcessPoolExecutor

        d = _codex_descriptor()
        with ProcessPoolExecutor(max_workers=1) as pool:
            kind = pool.submit(_build_runner_kind_in_worker, d).result()
        self.assertEqual(kind, "CodexAgent")

    def test_claude_descriptor_yields_claude_agent_in_worker(self):
        from concurrent.futures import ProcessPoolExecutor

        d = HarnessDescriptor(name="claude-code", runner="claude-code")
        with ProcessPoolExecutor(max_workers=1) as pool:
            kind = pool.submit(_build_runner_kind_in_worker, d).result()
        self.assertEqual(kind, "ClaudeAgent")


# ── guarded live smoke test (skipped by default) ─────────────────────


@unittest.skipUnless(
    shutil.which("codex") and os.environ.get("CODEX_BASE_URL") and os.environ.get("CZ_API_KEY"),
    "live codex smoke: needs codex on PATH + CODEX_BASE_URL + CZ_API_KEY",
)
class CodexLiveSmokeTest(unittest.TestCase):
    def test_trivial_run(self):
        import tempfile
        from pathlib import Path

        agent = CodexAgent(descriptor=_codex_descriptor(), role="prover")
        with tempfile.TemporaryDirectory() as d:
            ok = agent.run(
                "Reply with the single word OK and stop.",
                cwd=Path(d),
                log_base=Path(d) / "smoke",
                idle_timeout_s=120,
                max_attempts=1,
            )
            self.assertIsInstance(ok, bool)


# ── HarnessDescriptor.mcp parsing ────────────────────────────────────


class CodexMcpDescriptorParseTest(unittest.TestCase):
    def test_mcp_string_parses_to_tuple(self):
        cfg = ProjectConfig(
            raw={"harnesses": {"codex-gpt": {"runner": "codex", "mcp": "lean-lsp"}}}
        )
        d = load_harness_descriptor(cfg, "codex-gpt")
        self.assertEqual(d.mcp, ("lean-lsp",))

    def test_mcp_list_parses_to_tuple(self):
        # The list shape is accepted for future multi-server bundles.
        cfg = ProjectConfig(
            raw={"harnesses": {"c": {"runner": "codex", "mcp": ["lean-lsp", "other"]}}}
        )
        d = load_harness_descriptor(cfg, "c")
        self.assertEqual(d.mcp, ("lean-lsp", "other"))

    def test_mcp_absent_defaults_empty(self):
        # The whole point of the default: absent ⇒ no MCP (current behavior).
        d = _codex_descriptor()
        self.assertEqual(d.mcp, ())

    def test_mcp_empty_string_is_no_mcp(self):
        cfg = ProjectConfig(raw={"harnesses": {"c": {"runner": "codex", "mcp": ""}}})
        d = load_harness_descriptor(cfg, "c")
        self.assertEqual(d.mcp, ())

    def test_descriptor_with_mcp_is_picklable(self):
        cfg = ProjectConfig(
            raw={"harnesses": {"c": {"runner": "codex", "mcp": "lean-lsp"}}}
        )
        d = load_harness_descriptor(cfg, "c")
        d2 = pickle.loads(pickle.dumps(d))
        self.assertEqual(d2.mcp, ("lean-lsp",))


# ── CodexAgent MCP -c overrides (Task A) ─────────────────────────────


def _codex_mcp_descriptor() -> HarnessDescriptor:
    cfg = {
        "harnesses": {
            "codex-gpt": {**CODEX_CFG["harnesses"]["codex-gpt"], "mcp": "lean-lsp"}
        }
    }
    return load_harness_descriptor(ProjectConfig(raw=cfg), "codex-gpt")


class CodexMcpArgvTest(unittest.TestCase):
    def setUp(self):
        self.agent = CodexAgent(descriptor=_codex_mcp_descriptor(), role="prover")
        self.env_source = {
            "CODEX_BASE_URL": "https://apicz.boyuerichdata.com/v1",
            "CZ_API_KEY": "SECRET-KEY-123",
        }
        # The data_path-resolved lean-lsp-mcp dir (same as the init step).
        self.mcp_dir = str(Path(init_utils.data_path("tools/lean-lsp-mcp")))

    def _value_for(self, argv, key):
        """Find the JSON value emitted for a given `-c key=...` flag."""
        prefix = f"{key}="
        for a in argv:
            if a.startswith(prefix):
                return json.loads(a[len(prefix):])
        return None

    def test_five_lean_lsp_overrides_present(self):
        argv = self.agent.build_argv(
            "p", env_source=self.env_source, lake_root="/proj/lake"
        )
        base = "mcp_servers.archon-lean-lsp"
        self.assertEqual(self._value_for(argv, f"{base}.command"), "uv")
        self.assertEqual(
            self._value_for(argv, f"{base}.args"),
            ["run", "--directory", self.mcp_dir, "lean-lsp-mcp"],
        )
        self.assertEqual(
            self._value_for(argv, f"{base}.env.LEAN_PROJECT_PATH"), "/proj/lake"
        )
        self.assertIs(self._value_for(argv, f"{base}.required"), True)
        self.assertEqual(self._value_for(argv, f"{base}.tool_timeout_sec"), 600)

    def test_args_point_at_data_path(self):
        argv = self.agent.build_argv(
            "p", env_source=self.env_source, lake_root="/proj/lake"
        )
        args = self._value_for(argv, "mcp_servers.archon-lean-lsp.args")
        # The --directory must be the data_path-resolved lean-lsp-mcp dir.
        self.assertEqual(args[args.index("--directory") + 1], self.mcp_dir)

    def test_lake_root_flows_into_lean_project_path(self):
        argv = self.agent.build_argv(
            "p", env_source=self.env_source, lake_root=Path("/some/other/root")
        )
        self.assertEqual(
            self._value_for(
                argv, "mcp_servers.archon-lean-lsp.env.LEAN_PROJECT_PATH"
            ),
            "/some/other/root",
        )

    def test_mcp_flags_are_minus_c_pairs(self):
        # Each override is rendered as a `-c key=value` pair (TOML literal).
        argv = self.agent.build_argv(
            "p", env_source=self.env_source, lake_root="/r"
        )
        for i, a in enumerate(argv):
            if a.startswith("mcp_servers.archon-lean-lsp"):
                self.assertEqual(argv[i - 1], "-c")

    def test_key_still_not_in_argv_with_mcp_on(self):
        # MCP wiring must not regress the secret-out-of-argv invariant.
        argv = self.agent.build_argv(
            "p", env_source=self.env_source, lake_root="/r"
        )
        self.assertFalse(any("SECRET-KEY-123" in a for a in argv))

    def test_no_mcp_flags_when_unset(self):
        # The default descriptor (no mcp) renders zero mcp_servers flags —
        # identical to today.
        agent = CodexAgent(descriptor=_codex_descriptor(), role="prover")
        argv = agent.build_argv(
            "p", env_source=self.env_source, lake_root="/r"
        )
        self.assertFalse(any("mcp_servers" in a for a in argv))

    def test_unknown_bundle_raises(self):
        d = HarnessDescriptor(name="c", runner="codex", mcp=("frobnicate",))
        agent = CodexAgent(descriptor=d)
        with self.assertRaises(UnknownMcpBundleError) as cm:
            agent.build_argv("p", env_source={}, lake_root="/r")
        self.assertIn("frobnicate", str(cm.exception))

    def test_missing_lean_lsp_dir_raises(self):
        # Monkeypatch data_path to a missing path → fail-closed rather than
        # render a broken server (mirrors the harness's file check).
        d = HarnessDescriptor(name="c", runner="codex", mcp=("lean-lsp",))
        agent = CodexAgent(descriptor=d)
        orig = init_utils.data_path
        init_utils.data_path = lambda sub="": Path("/no/such/dir/" + sub)
        try:
            with self.assertRaises(LeanLspMcpUnavailableError) as cm:
                agent.build_argv("p", env_source={}, lake_root="/r")
            self.assertIn("does not exist", str(cm.exception))
        finally:
            init_utils.data_path = orig

    def test_lake_root_defaults_to_cwd_when_omitted(self):
        # A direct build_argv caller that omits lake_root still renders a
        # valid server (LEAN_PROJECT_PATH = process cwd); run() always
        # supplies its cwd, so this is just the last-resort fallback.
        argv = self.agent.build_argv("p", env_source=self.env_source)
        val = self._value_for(
            argv, "mcp_servers.archon-lean-lsp.env.LEAN_PROJECT_PATH"
        )
        self.assertEqual(val, str(Path.cwd()))


# ── prompt_variant resolution + append (Task B) ──────────────────────


class CodexPromptVariantTest(unittest.TestCase):
    def test_bundled_codex_variant_resolves(self):
        # The shipped codex variant is found via the bundled data path.
        text = resolve_prompt_variant("codex")
        self.assertIsNotNone(text)
        self.assertIn("apply_patch", text)
        self.assertIn("archon-lean-lsp", text)

    def test_missing_variant_resolves_none(self):
        self.assertIsNone(resolve_prompt_variant("does-not-exist-xyz"))

    def test_append_when_variant_set(self):
        d = HarnessDescriptor(name="c", runner="codex", prompt_variant="codex")
        agent = CodexAgent(descriptor=d)
        with tempfile.TemporaryDirectory() as proj:
            out = agent._apply_prompt_variant("BASE", project_path=Path(proj))
        self.assertTrue(out.startswith("BASE"))
        self.assertIn("Faithfulness", out)
        self.assertGreater(len(out), len("BASE"))

    def test_unset_variant_leaves_prompt_unchanged(self):
        agent = CodexAgent(descriptor=HarnessDescriptor(name="c", runner="codex"))
        with tempfile.TemporaryDirectory() as proj:
            self.assertEqual(
                agent._apply_prompt_variant("BASE", project_path=Path(proj)),
                "BASE",
            )

    def test_missing_variant_file_warns_and_continues(self):
        # prompt_variant names a file that doesn't exist anywhere → the run
        # must not crash; the prompt comes back unchanged.
        d = HarnessDescriptor(
            name="c", runner="codex", prompt_variant="nope-missing-variant"
        )
        agent = CodexAgent(descriptor=d)
        with tempfile.TemporaryDirectory() as proj:
            out = agent._apply_prompt_variant("BASE", project_path=Path(proj))
        self.assertEqual(out, "BASE")

    def test_append_is_deterministic(self):
        d = HarnessDescriptor(name="c", runner="codex", prompt_variant="codex")
        agent = CodexAgent(descriptor=d)
        with tempfile.TemporaryDirectory() as proj:
            a = agent._apply_prompt_variant("BASE", project_path=Path(proj))
            b = agent._apply_prompt_variant("BASE", project_path=Path(proj))
        self.assertEqual(a, b)

    def test_project_local_variant_wins_over_bundled(self):
        # A project-local .archon/prompts/variants/codex.md overrides the
        # bundled copy (local-overrides-global, like Archon's other prompts).
        d = HarnessDescriptor(name="c", runner="codex", prompt_variant="codex")
        agent = CodexAgent(descriptor=d)
        with tempfile.TemporaryDirectory() as proj:
            vdir = Path(proj) / ".archon" / "prompts" / "variants"
            vdir.mkdir(parents=True)
            (vdir / "codex.md").write_text(
                "PROJECT-LOCAL-OVERRIDE-MARKER", encoding="utf-8"
            )
            out = agent._apply_prompt_variant("BASE", project_path=Path(proj))
        self.assertIn("PROJECT-LOCAL-OVERRIDE-MARKER", out)
        # The bundled content must NOT also be appended.
        self.assertNotIn("apply_patch", out)

    def test_resolve_prompt_variant_prefers_project_local(self):
        with tempfile.TemporaryDirectory() as proj:
            vdir = Path(proj) / ".archon" / "prompts" / "variants"
            vdir.mkdir(parents=True)
            (vdir / "codex.md").write_text("LOCAL", encoding="utf-8")
            self.assertEqual(
                resolve_prompt_variant("codex", project_path=Path(proj)), "LOCAL"
            )


class CodexStreamParserTest(unittest.TestCase):
    """The peer normaliser turns codex's ``exec --json`` thread/turn/item
    stream into the same downstream schema claude-code produces, so the
    dashboard + cost/token aggregators stay harness-agnostic. Runs the
    embedded parser script as a subprocess (python, not codex) over a
    synthetic stream — no network, no codex binary.
    """

    def _run_parser(self, events, *, model="gpt-5.5", verbose=False):
        stream = "\n".join(json.dumps(o) for o in events)
        with tempfile.TemporaryDirectory() as d:
            jsonl = os.path.join(d, "out.jsonl")
            raw = os.path.join(d, "out.raw.jsonl")
            script = _CODEX_STREAM_PARSER.format(
                verbose=str(verbose), raw_log=raw, jsonl=jsonl, model=model
            )
            proc = subprocess.run(
                [sys.executable, "-u", "-c", script],
                input=stream,
                text=True,
                capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            rows = [json.loads(l) for l in open(jsonl) if l.strip()]
            raw_exists = os.path.exists(raw)
        return rows, proc.stdout, raw_exists

    def _events(self, rows):
        return [r["event"] for r in rows]

    def _session_end(self, rows):
        ends = [r for r in rows if r["event"] == "session_end"]
        self.assertEqual(len(ends), 1, "exactly one session_end expected")
        return ends[0]

    def test_full_stream_normalizes_to_shared_schema(self):
        rows, _stdout, _raw = self._run_parser([
            {"type": "thread.started", "thread_id": "abc-123"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {
                "id": "m0", "type": "agent_message", "text": "Planning."}},
            {"type": "item.started", "item": {
                "id": "c1", "type": "command_execution",
                "command": "lake build", "aggregated_output": "",
                "exit_code": None, "status": "in_progress"}},
            {"type": "item.completed", "item": {
                "id": "c1", "type": "command_execution",
                "command": "lake build", "aggregated_output": "ok",
                "exit_code": 0, "status": "completed"}},
            {"type": "item.completed", "item": {
                "id": "x2", "type": "mcp_tool_call",
                "server": "archon-lean-lsp", "tool": "lean_diagnostic_messages",
                "arguments": {"file": "X.lean"}, "result": {"errors": 0},
                "error": None, "status": "completed"}},
            {"type": "item.completed", "item": {
                "id": "f3", "type": "file_change",
                "changes": [{"path": "X.lean", "kind": "update"}],
                "status": "completed"}},
            {"type": "item.completed", "item": {
                "id": "m9", "type": "agent_message", "text": "Done."}},
            {"type": "turn.completed", "usage": {
                "input_tokens": 2607762, "cached_input_tokens": 2506880,
                "output_tokens": 23344, "reasoning_output_tokens": 9148}},
        ])
        events = self._events(rows)
        # session_meta from thread.started, both agent_messages as text,
        # each tool item a call+result, a closing session_end.
        self.assertEqual(events[0], "session_meta")
        self.assertEqual(rows[0]["session_id"], "abc-123")
        self.assertEqual(events.count("text"), 2)
        self.assertEqual(events.count("tool_call"), 3)
        self.assertEqual(events.count("tool_result"), 3)
        self.assertEqual(events[-1], "session_end")
        # Tool labels are claude-style so the dashboard renders them.
        labels = [r["tool"] for r in rows if r["event"] == "tool_call"]
        self.assertEqual(labels, ["Bash", "lean_diagnostic_messages", "Edit"])
        # num_turns reports codex's iteration count (completed items), not the
        # always-1 turn.completed count: m0, c1, x2, f3, m9 = 5 items.
        se = self._session_end(rows)
        self.assertEqual(se["num_turns"], 5)
        self.assertEqual(se["num_items"], 5)

    def test_session_end_token_breakdown_no_cost(self):
        rows, _stdout, _raw = self._run_parser([
            {"type": "thread.started", "thread_id": "t"},
            {"type": "item.completed", "item": {
                "id": "m", "type": "agent_message", "text": "ok"}},
            {"type": "turn.completed", "usage": {
                "input_tokens": 2607762, "cached_input_tokens": 2506880,
                "output_tokens": 23344, "reasoning_output_tokens": 9148}},
        ])
        se = self._session_end(rows)
        # fresh input = total - cached; cached → cache_read; no USD cost.
        self.assertEqual(se["input_tokens"], 2607762 - 2506880)
        self.assertEqual(se["cache_read_input_tokens"], 2506880)
        self.assertEqual(se["output_tokens"], 23344)
        self.assertEqual(se["reasoning_output_tokens"], 9148)
        self.assertEqual(se["input_tokens_total"], 2607762)
        self.assertEqual(se["num_turns"], 1)  # 1 completed item = 1 iteration
        self.assertEqual(se["num_items"], 1)
        self.assertNotIn("total_cost_usd", se)
        self.assertEqual(
            se["model_usage"],
            {"gpt-5.5": {"inputTokens": 100882, "outputTokens": 23344}},
        )

    def test_multiple_turns_accumulate(self):
        # Usage sums across every turn.completed (codex normally emits one,
        # but be robust to more). num_turns counts *items*, not turn.completed
        # events, so this item-free stream reports 0.
        rows, _stdout, _raw = self._run_parser([
            {"type": "thread.started", "thread_id": "t"},
            {"type": "turn.completed", "usage": {
                "input_tokens": 100, "cached_input_tokens": 20,
                "output_tokens": 5, "reasoning_output_tokens": 1}},
            {"type": "turn.completed", "usage": {
                "input_tokens": 200, "cached_input_tokens": 30,
                "output_tokens": 7, "reasoning_output_tokens": 2}},
        ])
        se = self._session_end(rows)
        self.assertEqual(se["num_turns"], 0)
        self.assertEqual(se["num_items"], 0)
        self.assertEqual(se["input_tokens"], (100 + 200) - (20 + 30))
        self.assertEqual(se["cache_read_input_tokens"], 50)
        self.assertEqual(se["output_tokens"], 12)
        self.assertEqual(se["reasoning_output_tokens"], 3)

    def test_completed_tool_without_started_still_emits_call(self):
        # A tool item that only ever produced item.completed (no started)
        # must still surface a tool_call before its tool_result.
        rows, _stdout, _raw = self._run_parser([
            {"type": "thread.started", "thread_id": "t"},
            {"type": "item.completed", "item": {
                "id": "c1", "type": "command_execution",
                "command": "echo hi", "aggregated_output": "hi",
                "exit_code": 0, "status": "completed"}},
            {"type": "turn.completed", "usage": {
                "input_tokens": 1, "cached_input_tokens": 0,
                "output_tokens": 1, "reasoning_output_tokens": 0}},
        ])
        events = self._events(rows)
        self.assertEqual(events.count("tool_call"), 1)
        self.assertEqual(events.count("tool_result"), 1)
        self.assertLess(events.index("tool_call"), events.index("tool_result"))
        self.assertEqual(self._session_end(rows)["num_turns"], 1)  # 1 item

    def test_empty_stream_still_closes_with_session_end(self):
        # A run that produces nothing (killed early) still gets a
        # session_end with zeroed tokens so downstream readers don't choke.
        rows, _stdout, _raw = self._run_parser([])
        se = self._session_end(rows)
        self.assertEqual(se["num_turns"], 0)
        self.assertEqual(se["input_tokens"], 0)
        self.assertEqual(se["output_tokens"], 0)
        self.assertNotIn("total_cost_usd", se)

    def test_verbose_writes_raw_log(self):
        events = [
            {"type": "thread.started", "thread_id": "t"},
            {"type": "turn.completed", "usage": {
                "input_tokens": 1, "cached_input_tokens": 0,
                "output_tokens": 1, "reasoning_output_tokens": 0}},
        ]
        _rows, _stdout, raw_exists_verbose = self._run_parser(events, verbose=True)
        self.assertTrue(raw_exists_verbose)
        _rows, _stdout, raw_exists_quiet = self._run_parser(events, verbose=False)
        self.assertFalse(raw_exists_quiet)


if __name__ == "__main__":
    unittest.main()

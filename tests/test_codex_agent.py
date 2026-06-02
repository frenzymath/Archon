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

import os
import pickle
import shutil
import unittest

from archon.agent import ClaudeAgent, build_runner
from archon.agents.codex import CodexAgent
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
        env = self.agent.build_env({"PARENT_SLUG": "foo", "CODEX_GATEWAY_API_KEY": "OVERRIDE"})
        self.assertEqual(env["PARENT_SLUG"], "foo")
        # caller override wins over the resolved gateway key
        self.assertEqual(env["CODEX_GATEWAY_API_KEY"], "OVERRIDE")

    def test_no_gateway_key_when_unconfigured(self):
        agent = CodexAgent(
            descriptor=HarnessDescriptor(name="c", runner="codex")
        )
        env = agent.build_env()
        self.assertNotIn("CODEX_GATEWAY_API_KEY", env)


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


if __name__ == "__main__":
    unittest.main()

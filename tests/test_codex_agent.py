"""Tests for the Codex engine runner (``archon.agents.codex``).

Covers the pure command/env builders (no codex subprocess spawned), the
fail-loud gateway / MCP guards, the prompt-variant tail, and — critically
for first-class parity — the ``codex --json`` → archon-JSONL parser, run
against synthetic streams that mirror the real codex 0.136 event schema.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archon.agents.codex import (
    CodexAgent,
    LeanLspMcpUnavailableError,
    PartialGatewayConfigError,
    UnknownMcpBundleError,
    _CODEX_STREAM_PARSER,
    _ensure_archon_on_path,
    _resolve_codex_bin,
    _stamp_archon_cli,
    resolve_prompt_variant,
)
from archon.commands.tooling.project_config import HarnessDescriptor


def _agent(**kw) -> CodexAgent:
    d = HarnessDescriptor(name=kw.pop("name", "codex"), runner="codex", **kw)
    return CodexAgent(descriptor=d, role="prover")


# ── build_argv ────────────────────────────────────────────────────────


class BuildArgvTest(unittest.TestCase):
    def test_base_argv_shape(self):
        argv = _agent(model="gpt-5.1-codex").build_argv(
            "PROMPT", env_source={}, lake_root="/proj"
        )
        # argv[0] resolves to the codex executable (absolute path when codex
        # is on PATH, else the bare name) — see _resolve_codex_bin.
        self.assertEqual(os.path.basename(argv[0]), "codex")
        self.assertEqual(argv[1:7], [
            "exec", "--json", "--skip-git-repo-check",
            "--ignore-user-config", "-m", "gpt-5.1-codex",
        ])
        self.assertIn("--ephemeral", argv)
        self.assertEqual(argv.index("--sandbox") + 1, argv.index("danger-full-access"))
        self.assertEqual(argv[-1], "PROMPT")

    def test_effort_rendered_when_set(self):
        argv = _agent(model="m", effort="xhigh").build_argv("P", env_source={})
        self.assertIn('model_reasoning_effort="xhigh"', argv)

    def test_effort_omitted_when_absent(self):
        argv = _agent(model="m").build_argv("P", env_source={})
        self.assertFalse(any("model_reasoning_effort" in a for a in argv))

    def test_model_omitted_when_descriptor_has_no_model(self):
        argv = _agent().build_argv("P", env_source={})
        self.assertIsNone(_agent().model)
        self.assertNotIn("-m", argv)

    def test_interactive_model_omitted_when_descriptor_has_no_model(self):
        argv = _agent().build_interactive_argv("P", env_source={})
        self.assertNotIn("-m", argv)

    def test_extra_args_appended_before_prompt(self):
        a = _agent(model="m", raw={"runner": "codex", "extra_args": ["--foo", "bar"]})
        argv = a.build_argv("P", env_source={})
        self.assertEqual(argv[-3:], ["--foo", "bar", "P"])


    def test_interactive_argv_uses_tui_entrypoint(self):
        argv = _agent(model="m", effort="xhigh").build_interactive_argv(
            "PROMPT", env_source={}, lake_root="/proj",
        )
        self.assertEqual(os.path.basename(argv[0]), "codex")
        self.assertNotIn("exec", argv)
        self.assertNotIn("--json", argv)
        self.assertIn("-m", argv)
        self.assertIn("m", argv)
        self.assertIn('model_reasoning_effort="xhigh"', argv)
        self.assertEqual(argv[-1], "PROMPT")


# ── codex binary resolution ──────────────────────────────────────────


class ResolveCodexBinTest(unittest.TestCase):
    def _desc(self, **raw):
        return HarnessDescriptor(name="codex", runner="codex", raw=raw)

    def test_descriptor_bin_override_wins(self):
        got = _resolve_codex_bin(
            self._desc(bin="/opt/codex/bin/codex"),
            {"ARCHON_CODEX_BIN": "/env/codex"},
        )
        self.assertEqual(got, "/opt/codex/bin/codex")

    def test_env_var_used_when_no_descriptor_override(self):
        got = _resolve_codex_bin(self._desc(), {"ARCHON_CODEX_BIN": "/env/codex"})
        self.assertEqual(got, "/env/codex")

    def test_falls_back_to_which_then_bare_name(self):
        with patch("archon.agents.codex.shutil.which", return_value="/usr/bin/codex"):
            self.assertEqual(_resolve_codex_bin(self._desc(), {}), "/usr/bin/codex")
        with patch("archon.agents.codex.shutil.which", return_value=None):
            self.assertEqual(_resolve_codex_bin(self._desc(), {}), "codex")

    def test_build_env_stamps_resolved_path_for_nested_dispatch(self):
        # The resolved path must be propagated so a nested `archon subagent` →
        # `codex exec` can spawn codex despite a sanitized child PATH.
        with patch("archon.agents.codex.shutil.which", return_value="/usr/bin/codex"):
            env = _agent(model="m").build_env()
        self.assertEqual(env["ARCHON_CODEX_BIN"], "/usr/bin/codex")

    def test_build_env_omits_stamp_when_codex_unresolvable(self):
        with patch("archon.agents.codex.shutil.which", return_value=None):
            env = _agent(model="m").build_env()
        self.assertNotIn("ARCHON_CODEX_BIN", env)


# ── gateway credentials ──────────────────────────────────────────────


class GatewayTest(unittest.TestCase):
    def test_no_gateway_uses_native_login(self):
        argv = _agent(model="m").build_argv("P", env_source={})
        self.assertFalse(any("model_provider=" in a for a in argv))

    def test_full_gateway_injects_provider_and_keeps_key_out_of_argv(self):
        a = _agent(model="m", base_url_env="CODEX_BASE_URL", key_env="CZ_API_KEY")
        env = {"CODEX_BASE_URL": "https://gw.example/v1", "CZ_API_KEY": "secret-xyz"}
        argv = a.build_argv("P", env_source=env)
        self.assertIn('model_provider="harness-gateway"', argv)
        self.assertTrue(any("base_url=" in x and "gw.example" in x for x in argv))
        # The secret must never appear in argv.
        self.assertFalse(any("secret-xyz" in x for x in argv))
        # …but it is copied into the child env under the provider's env_key.
        built = a.build_env(env_overrides=env)
        self.assertEqual(built["CODEX_GATEWAY_API_KEY"], "secret-xyz")

    def test_partial_gateway_base_without_key_raises(self):
        a = _agent(model="m", base_url_env="CODEX_BASE_URL", key_env="CZ_API_KEY")
        with self.assertRaises(PartialGatewayConfigError):
            a.build_argv("P", env_source={"CODEX_BASE_URL": "https://gw/v1"})

    def test_partial_gateway_key_without_base_raises(self):
        a = _agent(model="m", base_url_env="CODEX_BASE_URL", key_env="CZ_API_KEY")
        with self.assertRaises(PartialGatewayConfigError):
            a.build_argv("P", env_source={"CZ_API_KEY": "k"})


# ── MCP wiring ────────────────────────────────────────────────────────


class McpTest(unittest.TestCase):
    def test_no_mcp_by_default(self):
        argv = _agent(model="m").build_argv("P", env_source={}, lake_root="/proj")
        self.assertFalse(any("mcp_servers" in a for a in argv))

    def test_lean_lsp_renders_five_overrides_with_lake_root(self):
        a = _agent(model="m", mcp=("lean-lsp",))
        argv = a.build_argv("P", env_source={}, lake_root="/my/lake")
        joined = " ".join(argv)
        self.assertIn("mcp_servers.archon-lean-lsp.command", joined)
        self.assertIn("mcp_servers.archon-lean-lsp.required", joined)
        self.assertTrue(any(
            "archon-lean-lsp.env.LEAN_PROJECT_PATH" in a and "/my/lake" in a
            for a in argv
        ))

    def test_unknown_bundle_raises(self):
        a = _agent(model="m", mcp=("not-a-bundle",))
        with self.assertRaises(UnknownMcpBundleError):
            a.build_argv("P", env_source={}, lake_root="/proj")

    def test_lean_lsp_command_resolves_uv_from_env(self):
        # The MCP launcher must be PATH-independent for nested subagents.
        a = _agent(model="m", mcp=("lean-lsp",))
        argv = a.build_argv(
            "P", env_source={"ARCHON_UV_BIN": "/opt/uv/bin/uv"}, lake_root="/proj",
        )
        self.assertTrue(any(
            "archon-lean-lsp.command" in a and "/opt/uv/bin/uv" in a for a in argv
        ))

    def test_lean_lsp_forwards_path_to_server_env(self):
        # The Lean toolchain (lake/lean) the server spawns must be findable.
        a = _agent(model="m", mcp=("lean-lsp",))
        argv = a.build_argv(
            "P", env_source={"PATH": "/x/bin:/y/bin"}, lake_root="/proj",
        )
        self.assertTrue(any(
            "archon-lean-lsp.env.PATH" in a and "/x/bin:/y/bin" in a for a in argv
        ))


# ── prompt variant ────────────────────────────────────────────────────


class PromptVariantTest(unittest.TestCase):
    def test_no_variant_returns_prompt_unchanged(self):
        a = _agent(model="m")
        self.assertEqual(a._apply_prompt_variant("BASE", project_path=Path("/x")), "BASE")

    def test_project_local_variant_appended(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            vdir = proj / ".archon" / "prompts" / "variants"
            vdir.mkdir(parents=True)
            (vdir / "codex.md").write_text("CLI-RULES", encoding="utf-8")
            a = _agent(model="m", prompt_variant="codex")
            out = a._apply_prompt_variant("BASE", project_path=proj)
            self.assertTrue(out.startswith("BASE"))
            self.assertIn("CLI-RULES", out)

    def test_missing_variant_is_non_fatal(self):
        a = _agent(model="m", prompt_variant="does-not-exist-anywhere")
        with tempfile.TemporaryDirectory() as d:
            out = a._apply_prompt_variant("BASE", project_path=Path(d))
            self.assertEqual(out, "BASE")

    def test_resolve_prompt_variant_returns_none_when_absent(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(
                resolve_prompt_variant("nope-xyz", project_path=Path(d))
            )


# ── interactive foreground Codex ──────────────────────────────────────


class RunInteractiveTest(unittest.TestCase):
    def test_runs_foreground_codex_cli(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = Path(d)
            with patch("subprocess.run") as run:
                run.return_value.returncode = 7
                code = _agent(model="m").run_interactive("P", cwd=cwd)
        self.assertEqual(code, 7)
        argv = run.call_args.args[0]
        self.assertEqual(os.path.basename(argv[0]), "codex")
        self.assertEqual(argv[-1], "P")
        self.assertEqual(run.call_args.kwargs["cwd"], cwd)


# ── codex --json → archon JSONL parser ────────────────────────────────


def _run_parser(events: list[dict]) -> list[dict]:
    """Run the embedded parser over a synthetic codex stream, return rows."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.jsonl"
        script = _CODEX_STREAM_PARSER.format(
            verbose="False",
            raw_log=str(Path(d) / "raw"),
            jsonl=str(out),
            model="gpt-5.5",
        )
        stream = "".join(json.dumps(e) + "\n" for e in events)
        subprocess.run(
            [sys.executable, "-c", script],
            input=stream, text=True, check=True,
        )
        return [json.loads(l) for l in out.read_text().splitlines() if l.strip()]


class CodexParserTest(unittest.TestCase):
    def test_full_turn_maps_to_archon_schema(self):
        rows = _run_parser([
            {"type": "thread.started", "thread_id": "tid-1"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "i0", "type": "reasoning", "text": "thinking hard"}},
            {"type": "item.started", "item": {"id": "i1", "type": "command_execution", "command": "lake build"}},
            {"type": "item.completed", "item": {"id": "i1", "type": "command_execution", "command": "lake build", "exit_code": 0, "aggregated_output": "Build completed"}},
            {"type": "item.completed", "item": {"id": "i2", "type": "agent_message", "text": "Done proving."}},
            {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20, "reasoning_output_tokens": 12}},
        ])
        by_event = {}
        for r in rows:
            by_event.setdefault(r["event"], []).append(r)

        self.assertEqual(by_event["session_meta"][0]["session_id"], "tid-1")
        self.assertEqual(by_event["thinking"][0]["content"], "thinking hard")
        self.assertEqual(by_event["tool_call"][0]["tool"], "Bash")
        self.assertIn("lake build", by_event["tool_call"][0]["input"]["command"])
        self.assertIn("Build completed", by_event["tool_result"][0]["content"])
        self.assertEqual(by_event["text"][0]["content"], "Done proving.")

        end = by_event["session_end"][0]
        self.assertEqual(end["input_tokens"], 60)
        self.assertEqual(end["input_tokens_total"], 100)
        self.assertEqual(end["output_tokens"], 20)
        self.assertEqual(end["cache_read_input_tokens"], 40)
        self.assertEqual(end["num_turns"], 3)
        self.assertEqual(end["num_items"], 3)
        self.assertNotIn("total_cost_usd", end)
        self.assertEqual(end["model_usage"]["gpt-5.5"]["inputTokens"], 60)
        self.assertEqual(end["summary"], "Done proving.")
        self.assertEqual(end["runner"], "codex")

    def test_file_change_maps_to_apply_patch_tool(self):
        rows = _run_parser([
            {"type": "thread.started", "thread_id": "t"},
            {"type": "turn.started"},
            {"type": "item.started", "item": {"id": "i0", "type": "file_change", "changes": [{"path": "A.lean", "kind": "add"}], "status": "in_progress"}},
            {"type": "item.completed", "item": {"id": "i0", "type": "file_change", "changes": [{"path": "A.lean", "kind": "add"}], "status": "completed"}},
            {"type": "turn.completed", "usage": {}},
        ])
        tool_calls = [r for r in rows if r["event"] == "tool_call"]
        self.assertEqual(tool_calls[0]["tool"], "Edit")
        self.assertEqual(tool_calls[0]["input"]["changes"][0]["path"], "A.lean")

    def test_turn_failed_surfaces_as_summary(self):
        rows = _run_parser([
            {"type": "thread.started", "thread_id": "t"},
            {"type": "turn.started"},
            {"type": "error", "message": "model not allowed"},
            {"type": "turn.failed", "error": {"message": "model not allowed"}},
        ])
        end = [r for r in rows if r["event"] == "session_end"][0]
        self.assertIn("model not allowed", end["summary"])

    def test_unknown_item_types_do_not_crash(self):
        rows = _run_parser([
            {"type": "thread.started", "thread_id": "t"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "i0", "type": "todo_list", "items": []}},
            {"type": "item.completed", "item": {"id": "i1", "type": "brand_new_thing", "blah": 1}},
            {"type": "turn.completed", "usage": {}},
        ])
        events = {r["event"] for r in rows}
        self.assertIn("session_meta", events)
        self.assertIn("session_end", events)
        self.assertEqual([r for r in rows if r["event"] == "session_end"][0]["num_items"], 2)

    def test_completed_tool_without_started_still_emits_call(self):
        rows = _run_parser([
            {"type": "thread.started", "thread_id": "t"},
            {"type": "item.completed", "item": {
                "id": "c1", "type": "command_execution",
                "command": "echo hi", "aggregated_output": "hi",
                "exit_code": 0, "status": "completed"}},
            {"type": "turn.completed", "usage": {
                "input_tokens": 1, "cached_input_tokens": 0,
                "output_tokens": 1, "reasoning_output_tokens": 0}},
        ])
        events = [r["event"] for r in rows]
        self.assertEqual(events.count("tool_call"), 1)
        self.assertEqual(events.count("tool_result"), 1)
        self.assertLess(events.index("tool_call"), events.index("tool_result"))
        self.assertEqual([r for r in rows if r["event"] == "session_end"][0]["num_turns"], 1)


class StampArchonCliTest(unittest.TestCase):
    """The codex login shell (`bash -lc`) resets PATH, so the subagent
    wrapper cannot rely on it to find archon. build_env must stamp
    PATH-independent handles the wrapper can read instead.
    """

    def test_stamps_python_and_cli_bin(self):
        with patch("archon.agents.codex.shutil.which",
                   return_value="/venv/bin/archon"), \
             patch("archon.agents.codex.sys.executable", "/venv/bin/python"):
            env: dict[str, str] = {}
            _stamp_archon_cli(env)
        self.assertEqual(env["ARCHON_PYTHON"], "/venv/bin/python")
        self.assertEqual(env["ARCHON_CLI_BIN"], "/venv/bin/archon")

    def test_omits_cli_bin_when_console_script_unresolvable(self):
        with patch("archon.agents.codex.shutil.which", return_value=None), \
             patch("archon.agents.codex.sys.executable", "/venv/bin/python"):
            env: dict[str, str] = {}
            _stamp_archon_cli(env)
        self.assertEqual(env["ARCHON_PYTHON"], "/venv/bin/python")
        self.assertNotIn("ARCHON_CLI_BIN", env)

    def test_does_not_clobber_inherited_stamps(self):
        # A nested dispatch must keep the grandparent's stamp.
        with patch("archon.agents.codex.shutil.which",
                   return_value="/venv/bin/archon"), \
             patch("archon.agents.codex.sys.executable", "/venv/bin/python"):
            env = {"ARCHON_PYTHON": "/orig/python", "ARCHON_CLI_BIN": "/orig/archon"}
            _stamp_archon_cli(env)
        self.assertEqual(env["ARCHON_PYTHON"], "/orig/python")
        self.assertEqual(env["ARCHON_CLI_BIN"], "/orig/archon")

    def test_build_env_stamps_cli_handles(self):
        with patch("archon.agents.codex.shutil.which",
                   return_value="/venv/bin/archon"), \
             patch("archon.agents.codex.sys.executable", "/venv/bin/python"):
            env = _agent(model="m").build_env()
        self.assertEqual(env["ARCHON_CLI_BIN"], "/venv/bin/archon")
        self.assertEqual(env["ARCHON_PYTHON"], "/venv/bin/python")


class EnsureArchonOnPathTest(unittest.TestCase):
    """The codex child env must expose `archon` so sandboxed subagent
    dispatch (`python3 .claude/tools/archon-subagent.py`) can find it.
    """

    def test_prepends_archon_bin_dir(self):
        with patch("archon.agents.codex.shutil.which",
                   return_value="/opt/venv/bin/archon"):
            env = {"PATH": "/usr/bin"}
            _ensure_archon_on_path(env)
        self.assertEqual(env["PATH"].split(":")[0], "/opt/venv/bin")
        self.assertIn("/usr/bin", env["PATH"].split(":"))

    def test_idempotent_when_already_present(self):
        with patch("archon.agents.codex.shutil.which",
                   return_value="/opt/venv/bin/archon"):
            env = {"PATH": "/opt/venv/bin:/usr/bin"}
            _ensure_archon_on_path(env)
        self.assertEqual(env["PATH"], "/opt/venv/bin:/usr/bin")

    def test_noop_when_archon_not_found(self):
        with patch("archon.agents.codex.shutil.which", return_value=None):
            env = {"PATH": "/usr/bin"}
            _ensure_archon_on_path(env)
        self.assertEqual(env["PATH"], "/usr/bin")


if __name__ == "__main__":
    unittest.main()

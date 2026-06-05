"""Tests for the Codex engine runner (``archon.agents.codex``).

Covers the pure command/env builders (no codex subprocess spawned), the
fail-loud gateway / MCP guards, the prompt-variant tail, and — critically
for first-class parity — the ``codex --json`` → archon-JSONL parser, run
against synthetic streams that mirror the real codex 0.136 event schema.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from archon.agents.codex import (
    CodexAgent,
    LeanLspMcpUnavailableError,
    PartialGatewayConfigError,
    UnknownMcpBundleError,
    _CODEX_STREAM_PARSER,
    resolve_prompt_variant,
)
from archon.commands.tooling.project_config import HarnessDescriptor


def _agent(**kw) -> CodexAgent:
    d = HarnessDescriptor(name=kw.pop("name", "codex"), runner="codex", **kw)
    return CodexAgent(descriptor=d, role="prover")


# ── build_argv ────────────────────────────────────────────────────────


class BuildArgvTest(unittest.TestCase):
    def test_base_argv_shape(self):
        argv = _agent(model="gpt-5.1-codex-max").build_argv(
            "PROMPT", env_source={}, lake_root="/proj"
        )
        self.assertEqual(argv[:7], [
            "codex", "exec", "--json", "--skip-git-repo-check",
            "--ignore-user-config", "-m", "gpt-5.1-codex-max",
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

    def test_model_falls_back_to_default(self):
        # No model on the descriptor → the codex default id.
        self.assertEqual(_agent().model, "gpt-5.1-codex-max")

    def test_extra_args_appended_before_prompt(self):
        a = _agent(model="m", raw={"runner": "codex", "extra_args": ["--foo", "bar"]})
        argv = a.build_argv("P", env_source={})
        self.assertEqual(argv[-3:], ["--foo", "bar", "P"])


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


# ── run_interactive unsupported ───────────────────────────────────────


class RunInteractiveTest(unittest.TestCase):
    def test_raises_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            _agent(model="m").run_interactive("P", cwd=Path("/x"))


# ── codex --json → archon JSONL parser ────────────────────────────────


def _run_parser(events: list[dict]) -> list[dict]:
    """Run the embedded parser over a synthetic codex stream, return rows."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.jsonl"
        script = _CODEX_STREAM_PARSER.format(
            verbose="False", raw_log=str(Path(d) / "raw"), jsonl=str(out),
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
        self.assertEqual(by_event["tool_call"][0]["tool"], "shell")
        self.assertIn("lake build", by_event["tool_call"][0]["input"]["command"])
        self.assertIn("Build completed", by_event["tool_result"][0]["content"])
        self.assertEqual(by_event["text"][0]["content"], "Done proving.")

        end = by_event["session_end"][0]
        self.assertEqual(end["session_id"], "tid-1")
        self.assertEqual(end["input_tokens"], 100)
        self.assertEqual(end["output_tokens"], 20)
        self.assertEqual(end["cache_read_input_tokens"], 40)
        self.assertEqual(end["num_turns"], 1)
        self.assertEqual(end["total_cost_usd"], 0)
        self.assertEqual(end["summary"], "Done proving.")
        self.assertFalse(end["ended_early"])
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
        self.assertEqual(tool_calls[0]["tool"], "apply_patch")
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

    def test_unknown_item_types_ignored(self):
        # Additive: a todo_list / never-seen item must not crash the parser
        # and must not produce stray transcript rows.
        rows = _run_parser([
            {"type": "thread.started", "thread_id": "t"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "i0", "type": "todo_list", "items": []}},
            {"type": "item.completed", "item": {"id": "i1", "type": "brand_new_thing", "blah": 1}},
            {"type": "turn.completed", "usage": {}},
        ])
        events = {r["event"] for r in rows}
        self.assertEqual(events, {"session_meta", "session_end"})


if __name__ == "__main__":
    unittest.main()

"""Golden lock on ClaudeAgent's constructed ``claude`` argv.

Guards the invariant that the default (Anthropic) path is unchanged by the
extraction of ``supervise_streamed_run`` (the shared subprocess supervisor)
out of ``ClaudeAgent``. If a future change alters how the ``claude`` process
is launched — flag order, permission flags, the ``stream-json`` tail — these
tests fail loudly, so "ClaudeAgent is byte-identical after the refactor" is a
checked claim rather than a hand-review.
"""
from __future__ import annotations

import archon.agent as agent_mod
from archon.agent import ClaudeAgent, SupervisionResult


def test_build_flags_golden():
    flags = ClaudeAgent(model="opus", role="plan")._build_flags("opus")
    assert flags == [
        "--dangerously-skip-permissions",
        "--permission-mode", "bypassPermissions",
        "--model", "opus",
    ]


def test_build_flags_without_skip_permissions():
    flags = ClaudeAgent(
        model="sonnet", role="prover", skip_permissions=False,
    )._build_flags("sonnet")
    assert flags == [
        "--permission-mode", "bypassPermissions",
        "--model", "sonnet",
    ]


def test_headless_run_argv_golden(tmp_path, monkeypatch):
    """The full argv ClaudeAgent.run hands to the shared supervisor is stable."""
    captured: dict[str, list[str]] = {}

    def fake_supervise(agent_cmd, **kwargs):
        captured["argv"] = list(agent_cmd)
        return SupervisionResult(returncode=0, cancelled=False, idle_timeout_hit=False)

    monkeypatch.setattr(agent_mod, "supervise_streamed_run", fake_supervise)

    ok = ClaudeAgent(model="opus", role="plan").run(
        "PROMPT", cwd=tmp_path, log_base=tmp_path / "plan", max_attempts=1,
    )

    assert ok is True
    assert captured["argv"] == [
        "claude", "-p", "PROMPT",
        "--dangerously-skip-permissions",
        "--permission-mode", "bypassPermissions",
        "--model", "opus",
        "--verbose", "--output-format", "stream-json",
    ]

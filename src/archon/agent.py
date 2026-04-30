"""Wrapped Claude Code CLI invocation.

Centralizes how archon launches `claude` so model selection, permission
flags, and structured JSONL logging are consistent across every phase
agent (plan, refactor, prover, review, discuss). All sites that talk to
the `claude` binary should go through ``ClaudeAgent`` — when we later
swap engines (e.g., OpenClaw or another orchestrator), only this class
changes.

Quick reference:
    agent = ClaudeAgent(model="opus")
    agent.run(prompt, cwd=project, log_base=phase_log)   # headless `-p`
    agent.run_interactive(prompt, cwd=project)           # foreground TUI
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from archon import log


# Default model alias. ``opus`` resolves to the latest Opus build at the
# time the `claude` CLI is invoked, so we don't pin a specific revision —
# bumping the CLI auto-bumps the model. Override per-call via
# ``ClaudeAgent(model=…)`` or via the CLI ``--model`` flag.
DEFAULT_MODEL = "opus"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _emit_session_start(jsonl_path: str, *, model: str, role: str | None) -> None:
    """Stamp the model used into the very first line of a phase JSONL.

    Lets the dashboard and any downstream tooling read the model directly
    from the log instead of re-deriving it from CLI flags. ``role`` is the
    phase name (plan/refactor/prover/review) when known.
    """
    Path(jsonl_path).parent.mkdir(parents=True, exist_ok=True)
    row: dict[str, object] = {"ts": _now_iso(), "event": "session_start", "model": model}
    if role:
        row["role"] = role
    with open(jsonl_path, "a") as f:
        f.write(json.dumps(row) + "\n")


# ── stream parser ─────────────────────────────────────────────────────
#
# Embedded Python script that consumes claude's `--output-format
# stream-json` lines on stdin and writes a normalized JSONL (and an
# optional raw log) to disk. Kept here — instead of as a sibling .py
# file — so the agent module is self-contained: spawning the parser is
# `python -c <script>`.

_STREAM_PARSER = r'''
import sys, json, datetime

VERBOSE = '{verbose}' == 'True'
RAW = open('{raw_log}', 'a') if VERBOSE else None
JSONL = open('{jsonl}', 'a')

def emit(event_type, **fields):
    row = {{'ts': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), 'event': event_type, **fields}}
    JSONL.write(json.dumps(row) + '\n')
    JSONL.flush()

def terminal(s):
    print(s, flush=True)

last_result = ''

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    if RAW:
        RAW.write(line + '\n')
        RAW.flush()

    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        continue

    t = obj.get('type', '')

    if t == 'assistant' and 'message' in obj:
        msg = obj['message']
        if not isinstance(msg, dict):
            continue
        for block in msg.get('content', []):
            bt = block.get('type', '')
            if bt == 'thinking':
                thinking = block.get('thinking', '').strip()
                if thinking:
                    emit('thinking', content=thinking)
            elif bt == 'text':
                text = block.get('text', '').strip()
                if text:
                    emit('text', content=text)
                    last_result = text
            elif bt == 'tool_use':
                name = block.get('name', '?')
                inp = block.get('input', {{}})
                emit('tool_call', tool=name, input=inp)

    elif t == 'user' and 'message' in obj:
        msg = obj['message']
        if not isinstance(msg, dict):
            continue
        for block in msg.get('content', []):
            if block.get('type') == 'tool_result':
                content = block.get('content', '')
                if isinstance(content, str):
                    emit('tool_result', content=content)
                elif isinstance(content, list):
                    texts = [p.get('text','') for p in content if isinstance(p,dict) and p.get('type')=='text']
                    emit('tool_result', content='\n'.join(texts))

    elif t == 'result':
        cost = obj.get('total_cost_usd', 0) or obj.get('cost_usd', 0) or 0
        duration = obj.get('duration_ms', 0) or 0
        turns = obj.get('num_turns', 0) or 0
        session_id = obj.get('session_id', '') or ''
        result = obj.get('result', '')
        usage = obj.get('usage', {{}}) or {{}}
        model_usage = obj.get('modelUsage', {{}}) or {{}}
        summary = result if isinstance(result, str) and result else last_result

        emit('session_end',
            session_id=session_id,
            total_cost_usd=cost,
            duration_ms=duration,
            duration_api_ms=usage.get('duration_api_ms', 0) or 0,
            num_turns=turns,
            input_tokens=usage.get('input_tokens', 0) or 0,
            output_tokens=usage.get('output_tokens', 0) or 0,
            cache_read_input_tokens=usage.get('cache_read_input_tokens', 0) or 0,
            cache_creation_input_tokens=usage.get('cache_creation_input_tokens', 0) or 0,
            model_usage=model_usage,
            summary=summary,
        )

        if summary:
            terminal(summary)
        parts = []
        if duration:  parts.append(f'{{duration/60000:.1f}}min')
        if cost:      parts.append(f'${{cost:.4f}}')
        if usage.get('input_tokens') or usage.get('output_tokens'):
            parts.append(f'in={{usage.get("input_tokens",0)}} out={{usage.get("output_tokens",0)}}')
        if turns:     parts.append(f'turns={{turns}}')
        if parts:
            terminal(f'[COST] {{" | ".join(parts)}}')

JSONL.close()
if RAW: RAW.close()
'''


# ── ClaudeAgent ───────────────────────────────────────────────────────


@dataclass
class ClaudeAgent:
    """One ``claude`` CLI invocation.

    Designed so a future ``OpenClawAgent`` (or any other engine wrapper)
    can implement the same ``run`` / ``run_interactive`` shape. Keeping
    the wrapper data-class-shaped — model + permission flags + role —
    means swapping engines is one substitution at every call site, not a
    deep rewrite.

    Attributes:
        model: alias (`opus`, `sonnet`) or full model id passed verbatim
            to ``claude --model``.
        role: optional phase tag stamped into the JSONL (`plan`, `prover`,
            etc.). Surfaces in the dashboard alongside the model.
        permission_mode: forwarded to ``--permission-mode``. Defaults to
            ``bypassPermissions`` because every archon agent runs
            unattended in CI-style loops.
        skip_permissions: if True, also passes
            ``--dangerously-skip-permissions``.
    """

    model: str = DEFAULT_MODEL
    role: str | None = None
    permission_mode: str = "bypassPermissions"
    skip_permissions: bool = True

    # ── command assembly ─────────────────────────────────────────────

    def _common_flags(self) -> list[str]:
        flags: list[str] = []
        if self.skip_permissions:
            flags.append("--dangerously-skip-permissions")
        flags.extend(["--permission-mode", self.permission_mode])
        flags.extend(["--model", self.model])
        return flags

    # ── invocation modes ─────────────────────────────────────────────

    def run(
        self,
        prompt: str,
        *,
        cwd: Path,
        log_base: Path | None = None,
        verbose_logs: bool = False,
        extra_args: list[str] | None = None,
        env_overrides: dict[str, str] | None = None,
        cancel_event: 'threading.Event | None' = None,
    ) -> bool:
        """Headless ``claude -p`` run.

        Returns True iff claude exited zero. When ``log_base`` is given,
        writes ``{log_base}.jsonl`` (parsed events) and optionally
        ``{log_base}.raw.jsonl`` (verbose stream).

        ``env_overrides`` is forwarded to the spawned subprocess so lane
        runs can set provider-specific variables (e.g. an alternate
        ``ANTHROPIC_BASE_URL``) without leaking them into the parent.

        ``cancel_event`` is checked while waiting for claude to finish.
        Setting it from another thread sends SIGTERM to the spawned
        process, lets it tear down for up to 5 seconds, then SIGKILLs.
        Used by multilane to stop slow lanes once another lane has won
        the same file. Returns False on cancellation.
        """
        cmd = ["claude", "-p", prompt, *self._common_flags()]
        if extra_args:
            cmd.extend(extra_args)

        env = self._build_env(env_overrides)

        self._announce_model()

        if log_base is None:
            return subprocess.run(cmd, cwd=cwd, env=env).returncode == 0
        return self._run_with_logging(
            cmd, cwd=cwd, log_base=log_base, verbose_logs=verbose_logs,
            env=env, cancel_event=cancel_event,
        )

    def run_interactive(
        self,
        prompt: str,
        *,
        cwd: Path,
        extra_args: list[str] | None = None,
    ) -> int:
        """Foreground interactive launch (no ``-p``).

        Used by ``archon discuss`` / ``archon refactor draft`` / the
        bootstrap flow in ``archon init`` — anywhere claude takes over
        the terminal and expects user input. Returns the subprocess exit
        code so the caller can decide how to react to non-zero.
        """
        cmd = ["claude", *self._common_flags()]
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(prompt)

        self._announce_model()
        return subprocess.run(cmd, cwd=cwd).returncode

    # ── internals ────────────────────────────────────────────────────

    def _announce_model(self) -> None:
        suffix = f" ({self.role})" if self.role else ""
        log.info(f"Agent model: {self.model}{suffix}")

    def _build_env(self, overrides: dict[str, str] | None) -> dict[str, str]:
        env = os.environ.copy()
        if overrides:
            env.update(overrides)
        # Running as root in CI / containers needs IS_SANDBOX=1 so the
        # claude CLI doesn't refuse to start. Lanes set this from the
        # outside too (per-provider settings), which always takes
        # priority over the default below.
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            env.setdefault("IS_SANDBOX", "1")
        return env

    def _run_with_logging(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        log_base: Path,
        verbose_logs: bool,
        env: dict[str, str] | None = None,
        cancel_event: 'threading.Event | None' = None,
    ) -> bool:
        log_base.parent.mkdir(parents=True, exist_ok=True)
        jsonl = f"{log_base}.jsonl"
        raw_log = f"{log_base}.raw.jsonl"

        # Stamp the model BEFORE claude starts streaming, so the
        # dashboard/postprocessors can read which model produced every
        # subsequent event.
        _emit_session_start(jsonl, model=self.model, role=self.role)

        cmd = cmd + ["--verbose", "--output-format", "stream-json"]
        parser_script = _STREAM_PARSER.format(
            verbose=str(verbose_logs),
            raw_log=raw_log,
            jsonl=jsonl,
        )

        cancelled = False
        stderr_dest = raw_log if verbose_logs else os.devnull
        with open(stderr_dest, "a") as stderr_file:
            claude_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                cwd=cwd,
                env=env,
            )
            parser_proc = subprocess.Popen(
                [sys.executable, "-u", "-c", parser_script],
                stdin=claude_proc.stdout,
                cwd=cwd,
            )
            assert claude_proc.stdout is not None
            claude_proc.stdout.close()

            # Poll the parser instead of blocking on .wait() so we can
            # honour cancel_event from another thread (multilane uses
            # this to kill slow lanes after another lane wins). Tick
            # every 200 ms — fine-grained enough to feel responsive,
            # coarse enough to be cheap.
            while parser_proc.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    _terminate_process(claude_proc, sig=signal.SIGTERM)
                    break
                time.sleep(0.2)

            if cancelled:
                # Give claude a moment to tear down on its own, then
                # escalate to SIGKILL. The parser will exit on its own
                # once claude's stdout closes.
                try:
                    claude_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_process(claude_proc, sig=signal.SIGKILL)
                    claude_proc.wait()
                try:
                    parser_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_process(parser_proc, sig=signal.SIGKILL)
                    parser_proc.wait()
            else:
                # Normal teardown: parser already exited (saw
                # session_end). If claude lingers (slow MCP teardown,
                # for example), signal it.
                try:
                    claude_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_process(claude_proc, sig=signal.SIGTERM)
                    try:
                        claude_proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        _terminate_process(claude_proc, sig=signal.SIGKILL)
                        claude_proc.wait()

        if cancelled:
            return False

        # Some lanes legitimately end with a non-zero return even though
        # the assistant produced a valid session_end — check the JSONL
        # before flagging the run as failed.
        from archon.runner import _read_last_session_end, _session_end_indicates_success

        if claude_proc.returncode == 0:
            return True
        session_end = _read_last_session_end(jsonl)
        return _session_end_indicates_success(session_end)


def _terminate_process(proc: subprocess.Popen, *, sig: int = signal.SIGTERM) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(sig)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


__all__ = ["ClaudeAgent", "DEFAULT_MODEL"]

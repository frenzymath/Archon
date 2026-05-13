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

    # Auto-restart on a hung provider (e.g. overnight Kimi run):
    agent.run(
        prompt, cwd=project, log_base=phase_log,
        idle_timeout_s=900,   # 15 min of zero JSONL activity
        max_attempts=3,       # then re-run the same prompt up to 3x
    )
"""

from __future__ import annotations

import enum
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from archon import log


# Default model alias. ``opus`` resolves to the latest Opus build at the
# time the `claude` CLI is invoked, so we don't pin a specific revision —
# bumping the CLI auto-bumps the model. Override per-call via
# ``ClaudeAgent(model=…)`` or via the CLI ``--model`` flag.
DEFAULT_MODEL = "opus"


# Model aliases that select a non-Anthropic provider. When the agent's
# ``model`` matches one of these, ``_resolve_provider`` swaps in the
# corresponding ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` env
# overrides at run() time — no settings file is ever written to disk,
# so the API key can't leak into commits, logs, or shared snapshots.
# The keys are read from ``.archon/.env`` (or the shell) via
# ``provider_env`` in ``env_loader``.
PROVIDER_ALIASES: dict[str, str] = {
    "kimi": "moonshot",
    "moonshot": "moonshot",
    "deepseek": "deepseek",
}


class RunOutcome(enum.Enum):
    """Result of a single ``_run_with_logging`` attempt.

    Distinguishing IDLE_TIMEOUT from FAILED matters: the retry loop in
    :meth:`ClaudeAgent.run` only restarts on idle timeouts. Restarting
    on real failures (bad prompt, auth error, etc.) would double the
    token bill without fixing anything.
    """

    SUCCESS = "success"
    FAILED = "failed"
    IDLE_TIMEOUT = "idle_timeout"
    CANCELLED = "cancelled"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _emit_session_start(
    jsonl_path: str,
    *,
    model: str,
    role: str | None,
    attempt: int | None = None,
) -> None:
    """Stamp the model used into the very first line of a phase JSONL.

    Lets the dashboard and any downstream tooling read the model directly
    from the log instead of re-deriving it from CLI flags. ``role`` is the
    phase name (plan/refactor/prover/review) when known. ``attempt`` is
    stamped on retries so a single phase log can contain multiple
    session_start lines and the dashboard can tell them apart.
    """
    Path(jsonl_path).parent.mkdir(parents=True, exist_ok=True)
    row: dict[str, object] = {"ts": _now_iso(), "event": "session_start", "model": model}
    if role:
        row["role"] = role
    if attempt is not None:
        row["attempt"] = attempt
    with open(jsonl_path, "a") as f:
        f.write(json.dumps(row) + "\n")


def _emit_idle_timeout(jsonl_path: str, idle_s: float, attempt: int) -> None:
    """Record an idle-timeout kill in the JSONL.

    Surfaces in the dashboard as a distinct event so a hung-provider
    restart is visible instead of looking like a silent failure.
    """
    row = {
        "ts": _now_iso(),
        "event": "idle_timeout",
        "idle_threshold_s": idle_s,
        "attempt": attempt,
    }
    try:
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        # Best-effort; never let a logging failure mask the real
        # control-flow signal (the IDLE_TIMEOUT return).
        pass


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

    def _build_flags(self, model: str) -> list[str]:
        flags: list[str] = []
        if self.skip_permissions:
            flags.append("--dangerously-skip-permissions")
        flags.extend(["--permission-mode", self.permission_mode])
        flags.extend(["--model", model])
        return flags

    def _resolve_provider(self) -> tuple[str, dict[str, str], str | None]:
        """Resolve the agent's ``model`` to (real_model, env, provider).

        For standard Anthropic aliases (``opus``, ``sonnet``, …) this is
        a no-op: returns ``(self.model, {}, None)``.

        For non-Anthropic aliases declared in :data:`PROVIDER_ALIASES`
        (``kimi``, ``deepseek``, …), looks up
        :func:`env_loader.provider_env` and returns:

        - ``real_model``: the concrete provider model (e.g. ``kimi-k2.6``)
          to pass via ``claude --model``;
        - ``env``: the ``{ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, …}``
          dict that the spawned ``claude`` will read to redirect itself
          to the provider's Anthropic-compatible endpoint;
        - ``provider``: the provider name, used only to label log lines.

        Raises ``RuntimeError`` when the provider's API key isn't set —
        single-agent flows (plan / review / discuss / subagents) have no
        graceful fallback the way a multilane round does, so failing
        loud is the right call.
        """
        if self.model not in PROVIDER_ALIASES:
            return self.model, {}, None
        from archon.commands.tooling.env_loader import PROVIDERS, provider_env

        provider = PROVIDER_ALIASES[self.model]
        env = provider_env(provider)
        if not env:
            key_name = PROVIDERS.get(provider, [provider.upper() + "_API_KEY"])[0]
            raise RuntimeError(
                f"Model '{self.model}' resolves to provider '{provider}', "
                f"but {key_name} is not set. Add it to .archon/.env or "
                f"export it in your shell, then re-run."
            )
        real_model = env.get("ANTHROPIC_MODEL", self.model)
        return real_model, env, provider

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
        idle_timeout_s: float | None = 900,
        max_attempts: int = 3,
    ) -> bool:
        """Headless ``claude -p`` run.

        Returns True iff claude exited zero (or zero-with-valid-
        session_end). When ``log_base`` is given, writes
        ``{log_base}.jsonl`` (parsed events) and optionally
        ``{log_base}.raw.jsonl`` (verbose stream).

        ``env_overrides`` is forwarded to the spawned subprocess so lane
        runs can set provider-specific variables (e.g. an alternate
        ``ANTHROPIC_BASE_URL``) without leaking them into the parent.
        When the agent's own ``model`` is a non-Anthropic provider alias,
        the matching provider env vars are merged in here too —
        caller-supplied overrides win on conflict.

        ``cancel_event`` is checked while waiting for claude to finish.
        Setting it from another thread sends SIGTERM to the spawned
        process, lets it tear down for up to 5 seconds, then SIGKILLs.
        Used by multilane to stop slow lanes once another lane has won
        the same file. Returns False on cancellation.

        ``idle_timeout_s`` enables a watchdog: if no JSONL event is
        written for this many seconds, claude is killed and (when
        ``max_attempts`` > 1) the same prompt is re-run. This catches
        third-party providers (Kimi, DeepSeek) whose connections
        sometimes hang silently — a 15-minute idle threshold turns an
        overnight stall into a 15-minute hiccup. Only the watchdog path
        retries; real failures (auth errors, bad prompts) return False
        immediately so we don't double-bill on something a retry can't
        fix. Requires ``log_base`` (the watchdog reads the JSONL's
        mtime); ignored without it.

        ``max_attempts`` caps the retry count. Default 1 preserves
        legacy behaviour — no auto-restart unless the caller opts in.
        """
        real_model, provider_env_vars, provider = self._resolve_provider()
        cmd = ["claude", "-p", prompt, *self._build_flags(real_model)]
        if extra_args:
            cmd.extend(extra_args)

        merged = dict(provider_env_vars)
        if env_overrides:
            merged.update(env_overrides)
        env = self._build_env(merged)

        self._announce_model(real_model=real_model, provider=provider)

        if log_base is None:
            # Without a log file the watchdog has nothing to read; fall
            # back to the simple blocking invocation.
            return subprocess.run(cmd, cwd=cwd, env=env).returncode == 0

        if max_attempts < 1:
            max_attempts = 1

        last_outcome: RunOutcome = RunOutcome.FAILED
        for attempt in range(1, max_attempts + 1):
            outcome = self._run_with_logging(
                cmd,
                cwd=cwd,
                log_base=log_base,
                verbose_logs=verbose_logs,
                env=env,
                cancel_event=cancel_event,
                jsonl_model=real_model,
                idle_timeout_s=idle_timeout_s,
                attempt=attempt,
            )
            last_outcome = outcome

            if outcome is RunOutcome.SUCCESS:
                return True
            if outcome is RunOutcome.CANCELLED:
                return False
            if outcome is RunOutcome.FAILED:
                # A real failure — retrying won't fix it and would just
                # burn tokens. Stop here.
                return False

            # outcome is IDLE_TIMEOUT: provider went silent. Retry the
            # same prompt unless the caller asked us to stop or we've
            # hit the attempt cap.
            if cancel_event is not None and cancel_event.is_set():
                return False
            if attempt < max_attempts:
                log.warning(
                    f"Run idle for {idle_timeout_s}s on attempt "
                    f"{attempt}/{max_attempts}; restarting same prompt."
                )
            else:
                log.error(
                    f"Run still idle after {max_attempts} attempts; giving up."
                )

        return last_outcome is RunOutcome.SUCCESS

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

        No idle watchdog here: a human is sitting at the terminal and
        long pauses are normal (they're reading, thinking, typing).
        """
        real_model, provider_env_vars, provider = self._resolve_provider()
        cmd = ["claude", *self._build_flags(real_model)]
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(prompt)

        env = self._build_env(provider_env_vars) if provider_env_vars else None
        self._announce_model(real_model=real_model, provider=provider)
        return subprocess.run(cmd, cwd=cwd, env=env).returncode

    # ── internals ────────────────────────────────────────────────────

    def _announce_model(
        self, *, real_model: str | None = None, provider: str | None = None,
    ) -> None:
        role_suffix = f" ({self.role})" if self.role else ""
        shown = real_model or self.model
        if provider:
            log.info(f"Agent model: {shown} via {provider}{role_suffix}")
        else:
            log.info(f"Agent model: {shown}{role_suffix}")

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
        jsonl_model: str | None = None,
        idle_timeout_s: float | None = 900,
        attempt: int = 3,
    ) -> RunOutcome:
        """Run claude once with logging + watchdog.

        Returns a :class:`RunOutcome` so the caller can decide whether
        to retry. SUCCESS/FAILED come from the exit code (with a
        session_end fallback); IDLE_TIMEOUT means the watchdog killed
        the process; CANCELLED means ``cancel_event`` fired.
        """
        log_base.parent.mkdir(parents=True, exist_ok=True)
        jsonl = f"{log_base}.jsonl"
        raw_log = f"{log_base}.raw.jsonl"
        jsonl_path = Path(jsonl)

        # Stamp the model BEFORE claude starts streaming, so the
        # dashboard/postprocessors can read which model produced every
        # subsequent event. On retries this fires again with an
        # ``attempt`` field, so a single phase log can contain multiple
        # session_starts and downstream tooling can tell them apart.
        _emit_session_start(
            jsonl,
            model=jsonl_model or self.model,
            role=self.role,
            attempt=attempt if attempt > 1 else None,
        )

        cmd = cmd + ["--verbose", "--output-format", "stream-json"]
        parser_script = _STREAM_PARSER.format(
            verbose=str(verbose_logs),
            raw_log=raw_log,
            jsonl=jsonl,
        )

        cancelled = False
        idle_timeout_hit = False
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

            # Idle-watchdog state. We use the JSONL file's mtime as the
            # liveness signal because the parser flushes on every event
            # — cheap, no extra IPC, and survives even if claude's
            # stdout is buffered upstream.
            last_activity = time.monotonic()
            try:
                last_mtime = jsonl_path.stat().st_mtime
            except OSError:
                last_mtime = 0.0

            # Poll the parser instead of blocking on .wait() so we can
            # honour cancel_event from another thread (multilane uses
            # this to kill slow lanes after another lane wins) and the
            # idle watchdog. Tick every 200 ms — fine-grained enough to
            # feel responsive, coarse enough to be cheap.
            while parser_proc.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    _terminate_process(claude_proc, sig=signal.SIGTERM)
                    break

                if idle_timeout_s is not None:
                    try:
                        mtime = jsonl_path.stat().st_mtime
                    except OSError:
                        mtime = last_mtime
                    if mtime > last_mtime:
                        last_mtime = mtime
                        last_activity = time.monotonic()
                    elif time.monotonic() - last_activity > idle_timeout_s:
                        idle_timeout_hit = True
                        log.warning(
                            f"No JSONL activity for {idle_timeout_s}s on "
                            f"attempt {attempt}; terminating claude."
                        )
                        _emit_idle_timeout(jsonl, idle_timeout_s, attempt)
                        _terminate_process(claude_proc, sig=signal.SIGTERM)
                        break

                time.sleep(0.2)

            if cancelled or idle_timeout_hit:
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
            return RunOutcome.CANCELLED
        if idle_timeout_hit:
            return RunOutcome.IDLE_TIMEOUT

        # Some lanes legitimately end with a non-zero return even though
        # the assistant produced a valid session_end — check the JSONL
        # before flagging the run as failed.
        from archon.session_log import (
            read_last_session_end,
            session_end_indicates_success,
        )

        if claude_proc.returncode == 0:
            return RunOutcome.SUCCESS
        session_end = read_last_session_end(jsonl)
        if session_end_indicates_success(session_end):
            return RunOutcome.SUCCESS
        return RunOutcome.FAILED


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


__all__ = ["ClaudeAgent", "DEFAULT_MODEL", "RunOutcome"]
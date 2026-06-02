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
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from archon import log

if TYPE_CHECKING:
    from archon.commands.tooling.project_config import (
        HarnessDescriptor,
        ProjectConfig,
    )


# The built-in harness name: the claude-code engine that has always been
# the only thing Archon runs. Routing a responsibility to this harness is
# byte-for-byte equivalent to constructing a ``ClaudeAgent`` directly.
DEFAULT_HARNESS = "claude-code"


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


def _emit_prompt(
    jsonl_path: str,
    *,
    prompt: str,
    attempt: int | None = None,
    resume_session_id: str | None = None,
) -> None:
    """Record the full initial prompt sent to claude.

    Stamped right after ``session_start`` on every run (including each
    idle-timeout retry, since the prompt is what's being re-sent), so
    the dashboard / log viewer can show exactly what the agent
    received. Critically, when a user adds a directive via
    ``USER_HINTS.md`` and the agent appears to ignore it, the prompt
    event makes it trivial to verify whether the hint actually made it
    into the prompt — without re-running the plan-prompt builder by
    hand to reconstruct the string.

    On ``--resume`` runs the recorded prompt is the short continuation
    message (``PLAN_CONTINUE`` / ``PROVER_CONTINUE`` / ``REVIEW_CONTINUE``),
    not the original full prompt — that one lives in the prior session's
    transcript inside Claude Code's store. ``resume_session_id`` is
    stamped so the consumer knows this is a continuation, not a fresh
    submission.
    """
    row: dict[str, object] = {
        "ts": _now_iso(),
        "event": "prompt",
        "prompt": prompt,
        "length": len(prompt),
    }
    if attempt is not None:
        row["attempt"] = attempt
    if resume_session_id:
        row["resume_session_id"] = resume_session_id
    try:
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        # Best-effort; never let a logging failure mask the actual run.
        pass


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
session_id_emitted = False

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

    # Capture the session id from the first event that carries it
    # (every claude-code event has `session_id` at the top level). Emit
    # a `session_meta` row so extract_session_id can recover the id
    # even when the run crashes before reaching the `result` event.
    if not session_id_emitted:
        early_sid = obj.get('session_id', '') or ''
        if early_sid:
            emit('session_meta', session_id=early_sid)
            session_id_emitted = True

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
        import re as _re_session
        cost = obj.get('total_cost_usd', 0) or obj.get('cost_usd', 0) or 0
        duration = obj.get('duration_ms', 0) or 0
        turns = obj.get('num_turns', 0) or 0
        session_id = obj.get('session_id', '') or ''
        result = obj.get('result', '')
        usage = obj.get('usage', {{}}) or {{}}
        model_usage = obj.get('modelUsage', {{}}) or {{}}
        used_fallback = not (isinstance(result, str) and result)
        summary = result if isinstance(result, str) and result else last_result

        # Detect "session ended mid-dispatch": when Claude Code returned
        # no final result (empty `result` field) AND last_result reads
        # like dispatch narration. The dashboard renders this as a
        # distinct state so users don't mistake the launch-message for
        # the session's conclusion.
        _DISPATCH_NARRATION = _re_session.compile(
            r"\b(dispatching|now waiting|waiting for|will continue|in flight|"
            r"directive prepared|launched|in background)\b",
            _re_session.IGNORECASE,
        )
        ended_early = bool(
            used_fallback and isinstance(summary, str)
            and _DISPATCH_NARRATION.search(summary)
        )
        if ended_early:
            summary = "[session ended mid-dispatch] " + summary

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
            ended_early=ended_early,
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


# ── AgentRunner protocol ──────────────────────────────────────────────


@runtime_checkable
class AgentRunner(Protocol):
    """The engine interface every call site programs against.

    Today :class:`ClaudeAgent` is the only implementation; Phase 2 adds
    sibling ``CodexAgent`` / ``GeminiAgent`` runners. Callers obtain a
    runner from :func:`build_runner` instead of constructing
    ``ClaudeAgent`` directly, so swapping the engine for a role becomes a
    config change rather than an edit at every site.

    The two methods mirror :class:`ClaudeAgent`'s existing signatures
    exactly — headless ``run`` and foreground ``run_interactive`` — so a
    runner is a drop-in for the legacy agent object.
    """

    def run(
        self,
        prompt: str,
        *,
        cwd: Path,
        log_base: Path | None = None,
        verbose_logs: bool = False,
        extra_args: list[str] | None = None,
        env_overrides: dict[str, str] | None = None,
        cancel_event: "threading.Event | None" = None,
        idle_timeout_s: float | None = 900,
        max_attempts: int = 3,
        resume_session_id: str | None = None,
    ) -> bool:
        ...

    def run_interactive(
        self,
        prompt: str,
        *,
        cwd: Path,
        extra_args: list[str] | None = None,
    ) -> int:
        ...


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
        resume_session_id: str | None = None,
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

        ``resume_session_id`` enables Claude Code's session-resume mode.
        When set, ``--resume <id>`` is prepended to the command so claude
        continues the prior conversation instead of starting fresh; the
        ``prompt`` then acts as the next user turn (callers typically
        send a short "continue from where you left off" message). The
        caller is responsible for sourcing the id (e.g. from a prior
        iter's meta.json via ``state.read_meta``).
        """
        real_model, provider_env_vars, provider = self._resolve_provider()
        cmd = ["claude"]
        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])
        cmd.extend(["-p", prompt, *self._build_flags(real_model)])
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
                prompt=prompt,
                resume_session_id=resume_session_id,
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
                log.warn(
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
        prompt: str | None = None,
        resume_session_id: str | None = None,
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
        if prompt is not None:
            _emit_prompt(
                jsonl,
                prompt=prompt,
                attempt=attempt if attempt > 1 else None,
                resume_session_id=resume_session_id,
            )

        cmd = cmd + ["--verbose", "--output-format", "stream-json"]
        parser_script = _STREAM_PARSER.format(
            verbose=str(verbose_logs),
            raw_log=raw_log,
            jsonl=jsonl,
        )

        stderr_dest = raw_log if verbose_logs else os.devnull
        result = supervise_streamed_run(
            cmd,
            cwd=cwd,
            env=env,
            jsonl_path=jsonl_path,
            parser_cmd=[sys.executable, "-u", "-c", parser_script],
            stderr_dest=stderr_dest,
            cancel_event=cancel_event,
            idle_timeout_s=idle_timeout_s,
            attempt=attempt,
            role=self.role,
            on_idle_timeout=lambda idle_s, att: _emit_idle_timeout(
                jsonl, idle_s, att
            ),
        )

        if result.cancelled:
            return RunOutcome.CANCELLED
        if result.idle_timeout_hit:
            return RunOutcome.IDLE_TIMEOUT

        # Some lanes legitimately end with a non-zero return even though
        # the assistant produced a valid session_end — check the JSONL
        # before flagging the run as failed.
        from archon.session_log import (
            read_last_session_end,
            session_end_indicates_success,
        )

        if result.returncode == 0:
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


@dataclass
class SupervisionResult:
    """Outcome of a single :func:`supervise_streamed_run` invocation.

    The CLI-agnostic supervisor reports only the mechanical facts of the
    run; mapping them onto a :class:`RunOutcome` (success vs. failure vs.
    rate-limit, etc.) is the calling agent's job, since that depends on
    the engine's own exit-code / log semantics.

    Attributes:
        returncode: the agent process's exit code (``None`` only if it
            could not be reaped, which the supervisor never lets happen).
        cancelled: ``cancel_event`` fired and the process was torn down.
        idle_timeout_hit: the watchdog killed the process after
            ``idle_timeout_s`` of zero JSONL activity.
    """

    returncode: int | None
    cancelled: bool
    idle_timeout_hit: bool


def supervise_streamed_run(
    agent_cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None,
    jsonl_path: Path,
    activity_path: Path | None = None,
    parser_cmd: list[str] | None = None,
    stdout_dest: str | None = None,
    stderr_dest: str,
    cancel_event: "threading.Event | None" = None,
    idle_timeout_s: float | None = 900,
    attempt: int = 1,
    role: str | None = None,
    on_idle_timeout=None,
) -> SupervisionResult:
    """Spawn one streaming agent process and supervise it to completion.

    This is the CLI-agnostic supervision core shared by
    :class:`ClaudeAgent` and :class:`~archon.agents.codex.CodexAgent`.
    Both engines stream JSONL events line-by-line; the only differences
    are how the command is built and how success is judged, so everything
    *between* (process spawn + cancel handling + idle watchdog + ordered
    teardown) lives here exactly once.

    The agent process is launched with ``stdout`` either piped into
    ``parser_cmd`` (claude's ``stream-json`` normaliser) or redirected to
    ``stdout_dest`` (codex writes its native JSONL straight to disk).
    Exactly one of ``parser_cmd`` / ``stdout_dest`` must be given. The
    process whose lifetime the watchdog loop polls is the parser when
    present, else the agent itself.

    The idle watchdog uses the mtime of ``activity_path`` (defaults to
    ``jsonl_path``) as a cheap liveness signal: every emitted event
    flushes the JSONL, so a stalled provider stops bumping the mtime and
    is killed after ``idle_timeout_s`` seconds. ``on_idle_timeout`` is an
    optional ``(idle_s, attempt) -> None`` callback fired just before the
    kill (used to stamp an ``idle_timeout`` row into the log).

    Returns a :class:`SupervisionResult`; the caller maps it to a
    :class:`RunOutcome`.
    """
    if (parser_cmd is None) == (stdout_dest is None):
        raise ValueError(
            "supervise_streamed_run: pass exactly one of parser_cmd / stdout_dest"
        )
    watch_path = activity_path if activity_path is not None else jsonl_path

    cancelled = False
    idle_timeout_hit = False

    with open(stderr_dest, "a") as stderr_file:
        if parser_cmd is not None:
            agent_proc = subprocess.Popen(
                agent_cmd,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                cwd=cwd,
                env=env,
            )
            parser_proc: subprocess.Popen | None = subprocess.Popen(
                parser_cmd,
                stdin=agent_proc.stdout,
                cwd=cwd,
            )
            assert agent_proc.stdout is not None
            agent_proc.stdout.close()
            watched = parser_proc
        else:
            stdout_file = open(stdout_dest, "a")  # type: ignore[arg-type]
            agent_proc = subprocess.Popen(
                agent_cmd,
                stdout=stdout_file,
                stderr=stderr_file,
                cwd=cwd,
                env=env,
            )
            stdout_file.close()
            parser_proc = None
            watched = agent_proc

        # Idle-watchdog state. We use the JSONL file's mtime as the
        # liveness signal because each emitted event flushes the file —
        # cheap, no extra IPC, and survives even if the agent's stdout is
        # buffered upstream.
        last_activity = time.monotonic()
        try:
            last_mtime = watch_path.stat().st_mtime
        except OSError:
            last_mtime = 0.0

        # Poll instead of blocking on .wait() so we can honour
        # cancel_event from another thread (multilane uses this to kill
        # slow lanes after another lane wins) and the idle watchdog. Tick
        # every 200 ms — fine-grained enough to feel responsive, coarse
        # enough to be cheap.
        while watched.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                _terminate_process(agent_proc, sig=signal.SIGTERM)
                break

            if idle_timeout_s is not None:
                try:
                    mtime = watch_path.stat().st_mtime
                except OSError:
                    mtime = last_mtime
                if mtime > last_mtime:
                    last_mtime = mtime
                    last_activity = time.monotonic()
                elif time.monotonic() - last_activity > idle_timeout_s:
                    idle_timeout_hit = True
                    log.warn(
                        f"No JSONL activity for {idle_timeout_s}s on "
                        f"attempt {attempt}; terminating agent."
                    )
                    if on_idle_timeout is not None:
                        on_idle_timeout(idle_timeout_s, attempt)
                    _terminate_process(agent_proc, sig=signal.SIGTERM)
                    break

            time.sleep(0.2)

        if cancelled or idle_timeout_hit:
            # Give the agent a moment to tear down on its own, then
            # escalate to SIGKILL. A piped parser exits on its own once
            # the agent's stdout closes.
            try:
                agent_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _terminate_process(agent_proc, sig=signal.SIGKILL)
                agent_proc.wait()
            if parser_proc is not None:
                try:
                    parser_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_process(parser_proc, sig=signal.SIGKILL)
                    parser_proc.wait()
        else:
            # Normal teardown: the watched process already exited. If the
            # agent lingers (slow MCP teardown, for example), signal it.
            try:
                agent_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _terminate_process(agent_proc, sig=signal.SIGTERM)
                try:
                    agent_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _terminate_process(agent_proc, sig=signal.SIGKILL)
                    agent_proc.wait()

    return SupervisionResult(
        returncode=agent_proc.returncode,
        cancelled=cancelled,
        idle_timeout_hit=idle_timeout_hit,
    )


# ── runner factory ────────────────────────────────────────────────────


class UnknownHarnessError(ValueError):
    """Raised when a configured harness has no runner in this version."""


def build_runner(
    *,
    role: str,
    model: str = DEFAULT_MODEL,
    cfg: "ProjectConfig | None" = None,
    harness: str | None = None,
    descriptor: "HarnessDescriptor | None" = None,
) -> AgentRunner:
    """Build the :class:`AgentRunner` for a responsibility.

    This is the single decision point for routing a role to an engine.
    Registered runners: ``"claude-code"`` (the built-in
    :class:`ClaudeAgent`) and ``"codex"`` (:class:`~archon.agents.codex.CodexAgent`,
    Phase 2). An unknown runner raises :class:`UnknownHarnessError`.

    Resolution of *which* harness:

    * ``descriptor`` (if given) is used verbatim — callers that already
      resolved the picklable :class:`HarnessDescriptor` at the dispatch
      site (the prover pool / subagent / lane) pass it directly, so the
      worker builds a fully-configured runner (model / effort / gateway)
      without re-reading config.
    * else ``harness`` (if given) is the harness name — resolved to a
      descriptor via ``cfg`` when present, else used as a bare name.
    * else ``cfg`` + ``role`` are resolved with
      :func:`resolve_role_harness` (``loop.roles.<role>`` >
      ``loop.harness`` > ``"claude-code"``).
    * with none of those, the harness is the built-in ``"claude-code"``.

    Zero-regression invariant: when the resolved harness is
    ``"claude-code"`` AND no explicit ``harnesses."claude-code"`` entry
    is configured, this returns exactly ``ClaudeAgent(model=model,
    role=role)`` — the same object the call site built before the router,
    with no new parsing on the default path.

    Raises:
        UnknownHarnessError: the resolved harness names a runner that has
            no implementation in this version.
    """
    # If a fully-resolved descriptor was handed in, dispatch on it
    # directly — no name resolution, no config re-read.
    if descriptor is not None:
        return _runner_from_descriptor(descriptor, model=model, role=role)

    # Resolve the harness name.
    if harness is None:
        if cfg is not None:
            from archon.commands.tooling.project_config import resolve_role_harness

            harness = resolve_role_harness(cfg, role, fallback=DEFAULT_HARNESS)
        else:
            harness = DEFAULT_HARNESS

    # Fast path / zero-regression short-circuit: the built-in claude-code
    # harness with no explicit descriptor override is the legacy agent,
    # untouched. We deliberately skip descriptor loading entirely so the
    # default path proves it never depends on the new config code.
    has_override = False
    if cfg is not None:
        from archon.commands.tooling.project_config import (
            has_explicit_harness_override,
        )

        has_override = has_explicit_harness_override(cfg, harness)

    if harness == DEFAULT_HARNESS and not has_override:
        return ClaudeAgent(model=model, role=role)

    # A configured harness descriptor: load + dispatch on its runner.
    from archon.commands.tooling.project_config import load_harness_descriptor

    resolved = load_harness_descriptor(cfg, harness) if cfg is not None else None
    if resolved is None:
        # No cfg to load from, but a non-default harness name was passed
        # directly (e.g. by a pool worker before descriptors were
        # threaded). Synthesize a minimal descriptor whose runner is the
        # name itself, so an unknown name still raises clearly.
        from archon.commands.tooling.project_config import HarnessDescriptor

        resolved = HarnessDescriptor(name=harness, runner=harness)
    return _runner_from_descriptor(resolved, model=model, role=role)


def _runner_from_descriptor(
    descriptor: "HarnessDescriptor", *, model: str, role: str,
) -> AgentRunner:
    """Construct the engine runner named by a resolved descriptor.

    The single place that maps ``descriptor.runner`` → a concrete
    :class:`AgentRunner`. ``claude-code`` honours the descriptor's model
    override (else keeps ``model``); ``codex`` is built straight from the
    descriptor (model / effort / sandbox / gateway all live there).
    """
    runner = descriptor.runner
    if runner == DEFAULT_HARNESS:
        if descriptor.model:
            model = descriptor.model
        return ClaudeAgent(model=model, role=role)
    if runner == "codex":
        from archon.agents.codex import CodexAgent

        return CodexAgent(descriptor=descriptor, role=role)
    raise UnknownHarnessError(
        f"Harness {descriptor.name!r} requests runner {runner!r}, but the "
        f"runners available in this version of Archon are "
        f"{DEFAULT_HARNESS!r} and 'codex'. Remove the harness override or "
        f"set its runner to a supported value."
    )


__all__ = [
    "AgentRunner",
    "ClaudeAgent",
    "DEFAULT_HARNESS",
    "DEFAULT_MODEL",
    "RunOutcome",
    "SupervisionResult",
    "UnknownHarnessError",
    "build_runner",
    "supervise_streamed_run",
]
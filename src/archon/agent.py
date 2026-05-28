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
from dataclasses import dataclass, field
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
    "openrouter": "openrouter",
}


class RunOutcome(enum.Enum):
    """Result of a single ``_run_with_logging`` attempt.

    Distinguishing outcomes matters:
    - IDLE_TIMEOUT: provider went silent; restart the same prompt.
    - OVERLOADED: 529 server overload; wait + retry with backoff.
    - QUOTA_EXHAUSTED: hard weekly/session limit; stop the loop.
    - FAILED: real failure (bad prompt, auth, etc.); no retry.
    """

    SUCCESS = "success"
    FAILED = "failed"
    IDLE_TIMEOUT = "idle_timeout"
    CANCELLED = "cancelled"
    OVERLOADED = "overloaded"
    QUOTA_EXHAUSTED = "quota_exhausted"


class QuotaExhaustedError(RuntimeError):
    """Raised when the API reports a hard usage-limit (weekly or session).

    Callers that run a loop should catch this and stop iterating — retrying
    will just hit the same wall and pollute the logs with empty iterations.
    """


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


# ── ClaudeBackend ─────────────────────────────────────────────────────


class ClaudeBackend:
    """Extension point for how ``claude -p`` is invoked.

    The default implementation issues a plain ``claude -p <prompt>``
    subprocess. Subclass and override ``build_headless`` to change the
    command or inject environment variables — the returned (cmd, env)
    pair is forwarded directly to ``subprocess.Popen``.

    Adding a new backend (e.g. a Python wrapper that makes the
    interactive ``claude`` session headless) is a single new subclass
    here; nothing else in ``ClaudeAgent`` or its callers changes.
    """

    def build_headless(
        self,
        prompt: str,
        *,
        model: str,
        flags: list[str],
        resume_session_id: str | None,
        base_env: dict[str, str],
        log_base: "Path | str | None" = None,
    ) -> tuple[list[str], dict[str, str]]:
        """Return ``(command, env)`` for the headless ``claude -p`` call.

        ``base_env`` is already the fully merged environment (OS env +
        provider overrides + caller-supplied overrides + IS_SANDBOX).
        Subclasses may return a modified copy.

        ``log_base`` is the phase log stem (e.g. ``…/iter-001/plan``) so
        subclasses can place sidecar diagnostics next to the JSONL. The
        base implementation ignores it.
        """
        cmd = ["claude"]
        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])
        cmd.extend(["-p", prompt, *flags])
        return cmd, base_env


class ClaudePBackend(ClaudeBackend):
    """Uses ``claude-p`` instead of ``claude -p`` for headless invocations.

    ``claude-p`` is a drop-in ``claude -p`` replacement backed by the
    interactive Claude Code TUI — useful when the standard headless path
    is rate-limited or unavailable on a subscription account.  It accepts
    the same flags (``--model``, ``--resume``, ``--permission-mode``,
    ``--output-format stream-json``, ``--verbose``) but the prompt is
    positional rather than the argument to ``-p``.

    ``config_dir`` pins ``CLAUDE_CONFIG_DIR`` for the spawned process,
    selecting which Claude Code login / account to use.  When ``None``,
    the value already in the environment is used as-is.

    ``timeout_sec`` overrides claude-p's ``--timeout-sec`` (default 90 s —
    far too short for multi-tool-call plan/prover agents).  Archon's own
    idle watchdog provides the outer kill; set this to a comfortable margin
    above the longest expected agent run.

    ``quiet_after_sec`` overrides claude-p's ``--quiet-after-sec`` (default
    3 s).  Raised slightly to avoid premature exit during gaps between tool
    calls (e.g. lake build).
    """

    _DEFAULT_TIMEOUT_SEC = 1800   # 30 min — archon's idle watchdog is 15 min
    _DEFAULT_QUIET_AFTER_SEC = 15  # allow gaps between tool calls

    def __init__(
        self,
        config_dir: str | None = None,
        timeout_sec: int | None = None,
        quiet_after_sec: int | None = None,
    ) -> None:
        self.config_dir = config_dir
        self.timeout_sec = timeout_sec or self._DEFAULT_TIMEOUT_SEC
        self.quiet_after_sec = quiet_after_sec or self._DEFAULT_QUIET_AFTER_SEC

    def build_headless(
        self,
        prompt: str,
        *,
        model: str,
        flags: list[str],
        resume_session_id: str | None,
        base_env: dict[str, str],
        log_base: "Path | str | None" = None,
    ) -> tuple[list[str], dict[str, str]]:
        cmd = ["claude-p"]
        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])
        cmd.extend([
            prompt, *flags,
            "--timeout-sec", str(self.timeout_sec),
            "--quiet-after-sec", str(self.quiet_after_sec),
            # Emit assistant text as it appears in the TUI instead of
            # buffering until the session ends — gives the dashboard
            # something to show mid-run.
            "--live-tui-deltas",
        ])
        # Capture claude-p's raw PTY transcript next to the phase JSONL.
        # This is the single most useful artifact when a run stalls: it
        # shows the actual interactive screen (auth prompts, rate-limit
        # banners, the bypass-permissions menu, MCP startup errors) that
        # the stream-json output never surfaces.
        if log_base is not None:
            raw_log = f"{log_base}.claude-p-raw.log"
            cmd.extend(["--raw-log", raw_log])
            log.info(f"claude-p raw transcript: {raw_log}")
        if self.config_dir:
            env = {**base_env, "CLAUDE_CONFIG_DIR": self.config_dir}
        else:
            env = base_env
        self._ensure_bypass_setting(env.get("CLAUDE_CONFIG_DIR"))
        return cmd, env

    @staticmethod
    def _ensure_bypass_setting(config_dir: str | None) -> None:
        """Suppress interactive `claude`'s one-time "Bypass Permissions mode"
        acceptance menu by setting ``skipDangerousModePermissionPrompt`` in the
        target config dir's ``settings.json``.

        Headless ``claude -p`` never shows that menu, but claude-p drives the
        real TUI, which stalls on it indefinitely (no assistant output until
        ``--timeout-sec``) because there's no human to press "Yes, I accept".
        The key is merged in idempotently — the file is created if absent and
        all other keys are preserved.
        """
        base = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
        settings_path = base / "settings.json"
        try:
            data = (
                json.loads(settings_path.read_text())
                if settings_path.exists() else {}
            )
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict) or data.get("skipDangerousModePermissionPrompt") is True:
            return
        data["skipDangerousModePermissionPrompt"] = True
        try:
            base.mkdir(parents=True, exist_ok=True)
            tmp = settings_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2) + "\n")
            os.replace(tmp, settings_path)  # atomic: avoids torn writes under parallel provers
            log.info(
                f"claude-p: enabled skipDangerousModePermissionPrompt in {settings_path}"
            )
        except OSError:
            pass


class EntrypointBackend(ClaudeBackend):
    """Sets ``CLAUDE_CODE_ENTRYPOINT`` before invoking the standard binary.

    Use ``entrypoint="claude-vscode"`` or ``entrypoint="claude-desktop"``
    to run inside the matching session variant. ``entrypoint=None`` (the
    default) falls back to the base :class:`ClaudeBackend` behaviour with
    no env injection.
    """

    def __init__(self, entrypoint: str | None = None) -> None:
        self.entrypoint = entrypoint

    def build_headless(
        self,
        prompt: str,
        *,
        model: str,
        flags: list[str],
        resume_session_id: str | None,
        base_env: dict[str, str],
        log_base: "Path | str | None" = None,
    ) -> tuple[list[str], dict[str, str]]:
        env = (
            {**base_env, "CLAUDE_CODE_ENTRYPOINT": self.entrypoint}
            if self.entrypoint
            else base_env
        )
        cmd = ["claude"]
        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])
        cmd.extend(["-p", prompt, *flags])
        return cmd, env


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
    backend: ClaudeBackend = field(default_factory=ClaudeBackend)

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
        from archon.commands.tooling.env_loader import (
            PROVIDERS, openrouter_fallback_env, provider_env,
        )

        provider = PROVIDER_ALIASES[self.model]
        env = provider_env(provider)
        if not env:
            # Before giving up, try routing through OpenRouter if its key
            # is set and the provider has a known model slug there.
            if provider != 'openrouter':
                fallback = openrouter_fallback_env(provider)
                if fallback:
                    real_model = fallback.get("ANTHROPIC_MODEL", self.model)
                    log.info(
                        f"No {provider} key found; "
                        f"routing through OpenRouter ({real_model})."
                    )
                    return real_model, fallback, 'openrouter'
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

        merged = dict(provider_env_vars)
        if env_overrides:
            merged.update(env_overrides)
        base_env = self._build_env(merged)

        cmd, env = self.backend.build_headless(
            prompt,
            model=real_model,
            flags=self._build_flags(real_model),
            resume_session_id=resume_session_id,
            base_env=base_env,
            log_base=log_base,
        )
        if extra_args:
            cmd.extend(extra_args)

        self._announce_model(real_model=real_model, provider=provider)

        if log_base is None:
            # Without a log file the watchdog has nothing to read; fall
            # back to the simple blocking invocation.
            return subprocess.run(cmd, cwd=cwd, env=env).returncode == 0

        if max_attempts < 1:
            max_attempts = 1

        # Separate retry budgets: idle-timeout retries are caller-controlled
        # via max_attempts; overload retries are always enabled with their
        # own cap and exponential back-off so transient 529s don't abort work.
        _OVERLOAD_MAX_RETRIES = 5
        _OVERLOAD_BASE_SLEEP_S = 30

        last_outcome: RunOutcome = RunOutcome.FAILED
        idle_attempt = 0
        overload_attempt = 0

        while idle_attempt < max_attempts:
            idle_attempt += 1
            outcome = self._run_with_logging(
                cmd,
                cwd=cwd,
                log_base=log_base,
                verbose_logs=verbose_logs,
                env=env,
                cancel_event=cancel_event,
                jsonl_model=real_model,
                idle_timeout_s=idle_timeout_s,
                attempt=idle_attempt,
                prompt=prompt,
                resume_session_id=resume_session_id,
            )
            last_outcome = outcome

            if outcome is RunOutcome.SUCCESS:
                return True
            if outcome is RunOutcome.CANCELLED:
                return False
            if outcome is RunOutcome.QUOTA_EXHAUSTED:
                raise QuotaExhaustedError(
                    "API quota exhausted — stop the loop and wait for the "
                    "limit to reset before resuming."
                )
            if outcome is RunOutcome.FAILED:
                # A real failure — retrying won't fix it and would just
                # burn tokens. Stop here.
                return False
            if outcome is RunOutcome.OVERLOADED:
                overload_attempt += 1
                if overload_attempt > _OVERLOAD_MAX_RETRIES:
                    log.error(
                        f"API still overloaded after {_OVERLOAD_MAX_RETRIES} "
                        f"retries; giving up."
                    )
                    return False
                wait_s = min(
                    _OVERLOAD_BASE_SLEEP_S * (2 ** (overload_attempt - 1)),
                    300,
                )
                log.warn(
                    f"API overloaded (retry {overload_attempt}/"
                    f"{_OVERLOAD_MAX_RETRIES}); waiting {wait_s}s."
                )
                if cancel_event is not None and cancel_event.is_set():
                    return False
                time.sleep(wait_s)
                # Don't count this against the idle-timeout budget.
                idle_attempt -= 1
                continue

            # outcome is IDLE_TIMEOUT: provider went silent. Retry the
            # same prompt unless the caller asked us to stop or we've
            # hit the attempt cap.
            if cancel_event is not None and cancel_event.is_set():
                return False
            if idle_attempt < max_attempts:
                log.warn(
                    f"Run idle for {idle_timeout_s}s on attempt "
                    f"{idle_attempt}/{max_attempts}; restarting same prompt."
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
                        log.warn(
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
            session_end_failure_kind,
            session_end_indicates_success,
        )

        if claude_proc.returncode == 0:
            return RunOutcome.SUCCESS
        session_end = read_last_session_end(jsonl)
        if session_end_indicates_success(session_end):
            return RunOutcome.SUCCESS
        # Refine the failure so the retry loop can act on it.
        kind = session_end_failure_kind(session_end)
        if kind == 'overloaded':
            return RunOutcome.OVERLOADED
        if kind == 'quota_exhausted':
            return RunOutcome.QUOTA_EXHAUSTED
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


__all__ = [
    "ClaudeAgent", "ClaudeBackend", "ClaudePBackend", "EntrypointBackend",
    "DEFAULT_MODEL", "RunOutcome", "QuotaExhaustedError",
]
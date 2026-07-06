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
import functools
import glob
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
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


# Native Claude Code tools that every archon agent is forbidden to use,
# passed to ``claude --disallowedTools``.
#
# Archon's whole orchestration model assumes each phase agent runs as a
# one-shot headless ``claude -p`` whose turn does NOT end until all the
# work it spawned is done: the orchestrator (e.g. DagElaborationPhase)
# blocks on the subprocess and advances to the next iteration the moment
# it exits. The only sanctioned way to dispatch a subagent is the
# *blocking* ``python3 .claude/tools/archon-subagent.py`` Bash call (see
# the dispatcher pattern in prompts/dag.md and prompts/plan.md) — it runs
# the child synchronously inside the dispatching agent's turn, so the
# headless process stays alive until every dispatch returns.
#
# The native ``Agent`` (sub-agent spawn) and ``ScheduleWakeup`` tools
# break that contract: an agent that backgrounds its dispatches and
# schedules a wakeup ends its turn immediately. In headless ``-p`` there
# is no persistent runtime to honor the wakeup, so the process exits, the
# orchestrator treats the clean exit as "phase done" and launches the
# next iteration — while the just-spawned subagents are still in flight,
# now orphaned and racing the next iteration's writes to the same files.
# (The stream parser already *detects* this as ``[session ended
# mid-dispatch]``; blocking the tools is what *prevents* it.)
#
# The actual in-turn wait is *staying inside the blocking foreground dispatch
# Bash call*: ``archon-subagent.py`` is synchronous (``subprocess.run``), and
# ``BASH_FOREGROUND_TIMEOUT_MS`` (below) raises the auto-background ceiling to
# 30 min so a normal dispatch — including a ``& … & wait`` wave — stays
# foreground and holds the turn open until every child has written its report.
# ``prompts/{plan,dag,review}.md`` direct the agent to that pattern and to read
# the reports only *after* the call returns, all in the one-shot turn (there is
# no resume runtime / "next turn").
#
# ``Monitor`` is NOT a wait primitive here: it streams events and returns
# control immediately ("keep working"), so an agent that "waits" with Monitor
# ends its turn with the dispatch still in flight — the historical fable-5
# failure. The prompts therefore forbid using it to wait. It is left out of the
# disallow list only because nothing in the loop relies on it and the real
# primitives (Bash/Read/Write/Edit) are unaffected; if prompt discipline ever
# proves insufficient, adding ``Monitor`` here is a safe further hardening.
# The genuine turn-enders are ``Agent``/``Task`` (spawn-and-return) and
# ``ScheduleWakeup`` (async wakeup with no blocking mode); those stay banned.
#
# ``Task`` is included defensively to cover CLI builds that name the
# spawner ``Task`` rather than ``Agent``; archon never relies on a native
# task-checklist tool, so disallowing it is harmless. Bash/Read/Write/
# Edit/Grep/Glob/WebSearch/WebFetch/TodoWrite are unaffected.
DISALLOWED_NATIVE_TOOLS: tuple[str, ...] = (
    "Agent",
    "Task",
    "ScheduleWakeup",
    # Prover lanes must not manage process lifetime themselves. A Fable run
    # used `pkill -f "lean.*File"` as "cleanup", which can match the shell
    # running the command and terminate the lane before it writes a report.
    # Keep Bash available, but block the common process-kill entry points.
    "Bash(pkill:*)",
    "Bash(killall:*)",
    "Bash(kill:*)",
)


# Foreground-Bash timeout (ms) handed to every spawned ``claude`` via
# BASH_DEFAULT_TIMEOUT_MS / BASH_MAX_TIMEOUT_MS.
#
# Claude Code defaults a Bash command to a 2-minute timeout with a
# 10-minute hard ceiling, and AUTO-BACKGROUNDS a foreground command that
# overruns it (``Command running in background with ID: …``). That is
# fatal to archon's dispatch model: the sanctioned way to launch a
# subagent is a *blocking* ``python3 .archon/.../archon-subagent.py`` Bash
# call whose child ``claude`` session routinely runs 10-15+ minutes. Once
# that call auto-backgrounds, the dispatching agent's turn ends, the
# headless ``-p`` process exits, and the orchestrator advances to the
# next iteration while the subagent is still running (the same race the
# DISALLOWED_NATIVE_TOOLS guard addresses, reached by a different door).
# The planner never trips this only because its subagents finish under
# the 2-minute default; the dag-walkers reliably exceed it.
#
# 30 minutes comfortably covers a large dependency-cone walker. A
# genuinely hung child is still bounded by the inner agent's own idle
# watchdog (``idle_timeout_s`` in ``run``), which kills the child claude
# and lets the Bash call return — so a high ceiling doesn't mean a
# wedged dispatch runs forever. Set via setdefault so a shell/user
# override still wins.
BASH_FOREGROUND_TIMEOUT_MS = 30 * 60 * 1000


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


def _emit_session_meta(jsonl_path: str, *, session_id: str) -> None:
    """Stamp the Claude Code session id so ``--resume`` can recover it.

    The interactive backend pins its own ``--session-id``; recording it as a
    ``session_meta`` row (the same shape the headless parser emits) lets
    :func:`archon.state.extract_session_id` find it, so an interactive phase is
    resumable just like a headless one.
    """
    row = {"ts": _now_iso(), "event": "session_meta", "session_id": session_id}
    try:
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


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


def _emit_interactive_session_end(jsonl_path: str, *, ok: bool, summary: str) -> None:
    """Stamp a minimal ``session_end`` for a foreground interactive run.

    A human-driven ``claude`` session produces no stream-json to parse, so
    the dashboard / meta would otherwise see only a ``session_start``. We
    write a single synthetic ``session_end`` (no token/cost data is
    available from a TUI session) so the log is well-formed and the run
    reads as finished. Phase-level success is judged downstream by the
    usual sorry-count / git-diff checks, not this row.
    """
    row = {
        "ts": _now_iso(),
        "event": "session_end",
        "total_cost_usd": 0,
        "duration_ms": 0,
        "num_turns": 1,
        "summary": summary,
        "interactive": True,
        "ok": ok,
    }
    try:
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def _claude_config_dir(env: "dict | None" = None) -> Path:
    """Claude Code's config root, honouring ``CLAUDE_CONFIG_DIR``.

    Mirrors claude-p: a custom config dir (e.g. ``CLAUDE_CONFIG_DIR=~/.claude-
    sophie``) moves the whole ``projects/`` session store, so the transcript we
    tail lives under it, not the default ``~/.claude``.
    """
    src = (env or os.environ).get("CLAUDE_CONFIG_DIR")
    return Path(src).expanduser() if src else Path.home() / ".claude"


def _find_claude_session_file(session_id: str, *, env: "dict | None" = None) -> "Path | None":
    """Locate Claude Code's session transcript for a KNOWN session id.

    Claude Code writes interactive sessions to
    ``<config-dir>/projects/<sanitized-cwd>/<session-id>.jsonl``. Rather than
    guess the sanitized dir or the newest file (fragile across config dirs and
    path-sanitisation rules), we glob by the session id we passed to
    ``claude --session-id`` — exactly how claude-p finds it. Returns None until
    the file exists (the TUI creates it a beat after launch).
    """
    try:
        pattern = str(_claude_config_dir(env) / "projects" / "**" / f"{session_id}.jsonl")
        paths = [Path(p) for p in glob.glob(pattern, recursive=True)]
        if not paths:
            return None
        return max(paths, key=lambda p: p.stat().st_mtime)
    except OSError:
        return None


def _claude_tool_result_text(content: object) -> str:
    """Flatten a Claude session ``tool_result`` content into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                texts.append(b.get("text") or "")
            elif isinstance(b, str):
                texts.append(b)
        return "\n".join(t for t in texts if t)
    return ""


def _claude_session_obj_to_rows(obj: object) -> list[dict]:
    """Convert one Claude Code session-store JSON object into archon rows.

    Maps an ``assistant`` / ``user`` line's content blocks to ``thinking`` /
    ``text`` / ``tool_call`` / ``tool_result`` events — the SAME schema the
    headless stream parser emits. Unrecognised types/blocks yield ``[]``.
    Shared by the post-run importer and the live tailer so both stay in sync.
    """
    if not isinstance(obj, dict):
        return []
    typ = obj.get("type")
    if typ not in ("assistant", "user"):
        return []
    msg = obj.get("message") or {}
    content = msg.get("content")
    ts = obj.get("timestamp") or _now_iso()
    if not isinstance(content, list):
        return []
    rows: list[dict] = []
    for b in content:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if typ == "assistant" and bt == "thinking":
            rows.append({"ts": ts, "event": "thinking", "content": b.get("thinking") or ""})
        elif typ == "assistant" and bt == "text":
            rows.append({"ts": ts, "event": "text", "content": b.get("text") or ""})
        elif typ == "assistant" and bt == "tool_use":
            rows.append({
                "ts": ts, "event": "tool_call",
                "tool": b.get("name") or "", "input": b.get("input") or {},
            })
        elif typ == "user" and bt == "tool_result":
            rows.append({
                "ts": ts, "event": "tool_result",
                "content": _claude_tool_result_text(b.get("content")),
            })
    return rows


def _import_claude_session_jsonl(src: Path, dst_jsonl: str) -> int:
    """Convert Claude Code's native session transcript into archon JSONL.

    A foreground (``interactive`` backend) session produces no stream-json for
    us to parse, so the phase log would otherwise hold only the synthetic
    ``session_start`` / ``session_end`` stamps. We read Claude Code's own
    session store and append the converted ``thinking`` / ``text`` /
    ``tool_call`` / ``tool_result`` rows so the dashboard renders the real
    conversation. Used as a one-shot fallback when the live tailer never
    locked onto a session file. Returns the number of rows written.
    """
    try:
        raw = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    rows: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        rows.extend(_claude_session_obj_to_rows(obj))
    if not rows:
        return 0
    try:
        with open(dst_jsonl, "a") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    except OSError:
        return 0
    return len(rows)


class _LiveClaudeSessionTailer(threading.Thread):
    """Tail Claude Code's session JSONL live and re-emit archon events.

    The ``interactive`` backend runs ``claude`` in the foreground, so we can't
    capture a stream from its stdout the way the ``claude-p`` backend does
    (claude-p emits ``--stream-session-events`` to a pipe). But Claude Code
    writes its session transcript to ``~/.claude/projects/<cwd>/<sid>.jsonl``
    as the conversation progresses, so we tail THAT file from a background
    thread and append converted rows to the phase JSONL in real time — giving
    the dashboard a live log just like claude-p.

    Lifecycle: ``start()`` before launching the foreground claude, ``stop()``
    (which joins) after it exits. The locked-onto file is exposed via
    :attr:`locked` so the caller can fall back to a one-shot import if the
    session never appeared while we were watching.

    It finds the transcript by the ``session_id`` we passed to
    ``claude --session-id`` (honouring ``CLAUDE_CONFIG_DIR`` via ``env``) — the
    same deterministic lookup claude-p uses, not a newest-file guess.
    """

    def __init__(
        self, session_id: str, dst_jsonl: str, *, env: "dict | None" = None,
    ) -> None:
        super().__init__(daemon=True)
        self._session_id = session_id
        self._dst = dst_jsonl
        self._env = env
        self._stop_event = threading.Event()
        self._offset = 0
        self._leftover = b""
        self._rows_written = 0
        self.locked: Path | None = None

    def stop(self) -> int:
        """Signal the thread to finish, join it, and return rows written."""
        self._stop_event.set()
        self.join(timeout=10)
        return self._rows_written

    def run(self) -> None:
        # Phase 1: wait for the session file to appear (claude creates it a
        # beat after launch). Re-scan until found, stopped, or ~exit.
        while not self._stop_event.is_set() and self.locked is None:
            self.locked = _find_claude_session_file(self._session_id, env=self._env)
            if self.locked is None:
                self._stop_event.wait(0.3)
        # Phase 2: tail the locked file until stopped, then one final drain.
        while not self._stop_event.is_set():
            self._drain()
            self._stop_event.wait(0.4)
        self._drain()

    def _drain(self) -> None:
        if self.locked is None:
            return
        try:
            with open(self.locked, "rb") as f:
                f.seek(self._offset)
                data = f.read()
        except OSError:
            return
        if not data:
            return
        self._offset += len(data)
        buf = self._leftover + data
        *complete, self._leftover = buf.split(b"\n")
        out: list[dict] = []
        for raw in complete:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, ValueError):
                continue
            out.extend(_claude_session_obj_to_rows(obj))
        if not out:
            return
        try:
            with open(self._dst, "a") as f:
                for r in out:
                    f.write(json.dumps(r) + "\n")
        except OSError:
            return
        self._rows_written += len(out)


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
MODEL = '{model}'

def emit(event_type, **fields):
    row = {{'ts': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), 'event': event_type, **fields}}
    JSONL.write(json.dumps(row) + '\n')
    JSONL.flush()

def estimate_cost_usd(model, input_tokens, output_tokens, cache_read_tokens=0):
    m = model.lower()
    if "sonnet" in m:
        return (input_tokens * 3.0 + output_tokens * 15.0 + cache_read_tokens * 0.30) / 1_000_000.0
    elif "haiku" in m:
        return (input_tokens * 0.8 + output_tokens * 4.0 + cache_read_tokens * 0.08) / 1_000_000.0
    elif "opus" in m:
        return (input_tokens * 15.0 + output_tokens * 75.0) / 1_000_000.0
    elif "flash" in m:
        return (input_tokens * 0.075 + output_tokens * 0.30) / 1_000_000.0
    elif "pro" in m:
        return (input_tokens * 1.25 + output_tokens * 5.0) / 1_000_000.0
    elif "gpt-4o-mini" in m:
        return (input_tokens * 0.15 + output_tokens * 0.60) / 1_000_000.0
    elif "gpt-4o" in m:
        return (input_tokens * 2.50 + output_tokens * 10.00) / 1_000_000.0
    elif "o1-mini" in m or "o3-mini" in m:
        return (input_tokens * 1.10 + output_tokens * 4.40) / 1_000_000.0
    elif "o1-preview" in m or "o1" in m:
        return (input_tokens * 15.00 + output_tokens * 60.00) / 1_000_000.0
    elif "deepseek-chat" in m or "deepseek-v3" in m or "deepseek" in m:
        return (input_tokens * 0.14 + output_tokens * 0.28) / 1_000_000.0
    elif "deepseek-reasoner" in m or "deepseek-r1" in m:
        return (input_tokens * 0.55 + output_tokens * 2.19) / 1_000_000.0
    return (input_tokens * 2.50 + output_tokens * 10.00) / 1_000_000.0

total_in_chars = 15000
turn_out_chars = 0

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
                    turn_out_chars += len(thinking)
            elif bt == 'text':
                text = block.get('text', '').strip()
                if text:
                    emit('text', content=text)
                    last_result = text
                    turn_out_chars += len(text)
            elif bt == 'tool_use':
                name = block.get('name', '?')
                inp = block.get('input', {{}})
                emit('tool_call', tool=name, input=inp)
                turn_out_chars += len(name) + len(json.dumps(inp))

                if turn_out_chars > 0:
                    in_t = int(total_in_chars / 4.0)
                    out_t = int(turn_out_chars / 4.0)
                    cost = estimate_cost_usd(MODEL, in_t, out_t)
                    emit('turn_usage', input_tokens=in_t, output_tokens=out_t, cost_usd=cost)
                    total_in_chars += turn_out_chars
                    turn_out_chars = 0

    elif t == 'user' and 'message' in obj:
        msg = obj['message']
        if not isinstance(msg, dict):
            continue
        for block in msg.get('content', []):
            if block.get('type') == 'tool_result':
                content = block.get('content', '')
                if isinstance(content, str):
                    emit('tool_result', content=content)
                    total_in_chars += len(content)
                elif isinstance(content, list):
                    texts = [p.get('text','') for p in content if isinstance(p,dict) and p.get('type')=='text']
                    combined = '\n'.join(texts)
                    emit('tool_result', content=combined)
                    total_in_chars += len(combined)

    elif t == 'result':
        import re as _re_session
        if turn_out_chars > 0:
            in_t = int(total_in_chars / 4.0)
            out_t = int(turn_out_chars / 4.0)
            cost = estimate_cost_usd(MODEL, in_t, out_t)
            emit('turn_usage', input_tokens=in_t, output_tokens=out_t, cost_usd=cost)
            turn_out_chars = 0
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

    :class:`ClaudeAgent` is the built-in implementation;
    :class:`~archon.agents.codex.CodexAgent` is the first sibling engine.
    Callers obtain a runner from :func:`build_runner` instead of
    constructing ``ClaudeAgent`` directly, so swapping the engine for a
    role becomes a config change rather than an edit at every site.

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

    ``name`` is the config token this backend resolves from
    (``loop.claude_backend`` / ``--claude-backend``). It is exported into
    child agent processes so nested ``archon subagent`` dispatches
    reproduce the same backend — see :meth:`ClaudeAgent._build_env`.
    """

    name: str = "default"

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


@functools.lru_cache(maxsize=1)
def _claude_p_help() -> str:
    """``claude-p --help`` text, cached once per process.

    Used to feature-detect optional flags so we never pass one an
    older / stock-upstream ``claude-p`` build doesn't recognise (argparse
    would abort the whole run). Returns ``""`` when ``claude-p`` can't be
    run (not installed / errored) — callers then simply skip the flag.
    """
    try:
        out = subprocess.run(
            ["claude-p", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        return (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def _claude_p_supports(flag: str) -> bool:
    """True iff the installed ``claude-p`` advertises ``flag`` in --help."""
    return flag in _claude_p_help()


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

    name = "claude-p"

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
        ])
        # Trust the workspace non-interactively so the TUI's "Do you trust
        # the files in this folder?" prompt can't stall a headless lane /
        # fresh-worktree run. This flag is provided by the maintained
        # claude-p fork (github.com/AxelDlv00/claude-p); feature-detected
        # so a stock-upstream build without it still runs.
        if _claude_p_supports("--trust-workspace"):
            cmd.append("--trust-workspace")
        # Give each claude-p its own throwaway CLAUDE_CONFIG_DIR (symlinking the
        # base's auth/plugins/caches, but a fresh session store). Without this,
        # two claude-p TUIs sharing one config dir — a dispatcher and the
        # subagents it spawns — clash: the non-first session never persists, so
        # --stream-session-events stays empty and the dashboard shows no
        # subagent logs (the run itself still completes). Feature-detected so an
        # older claude-p without the flag still runs.
        if _claude_p_supports("--isolate-config-dir"):
            cmd.append("--isolate-config-dir")
        # Full live logging: tail Claude Code's session JSONL and re-emit
        # real assistant/user events (text + tool calls + results) in
        # stream-json, which our parser already understands. Prefer it over
        # the lower-fidelity terminal-scraped text deltas, falling back to
        # those when an older claude-p lacks the richer flag. Both are
        # feature-detected so a stock-upstream build still runs.
        if _claude_p_supports("--stream-session-events"):
            cmd.append("--stream-session-events")
        elif _claude_p_supports("--live-tui-deltas"):
            cmd.append("--live-tui-deltas")
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

    # Map the CLAUDE_CODE_ENTRYPOINT value back to its config token so the
    # backend's identity round-trips into child agents (see ClaudeAgent.
    # _build_env). "claude-vscode" -> "vscode", "claude-desktop" -> "desktop".
    _ENTRYPOINT_TO_NAME = {"claude-vscode": "vscode", "claude-desktop": "desktop"}

    def __init__(self, entrypoint: str | None = None) -> None:
        self.entrypoint = entrypoint
        self.name = self._ENTRYPOINT_TO_NAME.get(entrypoint or "", "default")

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


class InteractiveBackend(ClaudeBackend):
    """Run ``claude`` in the **foreground**, driven by a human at the terminal.

    This is the stable subscription fallback: instead of a headless
    ``claude -p`` (or the claude-p TUI automation), it launches the real
    interactive ``claude`` session and hands the terminal to the user, who
    drives the proof themselves. :meth:`ClaudeAgent.run` detects this
    backend and routes to its foreground path instead of ``build_headless``.

    Because a human can only attend one terminal at a time, the loop forces
    ``parallel=False`` / ``max_parallel=1`` and disables multilane when this
    backend is selected (see ``commands/loop/entry.py``). ``build_headless``
    is never used for this backend; it raises to make a misuse obvious.
    """

    name = "interactive"

    def build_headless(self, *args, **kwargs):  # noqa: D401 - see class doc
        raise NotImplementedError(
            "InteractiveBackend runs claude in the foreground; it has no "
            "headless command. ClaudeAgent.run routes around build_headless "
            "for this backend."
        )


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

    def _build_flags(self, model: str, *, include_disallowed: bool = True) -> list[str]:
        flags: list[str] = []
        if self.skip_permissions:
            flags.append("--dangerously-skip-permissions")
        flags.extend(["--permission-mode", self.permission_mode])
        flags.extend(["--model", model])
        # Force the blocking-Bash dispatch path: a HEADLESS agent must never
        # spawn subagents natively or schedule a wakeup, or it ends its turn
        # mid-dispatch and the orchestrator advances over in-flight work.
        # See DISALLOWED_NATIVE_TOOLS for the full rationale.
        #
        # NOT added for interactive sessions (``include_disallowed=False``):
        # a human is driving — there is no orchestrator to outrun — AND
        # ``--disallowedTools`` is variadic, so since ``run_interactive``
        # appends the prompt as the LAST argv token, the trailing flag would
        # swallow the prompt as a bogus tool name and the TUI would open with
        # no initial message (the bare-welcome-screen bug). Headless backends
        # pass the prompt via ``-p`` BEFORE the flags, so they are unaffected.
        if include_disallowed and DISALLOWED_NATIVE_TOOLS:
            flags.extend(["--disallowedTools", *DISALLOWED_NATIVE_TOOLS])
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

        When the backend is :class:`InteractiveBackend`, this routes to the
        foreground, human-driven path (:meth:`_run_interactive_phase`)
        instead — there is no headless stream to parse.
        """
        if isinstance(self.backend, InteractiveBackend):
            return self._run_interactive_phase(
                prompt, cwd=cwd, log_base=log_base, extra_args=extra_args,
            )

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
        session_id: str | None = None,
    ) -> int:
        """Foreground interactive launch (no ``-p``).

        Used by ``archon discuss`` / ``archon refactor draft`` / the
        bootstrap flow in ``archon init`` — anywhere claude takes over
        the terminal and expects user input. Returns the subprocess exit
        code so the caller can decide how to react to non-zero.

        ``session_id`` pins Claude Code's session id (``claude --session-id``)
        so the caller knows exactly which ``<config-dir>/projects/**/<id>.jsonl``
        transcript to tail — the same trick claude-p uses for live logging.

        No idle watchdog here: a human is sitting at the terminal and
        long pauses are normal (they're reading, thinking, typing).
        """
        real_model, provider_env_vars, provider = self._resolve_provider()
        # include_disallowed=False: --disallowedTools is variadic and would
        # swallow the trailing positional prompt below — and interactive
        # sessions don't need the guard (a human drives, no orchestrator).
        cmd = ["claude", *self._build_flags(real_model, include_disallowed=False)]
        if session_id:
            cmd.extend(["--session-id", session_id])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(prompt)

        env = self._build_env(provider_env_vars) if provider_env_vars else None
        self._announce_model(real_model=real_model, provider=provider)
        return subprocess.run(cmd, cwd=cwd, env=env).returncode

    def _run_interactive_phase(
        self,
        prompt: str,
        *,
        cwd: Path,
        log_base: Path | None = None,
        extra_args: list[str] | None = None,
    ) -> bool:
        """Foreground, human-driven phase run (the ``interactive`` backend).

        Launches the real ``claude`` TUI (no ``-p``) and hands the terminal
        to the user, who drives the session themselves — the stable
        subscription fallback. There is no stream-json to parse, so we stamp
        a minimal ``session_start`` / ``session_end`` into ``{log_base}.jsonl``
        for the dashboard and iter meta, and report success on a clean exit.
        Real phase progress is judged downstream by the usual sorry-count /
        git-diff checks. The loop forces serial execution for this backend
        (one human, one terminal) — see ``commands/loop/entry.py``.
        """
        log.info(
            "Interactive backend: launching claude in the foreground. Drive "
            "the session yourself, then exit claude to let the loop continue."
        )
        # Pin the session id so we know exactly which transcript to tail —
        # the same approach claude-p uses (claude --session-id <uuid>, then
        # glob <config-dir>/projects/**/<uuid>.jsonl). Guessing by newest-file
        # / sanitized-cwd broke under a custom CLAUDE_CONFIG_DIR.
        session_id = str(uuid.uuid4())
        jsonl: str | None = None
        tailer: "_LiveClaudeSessionTailer | None" = None
        if log_base is not None:
            jsonl = f"{log_base}.jsonl"
            _emit_session_start(jsonl, model=self.model, role=self.role)
            _emit_session_meta(jsonl, session_id=session_id)
            _emit_prompt(jsonl, prompt=prompt)
            # Live-tail Claude Code's own session JSONL in the background so the
            # dashboard updates in real time — the same live-log experience the
            # claude-p backend gets, but for the foreground TUI (which streams
            # nothing to our stdout). Best-effort; never let it break the run.
            try:
                tailer = _LiveClaudeSessionTailer(session_id, jsonl, env=os.environ)
                tailer.start()
            except Exception:
                tailer = None

        code = self.run_interactive(
            prompt, cwd=cwd, extra_args=extra_args, session_id=session_id,
        )
        ok = code == 0
        if jsonl is not None:
            live_rows = 0
            locked = None
            if tailer is not None:
                try:
                    live_rows = tailer.stop()
                    locked = tailer.locked
                except Exception:
                    live_rows = 0
            # Fallback: if the live tailer never locked onto a session file
            # (e.g. it appeared only as claude exited), import it once now.
            if locked is None:
                try:
                    src = _find_claude_session_file(session_id, env=os.environ)
                    if src is not None:
                        live_rows += _import_claude_session_jsonl(src, jsonl)
                except Exception:
                    pass
            if live_rows:
                log.info(
                    f"Interactive backend: captured {live_rows} transcript "
                    f"event(s) from Claude Code's session log."
                )
            else:
                log.info(
                    "Interactive backend: no Claude Code session transcript "
                    "found; the phase log holds the run's start/end markers only."
                )
            _emit_interactive_session_end(
                jsonl,
                ok=ok,
                summary=(
                    "[interactive session ended]"
                    if ok else f"[interactive session exited with code {code}]"
                ),
            )
        return ok

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
        # Stop long blocking subagent dispatches from being auto-backgrounded
        # or killed at Claude Code's 2-min default / 10-min ceiling — see
        # BASH_FOREGROUND_TIMEOUT_MS. setdefault so an explicit override wins.
        env.setdefault("BASH_DEFAULT_TIMEOUT_MS", str(BASH_FOREGROUND_TIMEOUT_MS))
        env.setdefault("BASH_MAX_TIMEOUT_MS", str(BASH_FOREGROUND_TIMEOUT_MS))
        # Propagate this agent's claude backend to nested `archon subagent`
        # dispatches. The subagent wrapper shells out to `archon subagent`
        # WITHOUT a --claude-backend flag, so without this the child re-resolves
        # from config and silently falls back to `default` (plain `claude -p`)
        # even when the parent loop runs claude-p/vscode/desktop. setdefault so
        # a caller-supplied override always wins.
        from archon.commands.tooling.project_config import (
            CLAUDE_BACKEND_ENV, CLAUDE_P_CONFIG_DIR_ENV,
        )
        backend = getattr(self, "backend", None)
        backend_name = getattr(backend, "name", None)
        if backend_name:
            env.setdefault(CLAUDE_BACKEND_ENV, backend_name)
        config_dir = getattr(backend, "config_dir", None)
        if config_dir:
            env.setdefault(CLAUDE_P_CONFIG_DIR_ENV, str(config_dir))
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
            model=self.model,
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
            session_end_failure_kind,
            session_end_indicates_success,
        )

        if result.returncode == 0:
            return RunOutcome.SUCCESS
        session_end = read_last_session_end(jsonl)
        if session_end_indicates_success(session_end):
            return RunOutcome.SUCCESS
        # A negative returncode means the process was killed by a signal
        # (Popen reports -N; a shell wrapper would surface 128+N, e.g. 137 for
        # SIGKILL). That's an *external* kill — OOM, a parent/loop watchdog
        # cascade, or a manual stop — not the agent failing on its own. Call it
        # out so "exit 137 with no report" isn't mistaken for a crash.
        if result.returncode is not None and result.returncode < 0:
            sig_num = -result.returncode
            try:
                sig_name = signal.Signals(sig_num).name
            except ValueError:
                sig_name = f"signal {sig_num}"
            log.warn(
                f"agent killed by {sig_name} (exit {128 + sig_num}) on attempt "
                f"{attempt} with no success marker — an external kill (OOM, a "
                f"parent/loop watchdog cascade, or a manual stop), not a clean "
                f"failure. Check the .claude-p-raw.log / output files: the run "
                f"may have produced its result before the kill."
            )
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


_SPAWN_RETRIES = 5


def _spawn_agent_proc(cmd: list[str], **popen_kwargs) -> subprocess.Popen:
    """``subprocess.Popen`` that rides out a CLI auto-update binary swap.

    The Claude Code CLI updates itself in the background by replacing its
    launcher on disk. A spawn that lands in that brief window raises
    ``FileNotFoundError`` for ``claude`` even though it is installed and on
    ``PATH`` the whole time — and left unhandled it takes the entire loop
    down mid-run (issue #30). Retry a few times with a short exponential
    backoff so the swap completes and the spawn succeeds; a genuinely
    missing binary still surfaces the same error after ~4s.
    """
    delay = 0.25
    for attempt in range(_SPAWN_RETRIES):
        try:
            return subprocess.Popen(cmd, **popen_kwargs)
        except FileNotFoundError:
            if attempt == _SPAWN_RETRIES - 1:
                raise
            log.warn(
                f"spawn of {cmd[0]!r} failed with FileNotFoundError "
                f"(likely a CLI auto-update binary swap) — retrying in "
                f"{delay:.2f}s ({attempt + 1}/{_SPAWN_RETRIES - 1})"
            )
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")  # pragma: no cover


# ── streamed-run supervisor ───────────────────────────────────────────


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
            agent_proc = _spawn_agent_proc(
                agent_cmd,
                # stdin=DEVNULL is load-bearing for the codex runner: `codex
                # exec [PROMPT]` treats a piped (non-TTY) stdin as "read more
                # prompt and append it as a <stdin> block", printing "Reading
                # additional input from stdin..." and BLOCKING until EOF.
                # A supervised, headless run never feeds stdin, but without
                # this the child inherits the parent's stdin — an open pipe
                # that never closes — so codex hangs until the idle-watchdog
                # kills it (the subagent then reports "failed (0s)" / idle).
                # DEVNULL hands it immediate EOF; the positional prompt runs.
                # Harmless for claude -p, which doesn't read stdin either.
                stdin=subprocess.DEVNULL,
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
            agent_proc = _spawn_agent_proc(
                agent_cmd,
                stdin=subprocess.DEVNULL,  # see note above — codex blocks on inherited stdin
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
                        f"idle-timeout (watchdog kill, NOT a crash): the agent "
                        f"produced no JSONL output for {idle_timeout_s}s on "
                        f"attempt {attempt} — killing it. Under claude-p the "
                        f"structured stream can stall even while the run makes "
                        f"progress; check the .claude-p-raw.log for the live "
                        f"screen before treating this as a real failure."
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


def _claude_backend_from_name(name: str | None) -> ClaudeBackend:
    """Map a harness ``backend`` name to a :class:`ClaudeBackend` (layer-2).

    This is the per-harness override path: a ``harnesses.<name>.backend``
    value names which claude launch strategy to use for *that* role. It is
    intentionally cfg-free so it works inside the prover process-pool
    worker (which has only the picklable descriptor, no ``ProjectConfig``).
    The loop-wide default backend (``--claude-backend`` / a populated
    ``ProjectConfig``) is resolved by
    :func:`archon.commands.tooling.project_config.resolve_claude_backend`
    and passed into :func:`build_runner` as ``backend``; this helper only
    handles an explicit per-harness override.

    ``claude-p`` here uses ``CLAUDE_CONFIG_DIR`` from the environment for
    its config dir (the cfg-sourced ``loop.claude_p_config_dir`` only
    applies to the loop-wide default). Unknown names warn and fall back to
    the plain backend rather than failing a run over a launch-strategy
    typo.
    """
    key = (name or "default").strip().lower()
    if key in ("", "default"):
        return ClaudeBackend()
    if key == "claude-p":
        return ClaudePBackend()
    if key == "interactive":
        return InteractiveBackend()
    if key == "vscode":
        return EntrypointBackend("claude-vscode")
    if key == "desktop":
        return EntrypointBackend("claude-desktop")
    log.warn(
        f"Unknown harness backend {name!r}; valid values: default, "
        f"claude-p, interactive, vscode, desktop. Using 'default'."
    )
    return ClaudeBackend()


def build_runner(
    *,
    role: str,
    model: str = DEFAULT_MODEL,
    cfg: "ProjectConfig | None" = None,
    harness: str | None = None,
    descriptor: "HarnessDescriptor | None" = None,
    backend: "ClaudeBackend | None" = None,
) -> AgentRunner:
    """Build the :class:`AgentRunner` for a responsibility.

    The single decision point for routing a role to an engine. Registered
    runners: ``"claude-code"`` (the built-in :class:`ClaudeAgent`) and
    ``"codex"`` (:class:`~archon.agents.codex.CodexAgent`). An unknown
    runner raises :class:`UnknownHarnessError`.

    Resolution of *which* harness:

    * ``descriptor`` (if given) is used verbatim — callers that already
      resolved the picklable :class:`HarnessDescriptor` at the dispatch
      site (the prover pool / subagent / lane) pass it directly.
    * else ``harness`` (if given) is the harness name — resolved to a
      descriptor via ``cfg`` when present, else used as a bare name.
    * else ``cfg`` + ``role`` are resolved with
      :func:`resolve_role_harness` (``loop.roles.<role>`` >
      ``loop.harness`` > ``"claude-code"``).
    * with none of those, the harness is the built-in ``"claude-code"``.

    ``backend`` is the loop-wide :class:`ClaudeBackend` (the resolved
    ``--claude-backend`` / ``loop.claude_backend``), threaded through to
    the claude-code engine so the billing-change backends (claude-p,
    vscode, …) keep working under the router. A harness whose descriptor
    names its own ``backend`` overrides it for that role (layer-2);
    non-claude runners ignore ``backend`` entirely.

    Zero-regression invariant: when the resolved harness is
    ``"claude-code"`` AND no explicit ``harnesses."claude-code"`` entry is
    configured, this returns exactly ``ClaudeAgent(model=model,
    role=role, backend=backend or ClaudeBackend())`` — the same object the
    call site built before the router.

    Raises:
        UnknownHarnessError: the resolved harness names a runner that has
            no implementation in this version.
    """
    if descriptor is not None:
        return _runner_from_descriptor(
            descriptor, model=model, role=role, default_backend=backend,
        )

    # Resolve the harness name.
    if harness is None:
        if cfg is not None:
            from archon.commands.tooling.project_config import resolve_role_harness

            harness = resolve_role_harness(cfg, role, fallback=DEFAULT_HARNESS)
        else:
            harness = DEFAULT_HARNESS

    # Fast path / zero-regression short-circuit: the built-in claude-code
    # harness with no explicit descriptor override is the legacy agent,
    # untouched (modulo the loop-wide backend, which it has always carried).
    has_override = False
    if cfg is not None:
        from archon.commands.tooling.project_config import (
            has_explicit_harness_override,
        )

        has_override = has_explicit_harness_override(cfg, harness)

    if harness == DEFAULT_HARNESS and not has_override:
        return ClaudeAgent(
            model=model, role=role, backend=backend or ClaudeBackend(),
        )

    # A configured harness descriptor: load + dispatch on its runner.
    from archon.commands.tooling.project_config import load_harness_descriptor

    resolved = load_harness_descriptor(cfg, harness) if cfg is not None else None
    if resolved is None:
        # No cfg to load from, but a non-default harness name was passed
        # directly. Synthesize a minimal descriptor whose runner is the
        # name itself, so an unknown name still raises clearly.
        from archon.commands.tooling.project_config import HarnessDescriptor

        resolved = HarnessDescriptor(name=harness, runner=harness)
    return _runner_from_descriptor(
        resolved, model=model, role=role, default_backend=backend,
    )


def _runner_from_descriptor(
    descriptor: "HarnessDescriptor",
    *,
    model: str,
    role: str,
    default_backend: "ClaudeBackend | None" = None,
) -> AgentRunner:
    """Construct the engine runner named by a resolved descriptor.

    The single place that maps ``descriptor.runner`` → a concrete
    :class:`AgentRunner`. ``claude-code`` honours the descriptor's model
    override (else keeps ``model``) and its ``backend`` override (else the
    loop-wide ``default_backend``); ``codex`` is built straight from the
    descriptor (model / effort / sandbox / gateway all live there).
    """
    runner = descriptor.runner
    if runner == DEFAULT_HARNESS:
        if descriptor.model:
            model = descriptor.model
        if descriptor.backend:
            chosen = _claude_backend_from_name(descriptor.backend)
        else:
            chosen = default_backend or ClaudeBackend()
        return ClaudeAgent(model=model, role=role, backend=chosen)
    if runner == "codex":
        from archon.agents.codex import CodexAgent

        if not descriptor.model and model:
            import dataclasses
            descriptor = dataclasses.replace(descriptor, model=model)

        return CodexAgent(descriptor=descriptor, role=role)
    if runner == "antigravity":
        from archon.agents.antigravity import AntigravityAgent
        # Antigravity CLI model defaults to something internal but can be overridden
        # We assume the user configures the model via the dashboard or CLI flags.
        return AntigravityAgent(descriptor=descriptor, role=role)
        
    raise UnknownHarnessError(
        f"Harness {descriptor.name!r} requests runner {runner!r}, but the "
        f"runners available in this version of Archon are "
        f"{DEFAULT_HARNESS!r}, 'codex', and 'antigravity'. Remove the harness override or "
        f"set its runner to a supported value."
    )


__all__ = [
    "AgentRunner", "ClaudeAgent", "ClaudeBackend", "ClaudePBackend",
    "EntrypointBackend", "InteractiveBackend", "DEFAULT_HARNESS", "DEFAULT_MODEL",
    "DISALLOWED_NATIVE_TOOLS", "BASH_FOREGROUND_TIMEOUT_MS",
    "RunOutcome", "QuotaExhaustedError", "SupervisionResult",
    "UnknownHarnessError", "build_runner", "supervise_streamed_run",
]
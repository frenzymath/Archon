"""Codex CLI runner — routes a responsibility to OpenAI Codex.

``CodexAgent`` implements the same :class:`~archon.agent.AgentRunner`
protocol as :class:`~archon.agent.ClaudeAgent`, so any site that obtains
its runner from :func:`archon.agent.build_runner` can drive ``codex exec``
instead of ``claude`` purely by config — see the ``harnesses.<name>``
descriptor with ``runner: "codex"``.

Unlike a bolt-on, codex is a first-class engine here:

* **Logging parity.** Codex's native ``--json`` stream (the
  ``thread.started`` / ``turn.*`` / ``item.*`` event schema) is normalised
  on the fly — by the embedded :data:`_CODEX_STREAM_PARSER`, piped exactly
  like claude's ``stream-json`` parser via
  :func:`~archon.agent.supervise_streamed_run` — into Archon's own JSONL
  ``thinking`` / ``text`` / ``tool_call`` / ``tool_result`` / ``session_end``
  rows, so the dashboard shows a live transcript and the token panel.
  (Codex reports token usage but not a USD cost, so ``session_end``
  omits ``total_cost_usd``; the token counts are the real signal.)
* Shared subprocess supervision (cancel / idle-watchdog / ordered
  teardown) via :func:`~archon.agent.supervise_streamed_run`.

Current limits (see ``docs/MIGRATION.md``):

* No true resume. Codex mints its own ``thread_id`` and resumes via a
  separate subcommand; v1 logs a warning and runs fresh when a
  ``resume_session_id`` is passed.

The ``archon-lean-lsp`` MCP server can be wired in per-invocation via
``-c mcp_servers.*`` overrides (opt in with ``harnesses.<name>.mcp:
"lean-lsp"``) — see :meth:`build_argv`. Codex has no ``--mcp-config``
flag, so unlike the claude-code path (which registers the server at
``archon init`` time) the codex MCP wiring is entirely self-contained in
this runner's argv.

The invocation mirrors the FormalQualBench reference runner:
``codex exec --json --skip-git-repo-check --ignore-user-config
[-m <model>] [-c model_reasoning_effort="<effort>"] --sandbox <sandbox>
--ephemeral [gateway -c …] [mcp -c …] [extra] <prompt>``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from archon import log
from archon.agent import (
    RunOutcome,
    prepare_safe_tmp,
    safe_mode_enabled,
    supervise_streamed_run,
)
from archon.commands.tooling.project_config import HarnessDescriptor


# The custom-provider id injected via `-c model_provider=...`. The
# provider reads its key from CODEX_GATEWAY_API_KEY so the raw key never
# appears in argv / process listings.
_GATEWAY_PROVIDER = "harness-gateway"
_GATEWAY_KEY_ENV = "CODEX_GATEWAY_API_KEY"

# The MCP server Archon ships for Lean (same one the claude-code path
# registers at init via ``claude mcp add archon-lean-lsp``). For codex we
# render it as per-invocation ``-c mcp_servers.*`` overrides instead.
_LEAN_LSP_SERVER = "archon-lean-lsp"
_KNOWN_MCP_BUNDLES = frozenset({"lean-lsp"})
# required=true fails codex startup if the MCP can't initialize (rather
# than silently running a no-MCP session whose prompt claims the tools are
# loaded); tool_timeout_sec=600 covers a cold Mathlib LSP index.
_MCP_TOOL_TIMEOUT_SEC = 600

_PROMPT_VARIANTS_SUBDIR = "variants"


class UnknownMcpBundleError(ValueError):
    """A codex harness names an ``mcp`` bundle this runner doesn't know."""


class LeanLspMcpUnavailableError(RuntimeError):
    """The bundled ``lean-lsp-mcp`` dir can't be resolved on disk."""


class PartialGatewayConfigError(ValueError):
    """A codex harness gateway is half-configured (base URL XOR key set)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ensure_archon_on_path(env: dict[str, str]) -> None:
    """Make the ``archon`` CLI resolvable inside the codex subprocess.

    Codex runs subagent dispatch (``python3 .claude/tools/archon-subagent.py``)
    in its own shell, whose ``PATH`` may not include the venv ``bin/`` that
    holds the ``archon`` console script — the wrapper then aborts with "archon
    CLI not found on PATH" and no subagents are dispatched. The parent archon
    process always has it on PATH, so locate it here and prepend its directory
    to the child's ``PATH``. No-op when ``archon`` can't be located (the wrapper
    has its own ``python -m archon`` fallback for that case).
    """
    exe = shutil.which("archon")
    if not exe:
        return
    bin_dir = os.path.dirname(os.path.abspath(exe))
    path = env.get("PATH", "")
    parts = path.split(os.pathsep) if path else []
    if bin_dir not in parts:
        env["PATH"] = os.pathsep.join([bin_dir, *parts]) if parts else bin_dir


def _prepend_to_path(env: dict[str, str], directory: str) -> None:
    """Prepend ``directory`` to ``env['PATH']`` (idempotent, no-op if empty)."""
    if not directory:
        return
    path = env.get("PATH", "")
    parts = path.split(os.pathsep) if path else []
    if directory not in parts:
        env["PATH"] = os.pathsep.join([directory, *parts]) if parts else directory


def _ensure_codex_runtime_on_path(env: dict[str, str], codex_bin: str) -> None:
    """Put the ``codex`` launcher's own directory on the child ``PATH``.

    The ``codex`` CLI is a **Node script** (``#!/usr/bin/env node``). When a
    nested subagent dispatch spawns codex from inside a *parent* codex's
    ``bash -lc`` sandbox, ``PATH`` has been reset to the sandbox profile and
    no longer contains the nvm ``bin/`` that holds ``node`` — so the OS
    shebang lookup fails and codex dies instantly at exit 127
    (``/usr/bin/env: 'node': No such file or directory``) with zero JSONL
    output. The symptom is an inner codex run that "fails in 0s": it never
    even reached ``thread.started``.

    ``node`` lives in the same directory as the resolved ``codex`` binary
    (both under the nvm/npm bin), so prepending that directory makes the
    launcher's interpreter discoverable. No-op for a bare ``"codex"`` (not an
    absolute path → nothing reliable to derive a directory from).
    """
    if not codex_bin or codex_bin == "codex":
        return
    abs_bin = os.path.abspath(codex_bin)
    if os.path.sep in codex_bin or os.path.isabs(abs_bin):
        _prepend_to_path(env, os.path.dirname(abs_bin))


def _stamp_archon_cli(env: dict[str, str]) -> None:
    """Stamp PATH-independent archon-CLI handles into the codex child env.

    :func:`_ensure_archon_on_path` prepends the venv ``bin/`` to ``PATH``,
    but that is not enough inside the codex sandbox: codex runs each tool
    command in a **login shell** (``bash -lc``), which re-derives ``PATH``
    from the user profile and discards whatever we prepended — so by the
    time ``python3 .claude/tools/archon-subagent.py`` runs, neither
    ``archon`` (console script) nor the venv ``python`` is on ``PATH`` and
    the wrapper aborts with "archon CLI not found". Non-``PATH`` env vars,
    by contrast, *do* survive into that login shell (same mechanism the
    ``ARCHON_CODEX_BIN`` / ``ARCHON_UV_BIN`` stamps already rely on).

    So we hand the wrapper absolute handles it can use without ``PATH``:

    * ``ARCHON_PYTHON`` — this process's interpreter (``sys.executable``),
      which by construction can import ``archon`` (we are running under it).
      The wrapper invokes ``<ARCHON_PYTHON> -m archon``.
    * ``ARCHON_CLI_BIN`` — the ``archon`` console script's absolute path,
      when locatable. Preferred by the wrapper as a single executable.

    ``setdefault`` so a nested dispatch inherits the value a parent codex
    run already stamped rather than re-resolving it.
    """
    env.setdefault("ARCHON_PYTHON", os.path.abspath(sys.executable))
    exe = shutil.which("archon")
    if exe:
        env.setdefault("ARCHON_CLI_BIN", os.path.abspath(exe))


def _resolve_external_bin(
    name: str, *, override: object, env_var: str, env: dict[str, str],
) -> str:
    """Resolve an external executable to an absolute path, PATH-independent.

    Resolution order:

    1. an explicit ``override`` path (from the harness descriptor's ``raw``
       config — the deterministic escape hatch).
    2. ``env_var`` in ``env`` — stamped by a parent codex run's
       :meth:`CodexAgent.build_env` so nested dispatches inherit the path.
    3. ``name`` on ``PATH``.
    4. the bare ``name`` (last resort).

    Why this matters for subagents: a codex agent dispatches a subagent as
    ``python3 .claude/tools/archon-subagent.py`` → ``archon subagent`` →
    a fresh ``codex exec`` (which in turn spawns its MCP servers, e.g.
    ``uv``). Codex runs the dispatch in a shell whose ``PATH`` is
    sanitized — it can omit the venv ``bin/`` (so the wrapper falls back to
    ``python -m archon``) and the directories holding ``codex`` / ``uv``.
    A bare name then raises ``FileNotFoundError`` in the nested process.
    Resolving to an absolute path here — and propagating it via ``env_var``
    — makes the spawn independent of the child shell's ``PATH``.
    """
    if isinstance(override, str) and override:
        return override
    from_env = env.get(env_var)
    if from_env:
        return from_env
    return shutil.which(name) or name


def _resolve_codex_bin(descriptor: "HarnessDescriptor", env: dict[str, str]) -> str:
    """Resolve the ``codex`` executable for ``argv[0]``. See _resolve_external_bin."""
    return _resolve_external_bin(
        "codex", override=descriptor.raw.get("bin"),
        env_var="ARCHON_CODEX_BIN", env=env,
    )


def _resolve_uv_bin(descriptor: "HarnessDescriptor", env: dict[str, str]) -> str:
    """Resolve the ``uv`` launcher used for the lean-lsp MCP server command.

    Same rationale as :func:`_resolve_codex_bin`: the MCP server is
    ``required=True``, so if a subagent's nested codex can't find ``uv`` on
    its sanitized env the whole codex session aborts at startup. Override
    key is ``uv_bin`` in the harness ``raw`` config.
    """
    return _resolve_external_bin(
        "uv", override=descriptor.raw.get("uv_bin"),
        env_var="ARCHON_UV_BIN", env=env,
    )


def resolve_prompt_variant(
    variant: str, *, project_path: Path | None = None,
) -> str | None:
    """Resolve a prompt-variant name to its text (project overrides bundled).

    A project-local copy under
    ``<project>/.archon/prompts/variants/<name>.md`` wins over the bundled
    package data at ``.archon-src/prompts/variants/<name>.md``. Returns the
    file's text, or ``None`` when neither location has it (the caller then
    warns and proceeds with the unmodified prompt).
    """
    from archon.commands.loop.utils import data_path

    rel = f"{_PROMPT_VARIANTS_SUBDIR}/{variant}.md"

    if project_path is not None:
        local = (
            Path(project_path) / ".archon" / "prompts" / _PROMPT_VARIANTS_SUBDIR
            / f"{variant}.md"
        )
        if local.is_file():
            try:
                return local.read_text(encoding="utf-8")
            except OSError:
                pass  # fall through to bundled

    bundled = Path(data_path(f"prompts/{rel}"))
    if bundled.is_file():
        try:
            return bundled.read_text(encoding="utf-8")
        except OSError:
            return None
    return None


# ── codex --json → archon JSONL parser ────────────────────────────────
#
# Embedded Python script (spawned as ``python -c``) that consumes codex's
# native ``exec --json`` event stream on stdin and writes Archon's
# normalized JSONL — the SAME row schema the dashboard reads from the
# claude ``stream-json`` parser (``thinking`` / ``text`` / ``tool_call`` /
# ``tool_result`` / ``session_meta`` / ``session_end``). Kept inline so the
# module stays self-contained.
#
# codex 0.136 event schema (verified against the CLI):
#   {"type":"thread.started","thread_id":"…"}
#   {"type":"turn.started"}
#   {"type":"item.started"|"item.completed", "item":{"type":…, …}}
#       item.type ∈ agent_message{text} | reasoning{text}
#                 | command_execution{command,aggregated_output,exit_code,status}
#                 | file_change{changes:[{path,kind}],status}
#                 | mcp_tool_call{server,tool,status,…} | web_search{query}
#                 | todo_list | error{message}
#   {"type":"turn.completed","usage":{input_tokens,cached_input_tokens,
#                                     output_tokens,reasoning_output_tokens}}
#   {"type":"turn.failed","error":{"message":…}}
#   {"type":"error","message":…}
#
# A single session_end is emitted once stdin closes. Usage is summed
# across turn.completed events, while num_turns reports completed Codex
# items/work steps because one codex exec is otherwise always one turn.
# Codex reports no USD cost, so total_cost_usd is omitted.
_CODEX_STREAM_PARSER = r'''
import sys, json, datetime

VERBOSE = '{verbose}' == 'True'
RAW = open('{raw_log}', 'a') if VERBOSE else None
JSONL = open('{jsonl}', 'a')
MODEL = '{model}'

def emit(event_type, **fields):
    row = {{'ts': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), 'event': event_type, **fields}}
    JSONL.write(json.dumps(row) + '\n')
    JSONL.flush()

# Mirror of archon.state.cost.estimate_cost_usd (canonical). Inlined because
# this parser is a self-contained `python -c` script and must not import archon.
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
    elif "deepseek-reasoner" in m or "deepseek-r1" in m:
        return (input_tokens * 0.55 + output_tokens * 2.19) / 1_000_000.0
    elif "deepseek-chat" in m or "deepseek-v3" in m or "deepseek" in m:
        return (input_tokens * 0.14 + output_tokens * 0.28) / 1_000_000.0
    return (input_tokens * 2.50 + output_tokens * 10.00) / 1_000_000.0

def terminal(s):
    print(s, flush=True)

_TOOL_ITEMS = ('command_execution', 'mcp_tool_call', 'file_change', 'todo_list')
_TOOL_LABEL = {{'command_execution': 'Bash', 'file_change': 'Edit', 'todo_list': 'TodoWrite'}}

def tool_label(item):
    if item.get('type') == 'mcp_tool_call':
        return item.get('tool') or 'mcp'
    return _TOOL_LABEL.get(item.get('type', ''), item.get('type', '') or 'tool')

def tool_input(item):
    t = item.get('type', '')
    if t == 'command_execution':
        return {{'command': item.get('command', '')}}
    if t == 'mcp_tool_call':
        return {{
            'server': item.get('server', ''),
            'tool': item.get('tool', ''),
            'arguments': item.get('arguments', {{}}),
        }}
    if t == 'file_change':
        return {{'changes': item.get('changes', [])}}
    if t == 'todo_list':
        return {{'items': item.get('items', [])}}
    return {{}}

def tool_result_content(item):
    t = item.get('type', '')
    if t == 'command_execution':
        out = item.get('aggregated_output') or item.get('output') or ''
        code = item.get('exit_code')
        if code is not None:
            out = (out + '\n' if out else '') + '[exit ' + str(code) + ']'
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
    if t == 'mcp_tool_call':
        err = item.get('error')
        if err:
            return '[error] ' + (err if isinstance(err, str) else json.dumps(err, ensure_ascii=False))
        res = item.get('result')
        if res is None:
            return str(item.get('status', '') or '')
        return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)
    if t == 'file_change':
        lines = []
        for ch in item.get('changes', []) or []:
            if isinstance(ch, dict):
                lines.append(str(ch.get('kind', '?')) + ' ' + str(ch.get('path', '')))
        status = item.get('status', '')
        if status and not lines:
            lines.append(str(status))
        return '\n'.join(lines)
    return ''

session_id_emitted = False
started_ids = set()
last_message = ''
sum_input = sum_cached = sum_output = sum_reasoning = 0
num_iters = 0

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

    if t == 'thread.started':
        if not session_id_emitted:
            sid = obj.get('thread_id', '') or ''
            if sid:
                emit('session_meta', session_id=sid)
                session_id_emitted = True

    elif t == 'item.started':
        item = obj.get('item', {{}}) or {{}}
        if item.get('type', '') in _TOOL_ITEMS:
            started_ids.add(item.get('id', ''))
            emit('tool_call', tool=tool_label(item), input=tool_input(item))

    elif t == 'item.completed':
        num_iters += 1
        item = obj.get('item', {{}}) or {{}}
        it = item.get('type', '')
        if it == 'agent_message':
            text = (item.get('text', '') or '').strip()
            if text:
                emit('text', content=text)
                last_message = text
        elif it == 'reasoning':
            text = (item.get('text', '') or '').strip()
            if text:
                emit('thinking', content=text)
        elif it in _TOOL_ITEMS:
            if item.get('id', '') not in started_ids:
                emit('tool_call', tool=tool_label(item), input=tool_input(item))
            if it != 'todo_list':
                emit('tool_result', content=tool_result_content(item))
        elif it == 'error':
            msg = str(item.get('message', ''))
            if msg:
                last_message = '[error] ' + msg
                emit('text', content=last_message)

    elif t == 'turn.completed':
        usage = obj.get('usage', {{}}) or {{}}
        t_in = usage.get('input_tokens', 0) or 0
        t_cached = usage.get('cached_input_tokens', 0) or 0
        t_out = usage.get('output_tokens', 0) or 0
        t_reasoning = usage.get('reasoning_output_tokens', 0) or 0

        sum_input += t_in
        sum_cached += t_cached
        sum_output += t_out
        sum_reasoning += t_reasoning

        t_cost = estimate_cost_usd(MODEL, t_in, t_out, t_cached)
        emit('turn_usage',
             input_tokens=t_in,
             output_tokens=t_out,
             cache_read_input_tokens=t_cached,
             cost_usd=t_cost)

    elif t == 'turn.failed':
        err = obj.get('error')
        if err:
            last_message = '[turn.failed] ' + (err if isinstance(err, str) else json.dumps(err, ensure_ascii=False))

    elif t == 'error':
        msg = str(obj.get('message', ''))
        if msg:
            last_message = '[error] ' + msg
            emit('text', content=last_message)

fresh_input = sum_input - sum_cached
if fresh_input < 0:
    fresh_input = 0
emit('session_end',
    num_turns=num_iters,
    num_items=num_iters,
    input_tokens=fresh_input,
    output_tokens=sum_output,
    cache_read_input_tokens=sum_cached,
    cache_creation_input_tokens=0,
    reasoning_output_tokens=sum_reasoning,
    input_tokens_total=sum_input,
    model_usage={{MODEL: {{'inputTokens': fresh_input, 'outputTokens': sum_output}}}},
    summary=last_message,
    runner='codex',
)

if last_message:
    terminal(last_message)
parts = []
if fresh_input or sum_output:
    parts.append('in=' + str(fresh_input) + ' cached=' + str(sum_cached) + ' out=' + str(sum_output) + ' reasoning=' + str(sum_reasoning))
if num_iters:
    parts.append('iters=' + str(num_iters))
if parts:
    terminal('[TOKENS] ' + ' | '.join(parts))

JSONL.close()
if RAW:
    RAW.close()
'''


@dataclass
class CodexAgent:
    """One ``codex exec`` invocation, configured from a harness descriptor.

    Attributes:
        descriptor: the resolved :class:`HarnessDescriptor` (runner
            ``"codex"``). Carries model / effort / sandbox / gateway env
            var names; threaded picklably into the prover pool so a worker
            rebuilds an identical agent.
        role: optional phase tag (``prover`` / a subagent name) stamped
            into the log's session_start row, like ClaudeAgent.
    """

    descriptor: HarnessDescriptor
    role: str | None = None

    # ── command assembly (pure; unit-tested without spawning codex) ──────

    @property
    def model(self) -> str | None:
        """Explicit codex model id, or ``None`` to use the CLI default."""
        return self.descriptor.model

    @property
    def log_model(self) -> str:
        return self.descriptor.model or "codex-default"

    @property
    def effort(self) -> str | None:
        return self.descriptor.effort

    @property
    def sandbox(self) -> str:
        if safe_mode_enabled():
            return "workspace-write"
        return self.descriptor.sandbox or "danger-full-access"

    def _gateway_creds(
        self, env_source: dict[str, str] | None = None,
    ) -> tuple[str | None, str | None]:
        """Resolve (base_url, api_key) from the descriptor's env var names.

        The descriptor names *which* env vars hold the gateway base URL and
        key (e.g. ``CODEX_BASE_URL`` / ``CZ_API_KEY``); the values come from
        the process environment. Returns ``(None, None)`` when the
        descriptor does not configure a gateway → codex uses its native
        ``~/.codex`` login.

        Fails loud on a partial gateway configuration (base URL present but
        key absent, or vice-versa) rather than silently falling back to
        native login (which would route to the wrong provider).
        """
        src = env_source if env_source is not None else os.environ
        d = self.descriptor
        if not d.base_url_env:
            return None, None

        base_url = src.get(d.base_url_env) or None
        key = src.get(d.key_env) if d.key_env else None
        key = key or None

        if base_url and not key:
            missing = d.key_env or "key_env"
            raise PartialGatewayConfigError(
                f"codex harness {d.name!r}: {d.base_url_env} is set but the "
                f"gateway key env {missing!r} is missing/empty — refusing to "
                f"fall back to native ~/.codex login (would route to the "
                f"wrong provider). Set {missing} or unset {d.base_url_env}."
            )
        if key and not base_url:
            raise PartialGatewayConfigError(
                f"codex harness {d.name!r}: gateway key env "
                f"{d.key_env!r} is set but {d.base_url_env} is missing/empty "
                f"— refusing to half-apply the gateway. Set {d.base_url_env} "
                f"or unset {d.key_env}."
            )
        return base_url, key

    def build_argv(
        self,
        prompt: str,
        *,
        last_message_path: Path | None = None,
        extra_args: list[str] | None = None,
        env_source: dict[str, str] | None = None,
        lake_root: Path | str | None = None,
    ) -> list[str]:
        """Build the full ``codex exec`` argv (no subprocess spawned).

        ``codex exec --json --skip-git-repo-check --ignore-user-config
          [-m <model>] [-c model_reasoning_effort="<effort>"]
          [-o <last_message_path>] [gateway -c …] [mcp -c …]
          --sandbox <sandbox> --ephemeral [extra] <prompt>``.

        Gateway ``-c`` flags are appended only when both ``base_url_env``
        and ``key_env`` resolve to set values; a *partial* gateway config
        raises :class:`PartialGatewayConfigError`. MCP ``-c mcp_servers.*``
        flags are appended only when the descriptor opts in
        (``descriptor.mcp``). The prompt is always the final positional arg.
        """
        argv: list[str] = [
            _resolve_codex_bin(self.descriptor, env_source or os.environ),
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--ignore-user-config",
        ]
        if self.model:
            argv += ["-m", self.model]
        if self.effort:
            argv += ["-c", f'model_reasoning_effort="{self.effort}"']
        if last_message_path is not None:
            argv += ["-o", str(last_message_path)]

        base_url, api_key = self._gateway_creds(env_source)
        if base_url and api_key:
            # Inject a custom provider; the key is read from
            # CODEX_GATEWAY_API_KEY in the child env, never on the cmd line.
            argv += [
                "-c", f'model_provider="{_GATEWAY_PROVIDER}"',
                "-c", f'model_providers.{_GATEWAY_PROVIDER}.name="{_GATEWAY_PROVIDER}"',
                "-c", f'model_providers.{_GATEWAY_PROVIDER}.base_url="{base_url}"',
                "-c", f'model_providers.{_GATEWAY_PROVIDER}.env_key="{_GATEWAY_KEY_ENV}"',
                "-c", f'model_providers.{_GATEWAY_PROVIDER}.wire_api="{self.descriptor.wire_api}"',
                "-c", f"model_providers.{_GATEWAY_PROVIDER}.supports_websockets=false",
            ]

        argv += self._mcp_overrides(lake_root, env_source or os.environ)
        argv += ["--sandbox", self.sandbox, "--ephemeral"]
        if safe_mode_enabled():
            # Never wait for an unavailable interactive approval in a
            # headless safe run. Operations outside workspace-write fail and
            # are returned to the model as command errors.
            argv += ["-c", 'approval_policy="never"']
            argv += [
                "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
                "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                "-c", "sandbox_workspace_write.network_access=true",
            ]
        argv += self._descriptor_extra_args()
        if extra_args:
            argv += list(extra_args)
        argv.append(prompt)
        return argv

    def build_interactive_argv(
        self,
        prompt: str,
        *,
        extra_args: list[str] | None = None,
        env_source: dict[str, str] | None = None,
        lake_root: Path | str | None = None,
    ) -> list[str]:
        """Build a foreground Codex CLI invocation for interactive roles.

        Unlike :meth:`build_argv`, this uses the native TUI entry point
        (``codex`` rather than ``codex exec --json``). It still applies the
        harness descriptor's optional model, effort, sandbox, gateway, MCP,
        and extra ``-c`` overrides consistently across headless and foreground
        commands.
        """
        argv: list[str] = [_resolve_codex_bin(self.descriptor, env_source or os.environ)]
        if self.model:
            argv += ["-m", self.model]
        if self.effort:
            argv += ["-c", f'model_reasoning_effort="{self.effort}"']

        base_url, api_key = self._gateway_creds(env_source)
        if base_url and api_key:
            argv += [
                "-c", f'model_provider="{_GATEWAY_PROVIDER}"',
                "-c", f'model_providers.{_GATEWAY_PROVIDER}.name="{_GATEWAY_PROVIDER}"',
                "-c", f'model_providers.{_GATEWAY_PROVIDER}.base_url="{base_url}"',
                "-c", f'model_providers.{_GATEWAY_PROVIDER}.env_key="{_GATEWAY_KEY_ENV}"',
                "-c", f'model_providers.{_GATEWAY_PROVIDER}.wire_api="{self.descriptor.wire_api}"',
                "-c", f"model_providers.{_GATEWAY_PROVIDER}.supports_websockets=false",
            ]

        argv += self._mcp_overrides(lake_root, env_source or os.environ)
        argv += ["--sandbox", self.sandbox]
        if safe_mode_enabled():
            argv += ["-c", 'approval_policy="never"']
            argv += [
                "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
                "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                "-c", "sandbox_workspace_write.network_access=true",
            ]
        argv += self._descriptor_extra_args()
        if extra_args:
            argv += list(extra_args)
        argv.append(prompt)
        return argv

    def _descriptor_extra_args(self) -> list[str]:
        raw_extra = self.descriptor.raw.get("extra_args")
        if isinstance(raw_extra, list):
            return [str(x) for x in raw_extra]
        if isinstance(raw_extra, str) and raw_extra.strip():
            return raw_extra.split()
        return []

    def _mcp_overrides(
        self, lake_root: Path | str | None, env: dict[str, str],
    ) -> list[str]:
        """Render ``-c mcp_servers.*`` flags for the descriptor's MCP bundles.

        Returns ``[]`` when ``descriptor.mcp`` is empty (the default → no
        MCP). Today the only known bundle is ``"lean-lsp"``. ``env`` is the
        resolved child env, used to resolve the MCP launcher (``uv``) to an
        absolute path and to pass a PATH through to the server.

        Raises:
            UnknownMcpBundleError: a configured bundle name isn't known.
            LeanLspMcpUnavailableError: the bundled ``lean-lsp-mcp`` dir
                can't be resolved on disk (fail-closed).
        """
        bundles = self.descriptor.mcp
        if not bundles:
            return []

        out: list[str] = []
        for bundle in bundles:
            if bundle == "lean-lsp":
                out += self._lean_lsp_overrides(lake_root, env)
            else:
                raise UnknownMcpBundleError(
                    f"codex harness {self.descriptor.name!r}: unknown MCP "
                    f"bundle {bundle!r} (known: "
                    f"{', '.join(sorted(_KNOWN_MCP_BUNDLES))})."
                )
        return out

    @staticmethod
    def _lean_lsp_mcp_dir() -> Path:
        """Resolve the bundled ``lean-lsp-mcp`` dir via Archon's data_path.

        Same source of truth as the claude-code init step. Raises
        :class:`LeanLspMcpUnavailableError` when the dir is missing.
        """
        from archon.commands.init.utils import data_path

        lean_lsp_dir = Path(data_path("tools/lean-lsp-mcp"))
        if not lean_lsp_dir.is_dir():
            raise LeanLspMcpUnavailableError(
                f"codex harness mcp 'lean-lsp': the bundled lean-lsp-mcp "
                f"directory does not exist at {lean_lsp_dir} — refusing to "
                f"render an archon-lean-lsp server pointed at a missing "
                f"path. Reinstall Archon's data tree or unset the harness's "
                f"`mcp` field."
            )
        return lean_lsp_dir

    def _lean_lsp_overrides(
        self, lake_root: Path | str | None, env: dict[str, str],
    ) -> list[str]:
        """The ``-c`` overrides for the ``archon-lean-lsp`` server.

        The launcher is resolved to an absolute ``uv`` path (the server is
        ``required=True``, so a missing ``uv`` aborts the whole codex
        session — see :func:`_resolve_uv_bin`). The child env's ``PATH`` is
        forwarded to the server so the Lean toolchain it spawns
        (``lake`` / ``lean``) is discoverable.
        """
        lean_lsp_dir = self._lean_lsp_mcp_dir()
        root = Path(lake_root) if lake_root is not None else Path.cwd()
        base = f"mcp_servers.{_LEAN_LSP_SERVER}"

        def flag(key: str, value: object) -> list[str]:
            return ["-c", f"{key}={json.dumps(value)}"]

        out: list[str] = []
        out += flag(f"{base}.command", _resolve_uv_bin(self.descriptor, env))
        out += flag(
            f"{base}.args",
            ["run", "--directory", str(lean_lsp_dir), "lean-lsp-mcp"],
        )
        out += flag(f"{base}.env.LEAN_PROJECT_PATH", str(root))
        path = env.get("PATH")
        if path:
            out += flag(f"{base}.env.PATH", path)
        out += flag(f"{base}.required", True)
        out += flag(f"{base}.tool_timeout_sec", _MCP_TOOL_TIMEOUT_SEC)
        return out

    def _apply_prompt_variant(self, prompt: str, *, project_path: Path) -> str:
        """Append the descriptor's prompt-variant tail to ``prompt`` (if any).

        The loop's evolving prover prompt stays the single source of truth;
        the variant is a codex-specific tail (CLI orientation + faithfulness
        rules), not a fork. A missing variant file is non-fatal: warn and
        return the prompt unchanged.
        """
        variant = self.descriptor.prompt_variant
        if not variant:
            return prompt
        tail = resolve_prompt_variant(variant, project_path=project_path)
        if tail is None:
            log.warn(
                f"codex harness {self.descriptor.name!r}: prompt_variant "
                f"{variant!r} not found at "
                f".archon/prompts/variants/{variant}.md (project) or the "
                f"bundled variants dir — running with the unmodified prompt."
            )
            return prompt
        tail = tail.strip("\n")
        if not tail:
            return prompt
        return f"{prompt}\n\n{tail}\n"

    def build_env(
        self, env_overrides: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build the child env: ``os.environ`` + gateway key + overrides.

        Caller-supplied ``env_overrides`` are merged first (and win on
        conflict); the gateway API key is then resolved from that merged env
        and copied into ``CODEX_GATEWAY_API_KEY`` so the injected provider
        can read it — keeping the secret out of argv and keeping build_env /
        build_argv consistent on a single env snapshot.
        """
        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)
        _ensure_archon_on_path(env)
        _stamp_archon_cli(env)
        # Propagate the resolved codex/uv paths so a nested subagent dispatch
        # (archon subagent → codex exec → its uv-launched MCP) can spawn them
        # even though codex's exec_command sanitizes PATH. See
        # _resolve_external_bin.
        codex_bin = _resolve_codex_bin(self.descriptor, env)
        if codex_bin != "codex":
            env["ARCHON_CODEX_BIN"] = codex_bin
            # codex is a Node script: a nested dispatch inside a parent
            # codex's PATH-reset sandbox can't find `node` (sibling of the
            # codex binary) and the launcher dies at 127 with no output.
            _ensure_codex_runtime_on_path(env, codex_bin)
        uv_bin = _resolve_uv_bin(self.descriptor, env)
        if uv_bin != "uv":
            env["ARCHON_UV_BIN"] = uv_bin
        base_url, api_key = self._gateway_creds(env)
        if base_url and api_key:
            env[_GATEWAY_KEY_ENV] = api_key
        return env

    # ── invocation modes ────────────────────────────────────────────────

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
        """Headless ``codex exec`` run. Returns True iff codex exited 0.

        Reuses the shared subprocess supervisor
        (:func:`archon.agent.supervise_streamed_run`) for cancel /
        idle-watchdog / ordered teardown, identical to ClaudeAgent. Codex's
        native ``--json`` stream is normalised by :data:`_CODEX_STREAM_PARSER`
        into Archon's JSONL schema (live transcript + tokens). On idle-kill
        the same prompt is re-run up to ``max_attempts`` times; a real
        non-zero exit returns False immediately (no retry).
        """
        if resume_session_id:
            log.warn(
                "codex harness: resume_session_id is not supported in v1; "
                "running a fresh codex session instead."
            )

        prompt = self._apply_prompt_variant(prompt, project_path=cwd)
        env = self.build_env(env_overrides)
        prepare_safe_tmp(cwd, env)
        self._announce()

        if log_base is None:
            # No log file → no watchdog. Stream codex JSON to devnull.
            import subprocess

            argv = self.build_argv(
                prompt, extra_args=extra_args, env_source=env, lake_root=cwd,
            )
            return subprocess.run(argv, cwd=cwd, env=env).returncode == 0

        if max_attempts < 1:
            max_attempts = 1

        for attempt in range(1, max_attempts + 1):
            outcome = self._run_with_logging(
                prompt,
                cwd=cwd,
                log_base=log_base,
                verbose_logs=verbose_logs,
                extra_args=extra_args,
                env=env,
                cancel_event=cancel_event,
                idle_timeout_s=idle_timeout_s,
                attempt=attempt,
            )
            if outcome is RunOutcome.SUCCESS:
                return True
            if outcome is RunOutcome.CANCELLED:
                return False
            if outcome is RunOutcome.FAILED:
                return False
            # IDLE_TIMEOUT: provider went silent; retry unless cancelled
            # or out of attempts.
            if cancel_event is not None and cancel_event.is_set():
                return False
            if attempt < max_attempts:
                log.warn(
                    f"codex run idle for {idle_timeout_s}s on attempt "
                    f"{attempt}/{max_attempts}; restarting same prompt."
                )
            else:
                log.error(
                    f"codex run still idle after {max_attempts} attempts; "
                    f"giving up."
                )
        return False

    def run_interactive(
        self,
        prompt: str,
        *,
        cwd: Path,
        extra_args: list[str] | None = None,
    ) -> int:
        """Foreground Codex TUI run for interactive Archon commands.

        This intentionally does not normalize logs: interactive Codex owns
        the terminal until the user exits. Headless Archon loop phases should
        continue to use :meth:`run`, which is supervised and logged.
        """
        import subprocess

        prompt = self._apply_prompt_variant(prompt, project_path=cwd)
        env = self.build_env()
        prepare_safe_tmp(cwd, env)
        self._announce()
        argv = self.build_interactive_argv(
            prompt, extra_args=extra_args, env_source=env, lake_root=cwd,
        )
        return subprocess.run(argv, cwd=cwd, env=env).returncode

    # ── internals ────────────────────────────────────────────────────────

    def _announce(self) -> None:
        role_suffix = f" ({self.role})" if self.role else ""
        base_url, _ = self._gateway_creds()
        via = " via gateway" if base_url else " via native login"
        log.info(f"Agent model: {self.model} [codex]{via}{role_suffix}")

    def _emit_session_start(self, jsonl: str, *, attempt: int) -> None:
        """Stamp a session_start so the log records the model/role/runner.

        Consistent with ClaudeAgent's header (so a log viewer reads the
        model); the codex parser then appends the normalised event rows.
        """
        Path(jsonl).parent.mkdir(parents=True, exist_ok=True)
        row: dict[str, object] = {
            "ts": _now_iso(),
            "event": "session_start",
            "model": self.model,
            "runner": "codex",
        }
        if self.role:
            row["role"] = self.role
        if attempt > 1:
            row["attempt"] = attempt
        try:
            with open(jsonl, "a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass

    def _emit_idle_timeout(self, jsonl: str, idle_s: float, attempt: int) -> None:
        row = {
            "ts": _now_iso(),
            "event": "idle_timeout",
            "idle_threshold_s": idle_s,
            "attempt": attempt,
        }
        try:
            with open(jsonl, "a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass

    def _run_with_logging(
        self,
        prompt: str,
        *,
        cwd: Path,
        log_base: Path,
        verbose_logs: bool,
        extra_args: list[str] | None,
        env: dict[str, str],
        cancel_event: "threading.Event | None",
        idle_timeout_s: float | None,
        attempt: int,
    ) -> RunOutcome:
        """Run codex once with normalised logging + the shared watchdog."""
        import sys

        log_base.parent.mkdir(parents=True, exist_ok=True)
        jsonl = f"{log_base}.jsonl"
        jsonl_path = Path(jsonl)
        raw_log = f"{log_base}.codex-raw.jsonl"
        last_message = log_base.parent / f"{log_base.name}.last_message.txt"

        # Header row first, then codex's normalised stream is appended by
        # the parser to the same file.
        self._emit_session_start(jsonl, attempt=attempt)

        argv = self.build_argv(
            prompt,
            last_message_path=last_message,
            extra_args=extra_args,
            env_source=env,
            lake_root=cwd,
        )

        parser_script = _CODEX_STREAM_PARSER.format(
            verbose=str(verbose_logs),
            raw_log=raw_log,
            jsonl=jsonl,
            model=self.model,
        )
        # codex's human-readable logs go to stderr; keep them only when
        # verbose (mirrors ClaudeAgent), separate from the codex JSON which
        # is piped through the parser on stdout.
        stderr_dest = f"{log_base}.raw.log" if verbose_logs else os.devnull

        result = supervise_streamed_run(
            argv,
            cwd=cwd,
            env=env,
            jsonl_path=jsonl_path,
            parser_cmd=[sys.executable, "-u", "-c", parser_script],
            stderr_dest=stderr_dest,
            cancel_event=cancel_event,
            idle_timeout_s=idle_timeout_s,
            attempt=attempt,
            role=self.role,
            on_idle_timeout=lambda idle_s, att: self._emit_idle_timeout(
                jsonl, idle_s, att
            ),
        )

        if result.cancelled:
            return RunOutcome.CANCELLED
        if result.idle_timeout_hit:
            return RunOutcome.IDLE_TIMEOUT
        if result.returncode == 0:
            return RunOutcome.SUCCESS
        return RunOutcome.FAILED


__all__ = [
    "CodexAgent",
    "PartialGatewayConfigError",
    "UnknownMcpBundleError",
    "LeanLspMcpUnavailableError",
    "resolve_prompt_variant",
]

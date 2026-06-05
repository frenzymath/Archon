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
  (Codex reports token usage but not a USD cost, so ``total_cost_usd`` is
  emitted as ``0``; the token counts are the real signal.)
* Shared subprocess supervision (cancel / idle-watchdog / ordered
  teardown) via :func:`~archon.agent.supervise_streamed_run`.

Current limits (see ``docs/MIGRATION.md``):

* Headless only. :meth:`run_interactive` raises — interactive sites
  (``archon discuss`` / ``refactor draft``) stay on claude-code.
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
``codex exec --json --skip-git-repo-check --ignore-user-config -m <model>
-c model_reasoning_effort="<effort>" --sandbox <sandbox> --ephemeral
[gateway -c …] [mcp -c …] [extra] <prompt>``.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from archon import log
from archon.agent import RunOutcome, supervise_streamed_run
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
# A single session_end is emitted once stdin closes (codex exec is one
# turn), carrying the last turn's usage and the last agent_message as the
# summary. Codex reports no USD cost → total_cost_usd is 0.
_CODEX_STREAM_PARSER = r'''
import sys, json, datetime, time

VERBOSE = '{verbose}' == 'True'
RAW = open('{raw_log}', 'a') if VERBOSE else None
JSONL = open('{jsonl}', 'a')

def emit(event_type, **fields):
    row = {{'ts': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), 'event': event_type, **fields}}
    JSONL.write(json.dumps(row) + '\n')
    JSONL.flush()

def terminal(s):
    print(s, flush=True)

start = time.monotonic()
last_text = ''
turns = 0
sid = ''
sid_emitted = False
usage = {{}}
failed = False
fail_msg = ''

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
        sid = obj.get('thread_id', '') or ''
        if sid and not sid_emitted:
            emit('session_meta', session_id=sid)
            sid_emitted = True

    elif t == 'turn.started':
        turns += 1

    elif t in ('item.started', 'item.completed', 'item.updated'):
        item = obj.get('item', {{}}) or {{}}
        it = item.get('type', '')
        if it == 'agent_message':
            if t == 'item.completed':
                txt = (item.get('text') or '').strip()
                if txt:
                    emit('text', content=txt)
                    last_text = txt
        elif it == 'reasoning':
            if t == 'item.completed':
                txt = (item.get('text') or '').strip()
                if txt:
                    emit('thinking', content=txt)
        elif it == 'command_execution':
            if t == 'item.started':
                cmd = item.get('command', '')
                emit('tool_call', tool='shell', input={{'command': cmd}})
            elif t == 'item.completed':
                out = item.get('aggregated_output') or item.get('output') or ''
                code = item.get('exit_code')
                status = item.get('status', '')
                head = f'[exit {{code}}] ' if code is not None else (f'[{{status}}] ' if status else '')
                emit('tool_result', content=head + (out if isinstance(out, str) else json.dumps(out)))
        elif it == 'file_change':
            if t == 'item.started':
                emit('tool_call', tool='apply_patch', input={{'changes': item.get('changes', [])}})
            elif t == 'item.completed':
                emit('tool_result', content='file_change: ' + str(item.get('status', '')))
        elif it == 'mcp_tool_call':
            server = item.get('server', '')
            tool = item.get('tool', '')
            name = (server + '.' + tool) if server else tool
            if t == 'item.started':
                emit('tool_call', tool='mcp:' + name, input=item.get('arguments', {{}}) or {{}})
            elif t == 'item.completed':
                emit('tool_result', content='mcp ' + name + ': ' + str(item.get('status', '')))
        elif it == 'web_search':
            if t == 'item.completed':
                emit('tool_call', tool='web_search', input={{'query': item.get('query', '')}})
        elif it == 'error':
            if t == 'item.completed':
                emit('text', content='[error] ' + str(item.get('message', '')))
        # todo_list and any unknown item types are ignored (additive).

    elif t == 'turn.completed':
        usage = obj.get('usage', {{}}) or {{}}

    elif t == 'turn.failed':
        failed = True
        err = obj.get('error', {{}}) or {{}}
        fail_msg = err.get('message', '') if isinstance(err, dict) else str(err)

    elif t == 'error':
        failed = True
        fail_msg = str(obj.get('message', '')) or fail_msg

# One session_end once the stream closes (codex exec is a single turn).
summary = last_text
if failed and fail_msg:
    summary = '[turn failed] ' + fail_msg if not last_text else last_text
duration_ms = (time.monotonic() - start) * 1000.0
input_tokens = usage.get('input_tokens', 0) or 0
output_tokens = usage.get('output_tokens', 0) or 0
cache_read = usage.get('cached_input_tokens', 0) or 0
emit('session_end',
    session_id=sid,
    total_cost_usd=0,
    duration_ms=duration_ms,
    duration_api_ms=0,
    num_turns=turns,
    input_tokens=input_tokens,
    output_tokens=output_tokens,
    cache_read_input_tokens=cache_read,
    cache_creation_input_tokens=0,
    reasoning_output_tokens=usage.get('reasoning_output_tokens', 0) or 0,
    model_usage={{}},
    summary=summary,
    ended_early=False,
    runner='codex',
)

if summary:
    terminal(summary)
parts = []
if duration_ms:
    parts.append(f'{{duration_ms/60000:.1f}}min')
if input_tokens or output_tokens:
    parts.append(f'in={{input_tokens}} out={{output_tokens}}')
if turns:
    parts.append(f'turns={{turns}}')
if parts:
    terminal('[COST] ' + ' | '.join(parts))

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
    def model(self) -> str:
        """The codex model id (``codex exec -m``)."""
        return self.descriptor.model or "gpt-5.1-codex-max"

    @property
    def effort(self) -> str | None:
        return self.descriptor.effort

    @property
    def sandbox(self) -> str:
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
          -m <model> -c model_reasoning_effort="<effort>"
          [-o <last_message_path>] [gateway -c …] [mcp -c …]
          --sandbox <sandbox> --ephemeral [extra] <prompt>``.

        Gateway ``-c`` flags are appended only when both ``base_url_env``
        and ``key_env`` resolve to set values; a *partial* gateway config
        raises :class:`PartialGatewayConfigError`. MCP ``-c mcp_servers.*``
        flags are appended only when the descriptor opts in
        (``descriptor.mcp``). The prompt is always the final positional arg.
        """
        argv: list[str] = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "-m",
            self.model,
        ]
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

        argv += self._mcp_overrides(lake_root)
        argv += ["--sandbox", self.sandbox, "--ephemeral"]
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

    def _mcp_overrides(self, lake_root: Path | str | None) -> list[str]:
        """Render ``-c mcp_servers.*`` flags for the descriptor's MCP bundles.

        Returns ``[]`` when ``descriptor.mcp`` is empty (the default → no
        MCP). Today the only known bundle is ``"lean-lsp"``.

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
                out += self._lean_lsp_overrides(lake_root)
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

    def _lean_lsp_overrides(self, lake_root: Path | str | None) -> list[str]:
        """The 5 ``-c`` overrides for the ``archon-lean-lsp`` server."""
        lean_lsp_dir = self._lean_lsp_mcp_dir()
        root = Path(lake_root) if lake_root is not None else Path.cwd()
        base = f"mcp_servers.{_LEAN_LSP_SERVER}"

        def flag(key: str, value: object) -> list[str]:
            return ["-c", f"{key}={json.dumps(value)}"]

        out: list[str] = []
        out += flag(f"{base}.command", "uv")
        out += flag(
            f"{base}.args",
            ["run", "--directory", str(lean_lsp_dir), "lean-lsp-mcp"],
        )
        out += flag(f"{base}.env.LEAN_PROJECT_PATH", str(root))
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
        raise NotImplementedError(
            "codex harness is headless-only; interactive roles stay on "
            "claude-code"
        )

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

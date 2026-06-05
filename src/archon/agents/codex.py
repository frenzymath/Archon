"""Codex CLI runner — routes a responsibility to OpenAI Codex (gpt-5.5).

``CodexAgent`` implements the same :class:`~archon.agent.AgentRunner`
protocol as :class:`~archon.agent.ClaudeAgent`, so any site that obtains
its runner from :func:`archon.agent.build_runner` can drive ``codex exec``
instead of ``claude`` purely by config — see the ``harnesses.<name>``
descriptor with ``runner: "codex"``.

This is a **lean cut** (see ``docs/MIGRATION.md`` for the honest limits):

* Headless only. :meth:`run_interactive` raises — interactive sites
  (``archon discuss`` / ``refactor draft``) stay on claude-code.
* No true resume. Codex mints its own ``thread_id`` and resumes via a
  separate subcommand; v1 logs a warning and runs fresh when a
  ``resume_session_id`` is passed.

Dashboard / token parity is *present*: codex's ``exec --json`` stream is
piped through :data:`_CODEX_STREAM_PARSER`, a peer of claude-code's
normaliser, so it lands in the same downstream JSONL schema (``text`` /
``tool_call`` / ``tool_result`` / ``session_end``). The two harnesses are
peers — neither is the "default" the other bolts onto. Codex carries no
per-token USD cost on a native login, so ``session_end`` omits
``total_cost_usd`` and records the per-session token breakdown instead.

The ``archon-lean-lsp`` MCP server can be wired in per-invocation via
``-c mcp_servers.*`` overrides (opt in with ``harnesses.<name>.mcp:
"lean-lsp"``) — see :meth:`build_argv`. Codex has no ``--mcp-config``
flag, so unlike the claude-code path (which registers the server at
``archon init`` time) the codex MCP wiring is entirely self-contained in
this runner's argv; the same server / dir / ``LEAN_PROJECT_PATH`` is
mirrored from the FormalQualBench harness translator.

The invocation mirrors the bash reference runner
(``FormalQualBench/harness/runners/codex/run_session.sh``) flag-for-flag:
``codex exec --json --skip-git-repo-check --ignore-user-config -m <model>
-c model_reasoning_effort="<effort>" --sandbox <sandbox> [gateway -c …]
[extra] <prompt>``.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from archon import log
from archon.agent import RunOutcome, supervise_streamed_run
from archon.commands.tooling.project_config import HarnessDescriptor


# The custom-provider id injected via `-c model_provider=...`, matching
# the bash runner's "harness-gateway" name. The provider reads its key
# from CODEX_GATEWAY_API_KEY (also matching the bash runner) so the raw
# key never appears in argv / process listings.
_GATEWAY_PROVIDER = "harness-gateway"
_GATEWAY_KEY_ENV = "CODEX_GATEWAY_API_KEY"

# The MCP server Archon ships for Lean (same one the claude-code path
# registers at init via ``claude mcp add archon-lean-lsp``). For codex we
# render it as per-invocation ``-c mcp_servers.*`` overrides instead,
# mirroring FormalQualBench/harness/runners/codex/mcp_to_codex_flags.py.
_LEAN_LSP_SERVER = "archon-lean-lsp"
# Known MCP bundle names accepted in ``harnesses.<name>.mcp``. Today the
# only bundle is the Lean LSP; the list shape leaves room for more.
_KNOWN_MCP_BUNDLES = frozenset({"lean-lsp"})
# Codex-specific MCP knobs, mirroring the harness translator:
#   required=true     — fail codex startup if the MCP can't initialize,
#     rather than silently running a no-MCP session whose prompt claims
#     the tools are loaded (would contaminate results vs. the baseline).
#   tool_timeout_sec  — 600s covers a cold Mathlib LSP: the first
#     lean_goal / lean_file_outline indexes imports before responding
#     (2-5 min); codex's default would time out on it.
_MCP_TOOL_TIMEOUT_SEC = 600


class UnknownMcpBundleError(ValueError):
    """A codex harness names an ``mcp`` bundle this runner doesn't know.

    Raised (fail-closed) rather than silently rendering no MCP — a prompt
    that insists the Lean LSP tools are loaded must not run against a
    codex with no MCP server.
    """


class LeanLspMcpUnavailableError(RuntimeError):
    """The bundled ``lean-lsp-mcp`` dir can't be resolved on disk.

    Mirrors the harness's fail-closed file check: rather than render a
    broken ``mcp_servers.archon-lean-lsp.args`` pointing at a missing
    directory (codex would fail to start the server, or — worse — start
    with no tools while the prompt claims they're present), raise loud.
    """


class PartialGatewayConfigError(ValueError):
    """A codex harness gateway is half-configured (base URL XOR key set).

    Raised instead of silently falling back to native ``~/.codex`` login —
    mirrors the bash runner's loud
    ``CODEX_BASE_URL set but CODEX_API_KEY missing`` guard so a typo'd /
    unexported key env can't quietly route the run to the wrong provider.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── codex stream parser ───────────────────────────────────────────────
#
# Peer of :data:`archon.agent._CLAUDE_STREAM_PARSER`. Consumes codex's
# ``exec --json`` stream (a thread / turn / item schema) on stdin and
# writes the *same* normalized JSONL the claude parser does, so the
# dashboard and the cost/token aggregators stay harness-agnostic.
#
# Codex schema → Archon event mapping:
#   thread.started {{thread_id}}                  → session_meta
#   item.completed · agent_message                → text
#   item.started/completed · command_execution    → tool_call / tool_result (Bash)
#   …             · mcp_tool_call                  → tool_call / tool_result (<tool>)
#   …             · file_change                    → tool_call / tool_result (Edit)
#   …             · todo_list                      → tool_call (TodoWrite)
#   turn.completed {{usage}}                       → accumulate; session_end at EOF
#
# Codex carries no per-token USD cost on a native login, so we
# deliberately omit ``total_cost_usd`` (cost aggregators read the missing
# key as 0) and record only the session's token breakdown. ``input_tokens``
# is the *fresh* (non-cached) input — codex reports a cache-inclusive
# total, so we subtract the cached portion to match claude's accounting,
# where cache reads are tracked separately in ``cache_read_input_tokens``.
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
        return {{'server': item.get('server', ''), 'tool': item.get('tool', ''), 'arguments': item.get('arguments', {{}})}}
    if t == 'file_change':
        return {{'changes': item.get('changes', [])}}
    if t == 'todo_list':
        return {{'items': item.get('items', [])}}
    return {{}}

def tool_result_content(item):
    t = item.get('type', '')
    if t == 'command_execution':
        out = item.get('aggregated_output', '') or ''
        code = item.get('exit_code')
        if code is not None:
            out = (out + '\n' if out else '') + '[exit ' + str(code) + ']'
        return out
    if t == 'mcp_tool_call':
        err = item.get('error')
        if err:
            return '[error] ' + (err if isinstance(err, str) else json.dumps(err, ensure_ascii=False))
        res = item.get('result')
        if res is None:
            return ''
        return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)
    if t == 'file_change':
        lines = []
        for ch in item.get('changes', []) or []:
            if isinstance(ch, dict):
                lines.append(str(ch.get('kind', '?')) + ' ' + str(ch.get('path', '')))
        return '\n'.join(lines)
    return ''

session_id_emitted = False
started_ids = set()
last_message = ''
sum_input = sum_cached = sum_output = sum_reasoning = 0
num_turns = 0

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
        item = obj.get('item', {{}}) or {{}}
        it = item.get('type', '')
        if it == 'agent_message':
            text = (item.get('text', '') or '').strip()
            if text:
                emit('text', content=text)
                last_message = text
        elif it in _TOOL_ITEMS:
            # If we never saw item.started for this id, synthesize the
            # call so the dashboard always shows a call before its result.
            if item.get('id', '') not in started_ids:
                emit('tool_call', tool=tool_label(item), input=tool_input(item))
            if it != 'todo_list':
                emit('tool_result', content=tool_result_content(item))

    elif t == 'turn.completed':
        usage = obj.get('usage', {{}}) or {{}}
        sum_input += usage.get('input_tokens', 0) or 0
        sum_cached += usage.get('cached_input_tokens', 0) or 0
        sum_output += usage.get('output_tokens', 0) or 0
        sum_reasoning += usage.get('reasoning_output_tokens', 0) or 0
        num_turns += 1

    elif t == 'turn.failed':
        err = obj.get('error')
        if err:
            last_message = '[turn.failed] ' + (err if isinstance(err, str) else json.dumps(err, ensure_ascii=False))

fresh_input = sum_input - sum_cached
if fresh_input < 0:
    fresh_input = 0
emit('session_end',
    num_turns=num_turns,
    input_tokens=fresh_input,
    output_tokens=sum_output,
    cache_read_input_tokens=sum_cached,
    cache_creation_input_tokens=0,
    reasoning_output_tokens=sum_reasoning,
    input_tokens_total=sum_input,
    model_usage={{MODEL: {{'inputTokens': fresh_input, 'outputTokens': sum_output}}}},
    summary=last_message,
)

if last_message:
    terminal(last_message)
parts = []
if fresh_input or sum_output:
    parts.append('in=' + str(fresh_input) + ' cached=' + str(sum_cached) + ' out=' + str(sum_output) + ' reasoning=' + str(sum_reasoning))
if num_turns:
    parts.append('turns=' + str(num_turns))
if parts:
    terminal('[TOKENS] ' + ' | '.join(parts))

JSONL.close()
if RAW: RAW.close()
'''


# Where prompt variants live, mirroring Archon's prompt resolution
# (local-overrides-bundled): a project may override a variant by dropping
# ``.archon/prompts/variants/<name>.md`` in its state dir; otherwise the
# bundled ``.archon-src/prompts/variants/<name>.md`` is used (copied to
# the project at ``archon init``). The relative segment is shared so both
# the lookup and the init copy step agree on the layout.
_PROMPT_VARIANTS_SUBDIR = "variants"


def resolve_prompt_variant(
    variant: str, *, project_path: Path | None = None,
) -> str | None:
    """Resolve a prompt-variant name to its text (project overrides bundled).

    Mirrors how Archon resolves its other prompts: a project-local copy
    under ``<project>/.archon/prompts/variants/<name>.md`` wins over the
    bundled package data at ``.archon-src/prompts/variants/<name>.md``
    (``data_path``). ``archon init`` copies bundled prompts into the
    project, so an existing project that re-inits picks up the variant
    file and may then edit its local copy.

    Returns the file's text, or ``None`` when neither location has it
    (the caller then warns and proceeds with the unmodified prompt).
    ``project_path`` is the running project's root (``run()``'s ``cwd``);
    when ``None``, only the bundled location is consulted.
    """
    from archon.commands.loop.utils import data_path

    rel = f"{_PROMPT_VARIANTS_SUBDIR}/{variant}.md"

    # 1. Project-local override (state dir mirrors the bundled layout).
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

    # 2. Bundled package data.
    bundled = Path(data_path(f"prompts/{rel}"))
    if bundled.is_file():
        try:
            return bundled.read_text(encoding="utf-8")
        except OSError:
            return None
    return None


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
        """The codex model id (``codex exec -m``). Falls back to gpt-5.5."""
        return self.descriptor.model or "gpt-5.5"

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

        Mirrors the claude-code runner reading ANTHROPIC_BASE_URL /
        ANTHROPIC_AUTH_TOKEN: the descriptor names *which* env vars hold
        the gateway base URL and key (e.g. ``CODEX_BASE_URL`` /
        ``CZ_API_KEY``); the values come from the process environment (or
        ``.archon/.env``, already merged into ``os.environ`` by
        ``env_loader``). Returns ``(None, None)`` when the descriptor does
        not configure a gateway (``base_url_env`` unset) → codex uses its
        native ``~/.codex`` login.

        **Fails loud on a partial gateway configuration.** The descriptor
        configures a gateway by naming ``base_url_env`` (and ``key_env``);
        once configured, *both* env vars must resolve to non-empty values.
        Mirroring the bash runner's
        ``: "${CODEX_API_KEY:?CODEX_BASE_URL set but CODEX_API_KEY missing}"``
        guard, a half-set gateway (base URL present but key absent, or
        vice-versa) raises :class:`PartialGatewayConfigError` naming the
        missing env var rather than silently falling back to native login —
        which would route to the wrong provider with no warning.
        """
        src = env_source if env_source is not None else os.environ
        d = self.descriptor
        if not d.base_url_env:
            # Gateway not configured at all → native login (unchanged).
            return None, None

        base_url = src.get(d.base_url_env) or None
        key = src.get(d.key_env) if d.key_env else None
        key = key or None

        # Gateway IS configured (base_url_env named). Require both values.
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
        # Both set → gateway; neither set → native login.
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

        Mirrors ``run_session.sh`` base_flags + exec_only ordering:

            codex exec --json --skip-git-repo-check --ignore-user-config
              -m <model> -c model_reasoning_effort="<effort>"
              [-o <last_message_path>]
              [gateway provider -c overrides …]
              [MCP -c overrides …]
              --sandbox <sandbox> --ephemeral
              [descriptor.raw["extra_args"] …] [extra_args …]
              <prompt>

        Gateway ``-c`` flags are appended only when both ``base_url_env``
        and ``key_env`` resolve to set values (via ``env_source`` /
        ``os.environ``); a *partial* gateway config raises
        :class:`PartialGatewayConfigError` (see :meth:`_gateway_creds`).

        MCP ``-c mcp_servers.*`` flags are appended only when the
        descriptor opts in (``descriptor.mcp`` names a known bundle, e.g.
        ``"lean-lsp"``); see :meth:`_mcp_overrides`. ``lake_root`` is the
        Lean lake project root threaded in by :meth:`run` (its ``cwd``);
        it becomes the server's ``LEAN_PROJECT_PATH``. When the descriptor
        opts into ``lean-lsp`` but ``lake_root`` is ``None``, the runner's
        ``cwd`` is unknown at build time and we still render the server
        with ``LEAN_PROJECT_PATH`` pointed at the current process cwd as a
        last resort — but :meth:`run` always supplies it, so that fallback
        is only hit by callers building argv directly.

        ``--ephemeral`` keeps rollouts off disk (v1 never resumes). The
        prompt is always the final positional arg.
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
            # Symmetric to the bash runner: inject a custom `responses`
            # provider; the key is read from CODEX_GATEWAY_API_KEY (set in
            # the child env by _build_env), never passed on the command line.
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

        # Descriptor-level extra flags (mirrors CODEX_EXTRA_FLAGS), then
        # call-site extra_args. descriptor.raw["extra_args"] may be a list
        # or a whitespace-delimited string.
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

        Returns ``[]`` when ``descriptor.mcp`` is empty (the default →
        no MCP, identical to before). Otherwise, for each named bundle,
        emit the codex config overrides. Today the only known bundle is
        ``"lean-lsp"``, rendered as the ``archon-lean-lsp`` server exactly
        as the FormalQualBench translator does:

            mcp_servers.archon-lean-lsp.command="uv"
            mcp_servers.archon-lean-lsp.args=["run","--directory",
              "<data_path('tools/lean-lsp-mcp')>","lean-lsp-mcp"]
            mcp_servers.archon-lean-lsp.env.LEAN_PROJECT_PATH="<lake root>"
            mcp_servers.archon-lean-lsp.required=true
            mcp_servers.archon-lean-lsp.tool_timeout_sec=600

        Each value is JSON-encoded — valid TOML for the ``-c key=value``
        shapes used here (strings, string arrays, ints, bools).

        Raises:
            UnknownMcpBundleError: a configured bundle name isn't known.
            LeanLspMcpUnavailableError: the bundled ``lean-lsp-mcp`` dir
                can't be resolved on disk (fail-closed; mirrors the
                harness's file check).
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

        Same source of truth as the claude-code init step
        (``commands/init/steps/lean_lsp.py`` → ``data_path``). Raises
        :class:`LeanLspMcpUnavailableError` when the dir is missing rather
        than rendering a server pointed at a non-existent path.
        """
        # Local import keeps the agent module free of an init-time
        # dependency at import; ``data_path`` is a pure path helper.
        from archon.commands.init.utils import data_path

        lean_lsp_dir = Path(data_path("tools/lean-lsp-mcp"))
        if not lean_lsp_dir.is_dir():
            raise LeanLspMcpUnavailableError(
                f"codex harness mcp 'lean-lsp': the bundled lean-lsp-mcp "
                f"directory does not exist at {lean_lsp_dir} — refusing to "
                f"render an archon-lean-lsp server pointed at a missing "
                f"path. Reinstall Archon's data tree "
                f"(.archon-src/tools/lean-lsp-mcp) or unset the harness's "
                f"`mcp` field."
            )
        return lean_lsp_dir

    def _lean_lsp_overrides(self, lake_root: Path | str | None) -> list[str]:
        """The 5 ``-c`` overrides for the ``archon-lean-lsp`` server."""
        lean_lsp_dir = self._lean_lsp_mcp_dir()
        # lake root = the project dir codex runs in (run()'s cwd). Fall
        # back to the process cwd only when a direct build_argv caller
        # omitted it; run() always threads its cwd.
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
        """Append the descriptor's prompt variant to ``prompt`` (if any).

        When ``descriptor.prompt_variant`` is set, resolve the variant
        text (project ``.archon/prompts/variants/<name>.md`` overrides the
        bundled copy — :func:`resolve_prompt_variant`) and append it to the
        incoming prompt, mirroring the harness gemini ``prompt_tail``
        pattern (the loop's evolving prover prompt stays the single source
        of truth; the variant is a codex-specific tail, not a fork). The
        append is deterministic: a fixed separator, no date/random.

        A missing variant file is non-fatal: ``log.warn`` and return the
        prompt unchanged (never crash a run over a prompt tail). With
        ``prompt_variant`` unset, the prompt is returned verbatim.
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

        The gateway API key (resolved from the descriptor's ``key_env``)
        is copied into ``CODEX_GATEWAY_API_KEY`` so the injected provider
        can read it via ``env_key`` — exactly as the bash runner does —
        keeping the secret out of argv. Caller-supplied ``env_overrides``
        are merged **first** (and win on conflict); gateway creds are then
        resolved from that merged env, so an override may supply the creds
        and ``CODEX_GATEWAY_API_KEY`` is set from the same env snapshot that
        :meth:`build_argv` reads when deciding whether to inject the
        provider — keeping the two consistent.
        """
        env = os.environ.copy()
        # Merge caller overrides FIRST, then resolve gateway creds from the
        # merged env — so build_env and build_argv (which resolves from the
        # env it's given, i.e. this result) agree on a single env snapshot.
        # If a lane supplies gateway creds via env_overrides, they must be
        # visible to _gateway_creds here, otherwise CODEX_GATEWAY_API_KEY
        # would never be set while build_argv still injects the provider that
        # reads it → auth failure.
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
        idle-watchdog / ordered teardown, identical to ClaudeAgent.
        Codex's native ``--json`` stream is written **raw** to
        ``{log_base}.jsonl`` (no normalisation — the dashboard does not
        parse codex cost/session in v1). ``env_overrides`` is merged into
        the child env. On idle-kill the same prompt is re-run up to
        ``max_attempts`` times; a real non-zero exit returns False
        immediately (no retry).
        """
        if resume_session_id:
            # Codex's resume model differs (separate `codex exec resume`
            # subcommand keyed on a self-minted thread_id). v1 does not
            # implement it; run fresh and say so loudly.
            log.warn(
                "codex harness: resume_session_id is not supported in v1; "
                "running a fresh codex session instead."
            )

        # Append the codex prompt variant (if configured) before the run.
        # cwd is the project root (lake root) → project-local variant
        # overrides win. Unset prompt_variant ⇒ prompt unchanged.
        prompt = self._apply_prompt_variant(prompt, project_path=cwd)

        env = self.build_env(env_overrides)
        self._announce()

        if log_base is None:
            # No log file → no watchdog. Mirror ClaudeAgent's simple
            # blocking fallback. Stream codex JSON to devnull.
            import subprocess

            argv = self.build_argv(
                prompt, extra_args=extra_args, env_source=env, lake_root=cwd,
            )
            return subprocess.run(argv, cwd=cwd, env=env).returncode == 0

        if max_attempts < 1:
            max_attempts = 1

        last_ok = False
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
                # Real failure — retrying would just re-burn tokens.
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
            last_ok = False
        return last_ok

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
        """Stamp a minimal session_start so the log records the model/role.

        The normalized event stream from :data:`_CODEX_STREAM_PARSER`
        then follows. We keep this header consistent with ClaudeAgent's so
        the dashboard reads the model the same way for both harnesses; the
        parser closes the run with a synthesized ``session_end`` carrying
        the token breakdown (no ``total_cost_usd`` — codex bills none on a
        native login).
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
        """Run codex once with normalized-stream logging + the watchdog."""
        log_base.parent.mkdir(parents=True, exist_ok=True)
        jsonl = f"{log_base}.jsonl"
        raw_log = f"{log_base}.raw.jsonl"
        jsonl_path = Path(jsonl)
        last_message = log_base.parent / f"{log_base.name}.last_message.txt"

        # Header row first, then the parser appends normalized events to
        # the same file (the raw codex stream goes to raw_log when verbose).
        self._emit_session_start(jsonl, attempt=attempt)

        argv = self.build_argv(
            prompt,
            last_message_path=last_message,
            extra_args=extra_args,
            env_source=env,
            lake_root=cwd,
        )

        # Pipe codex's `exec --json` through the peer normaliser so its
        # thread/turn/item stream lands in the dashboard's shared schema —
        # text / tool_call / tool_result / session_end (with the token
        # breakdown) — exactly like the claude-code path. stderr → raw log
        # when verbose, else devnull (mirrors ClaudeAgent).
        parser_script = _CODEX_STREAM_PARSER.format(
            verbose=str(verbose_logs),
            raw_log=raw_log,
            jsonl=jsonl,
            model=self.model,
        )
        stderr_dest = raw_log if verbose_logs else os.devnull

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
        # Success = codex exit 0. (The bash runner promotes a usage-limit
        # turn.failed to a distinct phase; v1 treats every non-zero exit
        # as FAILED and relies on the idle-watchdog for silent hangs.)
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

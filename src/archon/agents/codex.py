"""Codex CLI runner — routes a responsibility to OpenAI Codex (gpt-5.5).

``CodexAgent`` implements the same :class:`~archon.agent.AgentRunner`
protocol as :class:`~archon.agent.ClaudeAgent`, so any site that obtains
its runner from :func:`archon.agent.build_runner` can drive ``codex exec``
instead of ``claude`` purely by config — see the ``harnesses.<name>``
descriptor with ``runner: "codex"``.

This is a **lean v1** (see ``docs/MIGRATION.md`` for the honest limits):

* Headless only. :meth:`run_interactive` raises — interactive sites
  (``archon discuss`` / ``refactor draft``) stay on claude-code.
* No MCP. Codex uses its native ``exec_command`` / ``apply_patch`` /
  ``read_file`` tools and can run ``lake``/``lean`` via shell; the
  ``archon-lean-lsp`` MCP server is NOT wired in (slower inner-loop
  feedback than the claude-code prover).
* No dashboard cost/session parity. Codex's ``--json`` stream is a
  different schema from claude's ``stream-json``; we write it to the log
  **raw** and do not synthesize the dashboard's cost/session_end rows.
* No true resume. Codex mints its own ``thread_id`` and resumes via a
  separate subcommand; v1 logs a warning and runs fresh when a
  ``resume_session_id`` is passed.

The invocation mirrors the bash reference runner
(``FormalQualBench/harness/runners/codex/run_session.sh``) flag-for-flag:
``codex exec --json --skip-git-repo-check --ignore-user-config -m <model>
-c model_reasoning_effort="<effort>" --sandbox <sandbox> [gateway -c …]
[extra] <prompt>``.
"""

from __future__ import annotations

import json
import os
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
        ``env_loader``). Returns ``(None, None)`` when ``base_url_env`` is
        unconfigured → codex uses its native ``~/.codex`` login.
        """
        src = env_source if env_source is not None else os.environ
        d = self.descriptor
        if not d.base_url_env:
            return None, None
        base_url = src.get(d.base_url_env) or None
        key = src.get(d.key_env) if d.key_env else None
        return base_url, (key or None)

    def build_argv(
        self,
        prompt: str,
        *,
        last_message_path: Path | None = None,
        extra_args: list[str] | None = None,
        env_source: dict[str, str] | None = None,
    ) -> list[str]:
        """Build the full ``codex exec`` argv (no subprocess spawned).

        Mirrors ``run_session.sh`` base_flags + exec_only ordering:

            codex exec --json --skip-git-repo-check --ignore-user-config
              -m <model> -c model_reasoning_effort="<effort>"
              [-o <last_message_path>]
              [gateway provider -c overrides …]
              --sandbox <sandbox> --ephemeral
              [descriptor.raw["extra_args"] …] [extra_args …]
              <prompt>

        Gateway ``-c`` flags are appended only when both ``base_url_env``
        and ``key_env`` resolve to set values (via ``env_source`` /
        ``os.environ``). ``--ephemeral`` keeps rollouts off disk (v1 never
        resumes). The prompt is always the final positional arg.
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

    def build_env(
        self, env_overrides: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build the child env: ``os.environ`` + gateway key + overrides.

        The gateway API key (resolved from the descriptor's ``key_env``)
        is copied into ``CODEX_GATEWAY_API_KEY`` so the injected provider
        can read it via ``env_key`` — exactly as the bash runner does —
        keeping the secret out of argv. Caller-supplied ``env_overrides``
        are merged last and win on conflict.
        """
        env = os.environ.copy()
        base_url, api_key = self._gateway_creds(env)
        if base_url and api_key:
            env[_GATEWAY_KEY_ENV] = api_key
        if env_overrides:
            env.update(env_overrides)
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

        env = self.build_env(env_overrides)
        self._announce()

        if log_base is None:
            # No log file → no watchdog. Mirror ClaudeAgent's simple
            # blocking fallback. Stream codex JSON to devnull.
            import subprocess

            argv = self.build_argv(prompt, extra_args=extra_args, env_source=env)
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

        Codex's own raw stream then follows, byte-for-byte. We keep this
        header consistent with ClaudeAgent's so a log viewer can still
        read the model — but we do NOT synthesize a codex session_end /
        cost row (deferred; codex's schema differs).
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
        """Run codex once with raw-stream logging + the shared watchdog."""
        log_base.parent.mkdir(parents=True, exist_ok=True)
        jsonl = f"{log_base}.jsonl"
        jsonl_path = Path(jsonl)
        last_message = log_base.parent / f"{log_base.name}.last_message.txt"

        # Header row first, then codex's raw --json stream is appended to
        # the same file.
        self._emit_session_start(jsonl, attempt=attempt)

        argv = self.build_argv(
            prompt,
            last_message_path=last_message,
            extra_args=extra_args,
            env_source=env,
        )

        # stderr → raw log when verbose, else devnull (mirrors ClaudeAgent).
        stderr_dest = f"{log_base}.raw.jsonl" if verbose_logs else os.devnull

        result = supervise_streamed_run(
            argv,
            cwd=cwd,
            env=env,
            jsonl_path=jsonl_path,
            stdout_dest=jsonl,  # codex JSON written raw, no parser
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


__all__ = ["CodexAgent"]

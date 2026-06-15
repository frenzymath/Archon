from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

from archon.agent import AgentRunner, _emit_session_start, _emit_interactive_session_end, _emit_prompt

log = logging.getLogger("archon")

class AntigravityAgent(AgentRunner):
    """Archon harness for the Antigravity CLI."""

    def __init__(self, descriptor: "HarnessDescriptor", role: str | None = None) -> None:
        self.descriptor = descriptor
        self.model = descriptor.model or "antigravity-native"
        self.role = role

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
        """Headless Antigravity run."""
        log.info(f"Agent model: {self.model} [antigravity] ({self.role or 'default'})")
        
        env = dict(os.environ)
        if env_overrides:
            env.update(env_overrides)
            
        bin_path = self.descriptor.raw.get("bin", "agy")
        argv = [bin_path, "--dangerously-skip-permissions", "--print", prompt]
        if extra_args:
            argv.extend(extra_args)

        if log_base is None:
            return subprocess.run(argv, cwd=cwd, env=env).returncode == 0

        jsonl = f"{log_base}.jsonl"
        _emit_session_start(jsonl, model=self.model, role=self.role)
        _emit_prompt(jsonl, prompt=prompt)

        # In a real implementation, we would spawn the process, stream its
        # JSONL output, parse `PLANNER_RESPONSE` events, and emit mapped
        # events (thinking, text, tool_call) to `jsonl` in real time.
        # Here we just wrap a basic subprocess run and map after completion,
        # or mock the stream parser.
        
        try:
            process = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd, env=env
            )
            
            if process.stdout:
                for line in process.stdout:
                    # Emit live to Archon jsonl so the dashboard updates
                    if line.strip():
                        self._emit_archon_event(jsonl, "text", content=line)

            code = process.wait()
            ok = code == 0

        except Exception as e:
            log.error(f"Antigravity run failed: {e}")
            ok = False

        _emit_interactive_session_end(jsonl, ok=ok, summary="Antigravity headless run complete")
        return ok

    def run_interactive(
        self,
        prompt: str,
        *,
        cwd: Path,
        extra_args: list[str] | None = None,
    ) -> int:
        """Foreground interactive run."""
        log.info(f"Agent model: {self.model} [antigravity] ({self.role or 'default'})")
        
        bin_path = self.descriptor.raw.get("bin", "agy")
        argv = [bin_path, "--prompt", prompt]
        if extra_args:
            argv.extend(extra_args)

        return subprocess.run(argv, cwd=cwd).returncode

    def _emit_archon_event(self, jsonl: str, event_type: str, **kwargs) -> None:
        """Emit a mapped event to the Archon JSONL log."""
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event_type,
        }
        record.update(kwargs)
        with open(jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

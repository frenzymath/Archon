from __future__ import annotations

import datetime
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from archon.agent import AgentRunner, _emit_session_start, _emit_interactive_session_end, _emit_prompt
from archon.state.cost import estimate_cost_usd

log = logging.getLogger("archon")


def extract_json_or_text(blob: bytes) -> tuple[str, str | dict] | None:
    """Extract JSON objects or ASCII text from raw protobuf bytes."""
    if not blob:
        return None

    # 1. Search for JSON structures (representing tool calls)
    json_matches = re.findall(b"(\\{[^{}]*\\})", blob)
    for m in json_matches:
        try:
            decoded = m.decode("utf-8", errors="ignore")
            parsed = json.loads(decoded)
            if isinstance(parsed, dict) and parsed:
                return ("json", parsed)
        except Exception:
            pass

    # 2. Extract ASCII text strings (longest printable text)
    ascii_seqs = re.findall(b"([\x20-\x7e\n\r\t]{10,})", blob)
    valid_texts = []
    for s in ascii_seqs:
        try:
            decoded = s.decode("utf-8").strip()
            # Clean up junk signatures / system strings
            if any(term in decoded for term in ["sessionID", "cascade", "trajectory", "b$", "google.rpc", "RetryInfo", "quotaReset"]):
                continue
            if decoded.startswith("@type") or decoded.startswith("type.googleapis.com"):
                continue
            if len(decoded) > 10:
                valid_texts.append(decoded)
        except Exception:
            pass
    if valid_texts:
        return ("text", max(valid_texts, key=len))
    return None


def _emit_archon_event_static(jsonl_path: str, event_type: str, **kwargs) -> None:
    """Emit a mapped event to the Archon JSONL log file."""
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": event_type,
    }
    record.update(kwargs)
    try:
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


def poll_antigravity_db(
    db_path: Path,
    jsonl_path: str,
    stop_event: threading.Event,
    model: str,
    role: str | None,
) -> None:
    """Background thread to poll the SQLite steps table and emit live events."""
    # Give the database a brief moment to initialize
    time.sleep(0.5)

    last_idx = -1
    emitted_texts: set[str] = set()

    total_in_chars = 15000
    turn_out_chars = 0

    conn = None
    try:
        # Open SQLite in read-only WAL mode safely
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception as e:
        log.error(f"Failed to connect to SQLite DB {db_path}: {e}")
        return

    while not stop_event.is_set():
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT idx, step_type, status, step_payload FROM steps WHERE idx > ? ORDER BY idx",
                (last_idx,),
            )
            rows = cur.fetchall()
            cur.close()

            for idx, step_type, status, payload in rows:
                last_idx = idx
                if not payload:
                    continue

                parsed = extract_json_or_text(payload)
                if not parsed:
                    continue

                val_type, val = parsed
                if val_type == "json":
                    # Determine tool name from keys
                    tool_name = "tool"
                    if "CommandLine" in val:
                        tool_name = "Bash"
                    elif "DirectoryPath" in val:
                        tool_name = "ListDir"
                    elif "AbsolutePath" in val:
                        tool_name = "ViewFile"
                    elif "TargetFile" in val:
                        tool_name = "EditFile"
                    elif "Query" in val:
                        tool_name = "GrepSearch"
                    elif "Subagents" in val:
                        tool_name = "InvokeSubagent"
                    elif "Recipient" in val:
                        tool_name = "SendMessage"

                    _emit_archon_event_static(jsonl_path, "tool_call", tool=tool_name, input=val)
                    turn_out_chars += len(json.dumps(val))

                    # Emit turn usage on tool execution trigger
                    if turn_out_chars > 0:
                        in_t = int(total_in_chars / 4.0)
                        out_t = int(turn_out_chars / 4.0)
                        cost = estimate_cost_usd(model, in_t, out_t)
                        _emit_archon_event_static(
                            jsonl_path,
                            "turn_usage",
                            input_tokens=in_t,
                            output_tokens=out_t,
                            cost_usd=cost,
                        )
                        total_in_chars += turn_out_chars
                        turn_out_chars = 0

                else:  # val_type == 'text'
                    if val in emitted_texts:
                        continue
                    emitted_texts.add(val)

                    if step_type in (14, 23):
                        # User/system message
                        pass
                    elif step_type == 15:
                        # Assistant thinking or text
                        if len(val) > 200 or "\n" in val:
                            _emit_archon_event_static(jsonl_path, "text", content=val)
                        else:
                            _emit_archon_event_static(jsonl_path, "thinking", content=val)
                        turn_out_chars += len(val)
                    elif step_type == 21:
                        # Tool output
                        _emit_archon_event_static(jsonl_path, "tool_result", content=val)
                        total_in_chars += len(val)

        except Exception:
            # Table locks / busy states are ignored
            pass

        time.sleep(0.5)

    # Emit final turn usage if anything remained
    if turn_out_chars > 0:
        in_t = int(total_in_chars / 4.0)
        out_t = int(turn_out_chars / 4.0)
        cost = estimate_cost_usd(model, in_t, out_t)
        _emit_archon_event_static(
            jsonl_path,
            "turn_usage",
            input_tokens=in_t,
            output_tokens=out_t,
            cost_usd=cost,
        )

    if conn:
        conn.close()


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
        """Headless Antigravity run with real-time SQLite polling."""
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

        conv_dir = Path("~/.gemini/antigravity-cli/conversations").expanduser()
        existing_dbs = set(conv_dir.glob("*.db")) if conv_dir.exists() else set()

        try:
            process = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd, env=env
            )

            # Discover the newly created SQLite database file
            active_db = None
            start_detect = time.time()
            while time.time() - start_detect < 3.0:
                if conv_dir.exists():
                    current_dbs = set(conv_dir.glob("*.db"))
                    new_dbs = current_dbs - existing_dbs
                    if new_dbs:
                        active_db = list(new_dbs)[0]
                        break
                time.sleep(0.2)

            if active_db is None and conv_dir.exists():
                all_dbs = list(conv_dir.glob("*.db"))
                if all_dbs:
                    active_db = max(all_dbs, key=lambda p: p.stat().st_mtime)

            # Spawn database monitor thread
            stop_event = threading.Event()
            monitor_thread = None
            if active_db:
                log.info(f"Monitoring Antigravity SQLite DB: {active_db}")
                monitor_thread = threading.Thread(
                    target=poll_antigravity_db,
                    args=(active_db, jsonl, stop_event, self.model, self.role),
                    daemon=True,
                )
                monitor_thread.start()

            # Read stdout to ensure process makes forward progress
            if process.stdout:
                for line in process.stdout:
                    # Print to terminal output, but do not write to jsonl (DB polling handles jsonl events)
                    print(line, end="", flush=True)

            code = process.wait()
            ok = code == 0

            # Stop database monitor thread
            if monitor_thread:
                stop_event.set()
                monitor_thread.join(timeout=1.0)

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

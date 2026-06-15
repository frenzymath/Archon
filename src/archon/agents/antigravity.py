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


def decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        val |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            return val, pos
        shift += 7
    return val, pos


def extract_protobuf_fields(data: bytes, parent_path: str = "", depth: int = 0) -> dict[str, bytes]:
    """Extract length-delimited fields from protobuf data and return a flat dict of field_path -> bytes."""
    fields = {}
    if depth > 10:
        return fields
    pos = 0
    while pos < len(data):
        try:
            key, next_pos = decode_varint(data, pos)
            if next_pos == pos:
                break
            pos = next_pos
            wire_type = key & 0x07
            field_num = key >> 3
            
            field_path = f"{parent_path}.{field_num}" if parent_path else str(field_num)
            
            if wire_type == 0:  # Varint
                _, pos = decode_varint(data, pos)
            elif wire_type == 1:  # 64-bit
                pos += 8
            elif wire_type == 2:  # Length-delimited
                length, pos = decode_varint(data, pos)
                if pos + length > len(data):
                    break
                val = data[pos : pos + length]
                fields[field_path] = val
                # Recursively parse nested messages
                if len(val) > 0:
                    nested = extract_protobuf_fields(val, field_path, depth + 1)
                    fields.update(nested)
                pos += length
            elif wire_type == 5:  # 32-bit
                pos += 4
            else:
                break
        except Exception:
            break
    return fields


def extract_tool_result(fields: dict[str, bytes], step_type: int) -> str | None:
    """Extract clean tool result string from execution steps."""
    if step_type == 9:  # ListDir
        # Gather all entries under 15.3
        entries = []
        for path, val_bytes in sorted(fields.items()):
            parts = path.split('.')
            if len(parts) >= 2 and parts[-2] == '3' and parts[-3] == '15':
                try:
                    entries.append(val_bytes.decode('utf-8'))
                except Exception:
                    pass
        if entries:
            return "\n".join(entries)

    # For other step types: find the longest valid UTF-8 string
    # that is not under '5.*' and is not a UUID or system string.
    best_str = None
    best_len = -1
    
    for path, val_bytes in fields.items():
        # Ignore fields under '5.*' (call metadata)
        parts = path.split('.')
        if parts[0] == '5':
            continue
            
        try:
            s = val_bytes.decode('utf-8')
            if not s:
                continue
            # Ignore UUIDs
            if len(s) == 36 and s.count('-') == 4:
                continue
            # Ignore sessionID or tracking tokens
            if any(term in s for term in ('sessionID', 'google.rpc', 'RetryInfo', 'quotaReset')):
                continue
            # Ignore JSON strings
            if s.startswith('{') and s.endswith('}'):
                continue
                
            if len(s) > best_len:
                best_len = len(s)
                best_str = s
        except Exception:
            pass
            
    return best_str


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
    time.sleep(0.5)

    last_idx = -1
    last_emitted_thinking: dict[int, str] = {}
    last_emitted_text: dict[int, str] = {}
    last_emitted_result: dict[int, str] = {}
    emitted_tool_calls: set[int] = set()

    total_in_chars = 15000
    turn_out_chars = 0

    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception as e:
        log.error(f"Failed to connect to SQLite DB {db_path}: {e}")
        return

    while not stop_event.is_set():
        try:
            cur = conn.cursor()
            # Query steps that are new OR currently running (status = 2)
            cur.execute(
                "SELECT idx, step_type, status, step_payload FROM steps WHERE idx > ? OR status = 2 ORDER BY idx",
                (last_idx,),
            )
            rows = cur.fetchall()
            cur.close()

            for idx, step_type, status, payload in rows:
                last_idx = max(last_idx, idx)
                if not payload:
                    continue

                fields = extract_protobuf_fields(payload)
                if not fields:
                    continue

                if step_type == 15:
                    # Assistant turn
                    # Find candidate content fields for thinking and text
                    val_20_1 = fields.get("20.1") or fields.get("5.20.1")
                    val_20_3 = fields.get("20.3") or fields.get("5.20.3")
                    
                    # Fallback to suffix matching for robustness
                    if not val_20_1 or not val_20_3:
                        for path, val_bytes in fields.items():
                            if path == "20.1" or path.endswith(".20.1"):
                                val_20_1 = val_bytes
                            elif path == "20.3" or path.endswith(".20.3"):
                                val_20_3 = val_bytes

                    thinking_str = None
                    text_str = None
                    
                    if val_20_1 and val_20_3:
                        try:
                            thinking_str = val_20_1.decode("utf-8", errors="ignore").strip()
                        except Exception:
                            pass
                        try:
                            text_str = val_20_3.decode("utf-8", errors="ignore").strip()
                        except Exception:
                            pass
                    elif val_20_1:
                        try:
                            text_str = val_20_1.decode("utf-8", errors="ignore").strip()
                        except Exception:
                            pass

                    # Emit thinking
                    if thinking_str:
                        prev = last_emitted_thinking.get(idx, "")
                        if thinking_str != prev:
                            _emit_archon_event_static(jsonl_path, "thinking", content=thinking_str)
                            last_emitted_thinking[idx] = thinking_str
                            turn_out_chars += len(thinking_str) - len(prev)
                            
                    # Emit text
                    if text_str:
                        prev = last_emitted_text.get(idx, "")
                        if text_str != prev:
                            _emit_archon_event_static(jsonl_path, "text", content=text_str)
                            last_emitted_text[idx] = text_str
                            turn_out_chars += len(text_str) - len(prev)

                    # Extract tool call
                    val_20_7_3 = fields.get("20.7.3") or fields.get("5.20.7.3")
                    if not val_20_7_3:
                        for path, val_bytes in fields.items():
                            if path == "20.7.3" or path.endswith(".20.7.3"):
                                val_20_7_3 = val_bytes
                                break

                    if val_20_7_3 and idx not in emitted_tool_calls:
                        try:
                            parsed_json = json.loads(val_20_7_3.decode("utf-8", errors="ignore"))
                            if isinstance(parsed_json, dict) and parsed_json:
                                tool_name = "tool"
                                if "CommandLine" in parsed_json:
                                    tool_name = "Bash"
                                elif "DirectoryPath" in parsed_json:
                                    tool_name = "ListDir"
                                elif "AbsolutePath" in parsed_json:
                                    tool_name = "ViewFile"
                                elif "TargetFile" in parsed_json:
                                    tool_name = "EditFile"
                                elif "Query" in parsed_json:
                                    tool_name = "GrepSearch"
                                elif "Subagents" in parsed_json:
                                    tool_name = "InvokeSubagent"
                                elif "Recipient" in parsed_json:
                                    tool_name = "SendMessage"
                                    
                                _emit_archon_event_static(jsonl_path, "tool_call", tool=tool_name, input=parsed_json)
                                emitted_tool_calls.add(idx)
                                turn_out_chars += len(json.dumps(parsed_json))
                                
                                # Emit turn usage on tool execution trigger
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
                        except Exception:
                            pass

                elif step_type in (5, 7, 8, 9, 21, 23, 101, 132):
                    # Tool execution step: extract and emit tool result
                    result_str = extract_tool_result(fields, step_type)
                    if result_str:
                        prev = last_emitted_result.get(idx, "")
                        if result_str != prev:
                            _emit_archon_event_static(jsonl_path, "tool_result", content=result_str)
                            last_emitted_result[idx] = result_str
                            total_in_chars += len(result_str) - len(prev)

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
        """Headless Antigravity run with real-time SQLite polling matching PID in logs."""
        log.info(f"Agent model: {self.model} [antigravity] ({self.role or 'default'})")

        env = dict(os.environ)
        if env_overrides:
            env.update(env_overrides)

        bin_path = self.descriptor.raw.get("bin", "agy")

        if log_base is None:
            argv = [bin_path, "--dangerously-skip-permissions", "--print", prompt]
            if extra_args:
                argv.extend(extra_args)
            return subprocess.run(argv, cwd=cwd, env=env).returncode == 0

        jsonl = f"{log_base}.jsonl"
        _emit_session_start(jsonl, model=self.model, role=self.role)
        _emit_prompt(jsonl, prompt=prompt)

        # Track log files and folders for target matching and fallback
        log_path = Path("~/.gemini/antigravity-cli/cli.log").expanduser()
        conv_dir = Path("~/.gemini/antigravity-cli/conversations").expanduser()

        active_db = None
        conv_id = None
        monitor_thread = None
        stop_event = threading.Event()

        current_prompt = prompt
        use_resume = False
        ok = True

        try:
            while True:
                # Assemble argv for this run/resume
                argv = [bin_path, "--dangerously-skip-permissions"]
                if use_resume and conv_id:
                    argv.extend([
                        "--conversation", conv_id,
                        "--print", "All background tasks and subagents have completed. Please read their reports and proceed."
                    ])
                else:
                    argv.extend(["--print", current_prompt])

                if extra_args:
                    argv.extend(extra_args)

                initial_log_size = log_path.stat().st_size if log_path.exists() else 0
                existing_dbs = set(conv_dir.glob("*.db")) if conv_dir.exists() else set()

                process = subprocess.Popen(
                    argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd, env=env
                )
                pid = process.pid

                # Discover the newly created SQLite database file matching PID in logs
                if not active_db:
                    start_detect = time.time()
                    while time.time() - start_detect < 5.0:
                        if log_path.exists():
                            try:
                                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                                    f.seek(initial_log_size)
                                    content = f.read()
                                    for line in content.splitlines():
                                        if str(pid) in line and "Created conversation" in line:
                                            uuid_match = re.search(r"Created conversation ([a-f0-9-]+)", line)
                                            if uuid_match:
                                                conv_id = uuid_match.group(1)
                                                candidate = conv_dir / f"{conv_id}.db"
                                                if candidate.exists():
                                                    active_db = candidate
                                                    break
                            except Exception:
                                pass
                        if active_db:
                            break
                        time.sleep(0.1)

                    # Fallback: Scan directory for folders/new databases
                    if active_db is None and conv_dir.exists():
                        current_dbs = set(conv_dir.glob("*.db"))
                        new_dbs = current_dbs - existing_dbs
                        if new_dbs:
                            active_db = list(new_dbs)[0]
                            conv_id = active_db.stem
                        else:
                            all_dbs = list(conv_dir.glob("*.db"))
                            if all_dbs:
                                active_db = max(all_dbs, key=lambda p: p.stat().st_mtime)
                                conv_id = active_db.stem

                # Spawn database monitor thread (only once!)
                if active_db and not monitor_thread:
                    log.info(f"Monitoring Antigravity SQLite DB (PID {pid}): {active_db}")
                    monitor_thread = threading.Thread(
                        target=poll_antigravity_db,
                        args=(active_db, jsonl, stop_event, self.model, self.role),
                        daemon=True,
                    )
                    monitor_thread.start()

                # Read stdout to ensure process makes forward progress
                if process.stdout:
                    for line in process.stdout:
                        print(line, end="", flush=True)

                code = process.wait()
                if code != 0:
                    ok = False

                # Check if there are active subagents or background tasks
                if active_db:
                    had_active = False
                    start_wait = time.time()
                    while True:
                        active_subagents = False
                        try:
                            # Check for running archon-subagent.py processes matching this cwd
                            res = subprocess.run(["pgrep", "-f", "archon-subagent.py"], capture_output=True, text=True)
                            if res.returncode == 0:
                                pids = res.stdout.strip().split()
                                for p in pids:
                                    try:
                                        cmdline_path = Path(f"/proc/{p}/cmdline")
                                        if cmdline_path.exists():
                                            cmdline = cmdline_path.read_text().replace("\x00", " ")
                                            if str(cwd) in cmdline:
                                                active_subagents = True
                                                break
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                        active_db_tasks = False
                        try:
                            # Check steps table for status = 2
                            conn = sqlite3.connect(f"file:{active_db}?mode=ro", uri=True)
                            cur = conn.cursor()
                            cur.execute("SELECT COUNT(*) FROM steps WHERE status = 2")
                            count = cur.fetchone()[0]
                            cur.close()
                            conn.close()
                            if count > 0:
                                active_db_tasks = True
                        except Exception:
                            pass

                        if not active_subagents and not active_db_tasks:
                            break

                        had_active = True
                        # Print status periodically (every 30 seconds)
                        if int(time.time() - start_wait) % 30 == 0:
                            log.info("Waiting for background subagents or tasks to complete...")
                        time.sleep(2.0)

                    if had_active:
                        use_resume = True
                        continue

                # No active tasks/subagents remain, so finish
                break

        except Exception as e:
            log.error(f"Antigravity run failed: {e}")
            ok = False
        finally:
            if monitor_thread:
                stop_event.set()
                monitor_thread.join(timeout=1.0)

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

"""Tests for the Antigravity integration runner (``archon.agents.antigravity``)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from archon.agents.antigravity import (
    decode_varint,
    extract_protobuf_fields,
    extract_tool_result,
    poll_antigravity_db,
    AntigravityAgent,
)
from archon.commands.tooling.project_config import HarnessDescriptor


def encode_varint(val: int) -> bytes:
    res = bytearray()
    while True:
        b = val & 0x7f
        val >>= 7
        if val > 0:
            res.append(b | 0x80)
        else:
            res.append(b)
            break
    return bytes(res)


def encode_length_delimited(field_num: int, val: bytes) -> bytes:
    key = (field_num << 3) | 2
    return encode_varint(key) + encode_varint(len(val)) + val


class ProtobufParserTest(unittest.TestCase):
    def test_decode_varint(self):
        # 150 = 0x96 0x01
        self.assertEqual(decode_varint(b"\x96\x01", 0), (150, 2))
        self.assertEqual(decode_varint(b"\x08", 0), (8, 1))

    def test_extract_protobuf_fields(self):
        # Construct synthetic payload: field 20 containing field 1 ("thinking") and field 3 ("text")
        thinking_bytes = b"This is thinking."
        text_bytes = b"This is text."
        
        part1 = encode_length_delimited(1, thinking_bytes)
        part3 = encode_length_delimited(3, text_bytes)
        
        inner_content = part1 + part3
        payload = encode_length_delimited(20, inner_content)
        
        fields = extract_protobuf_fields(payload)
        
        self.assertEqual(fields.get("20"), inner_content)
        self.assertEqual(fields.get("20.1"), thinking_bytes)
        self.assertEqual(fields.get("20.3"), text_bytes)

    def test_extract_tool_result(self):
        # ViewFile (st=8) test: longest UTF-8 string not under 5.* is the file content
        fields = {
            "5.1": b"some call metadata",
            "14.1": b"file:///home/archon/README.md",
            "14.4": b"This is the actual file content.",
        }
        res = extract_tool_result(fields, step_type=8)
        self.assertEqual(res, "This is the actual file content.")

        # ListDir (st=9) test: gather child entries under 15.3
        fields = {
            "15.1": b"file:///home/archon/dir",
            "15.3.1": b"file1.txt",
            "15.3.2": b"file2.txt",
        }
        res = extract_tool_result(fields, step_type=9)
        self.assertEqual(res, "file1.txt\nfile2.txt")


class PollAntigravityDbTest(unittest.TestCase):
    def setUp(self):
        self.temp_db_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_db_dir.name) / "conv.db"
        self.jsonl_path = Path(self.temp_db_dir.name) / "events.jsonl"
        
        # Initialize SQLite DB
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE steps (
                idx INTEGER PRIMARY KEY,
                step_type INTEGER,
                status INTEGER,
                step_payload BLOB
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_db_dir.cleanup()

    def test_poll_antigravity_db(self):
        stop_event = threading.Event()
        
        # Start DB poller in background thread
        poller = threading.Thread(
            target=poll_antigravity_db,
            args=(self.db_path, str(self.jsonl_path), stop_event, "sonnet", "plan"),
            daemon=True,
        )
        poller.start()
        
        # Write step 15 (Assistant turn with thinking, text, and a tool call)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        # Prepare step 15 payload
        thinking = b"Thinking about the tool."
        text = b"Running a bash command now."
        tool_input = b'{"CommandLine": "echo hello", "Cwd": "/home"}'
        
        part_think = encode_length_delimited(1, thinking)
        part_text = encode_length_delimited(3, text)
        part_tool = encode_length_delimited(7, encode_length_delimited(3, tool_input))
        
        payload_15 = encode_length_delimited(20, part_think + part_text + part_tool)
        
        cur.execute(
            "INSERT INTO steps (idx, step_type, status, step_payload) VALUES (?, ?, ?, ?)",
            (0, 15, 2, payload_15)
        )
        conn.commit()
        
        # Wait for poller to capture step 15
        time.sleep(1.0)
        
        # Write step 21 (Bash execution result)
        cmd_output = b"hello\n"
        payload_21 = encode_length_delimited(28, encode_length_delimited(21, encode_length_delimited(1, cmd_output)))
        
        cur.execute(
            "INSERT INTO steps (idx, step_type, status, step_payload) VALUES (?, ?, ?, ?)",
            (1, 21, 3, payload_21)
        )
        conn.commit()
        
        # Wait for poller to capture step 21
        time.sleep(1.0)
        
        # Stop poller
        stop_event.set()
        poller.join(timeout=2.0)
        conn.close()
        
        # Read emitted events
        events = []
        with open(self.jsonl_path, "r") as f:
            for line in f:
                events.append(json.loads(line))
                
        # Validate events
        event_types = [ev.get("event") for ev in events]
        self.assertIn("thinking", event_types)
        self.assertIn("text", event_types)
        self.assertIn("tool_call", event_types)
        self.assertIn("tool_result", event_types)
        
        # Verify specific event content
        thinking_event = next(ev for ev in events if ev.get("event") == "thinking")
        self.assertEqual(thinking_event.get("content"), "Thinking about the tool.")
        
        text_event = next(ev for ev in events if ev.get("event") == "text")
        self.assertEqual(text_event.get("content"), "Running a bash command now.")
        
        tool_call_event = next(ev for ev in events if ev.get("event") == "tool_call")
        self.assertEqual(tool_call_event.get("tool"), "Bash")
        self.assertEqual(tool_call_event.get("input").get("CommandLine"), "echo hello")
        
        tool_result_event = next(ev for ev in events if ev.get("event") == "tool_result")
        self.assertEqual(tool_result_event.get("content"), "hello\n")

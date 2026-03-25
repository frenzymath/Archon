#!/usr/bin/env python3
"""
extract-attempts.py — Extract structured attempt data from agent logs.

Supports two log formats:
  - Archon's pre-parsed JSONL (event: "tool_call" / "tool_result")
  - Raw Claude Code stream-json (type: "assistant" / "user" with nested tool_use)

Usage:
    python3 extract-attempts.py <agent_log.jsonl> [output.jsonl]

Output: one JSON line per relevant event, suitable for review agent consumption.
"""

import json
import sys
from pathlib import Path


def parse_jsonl(log_path: str):
    """Parse JSONL log, yielding (line_num, obj) tuples."""
    with open(log_path, 'r', errors='replace') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('==='):
                continue
            try:
                obj = json.loads(line)
                yield line_num, obj
            except json.JSONDecodeError:
                continue


def detect_format(log_path: str) -> str:
    """Detect whether the log is Archon pre-parsed or raw stream-json."""
    for _, obj in parse_jsonl(log_path):
        if 'event' in obj:
            return 'archon'
        if 'type' in obj and obj['type'] in ('assistant', 'user', 'result'):
            return 'stream-json'
    return 'archon'


def extract_tool_calls_archon(log_path: str):
    """Extract from Archon's pre-parsed JSONL format."""
    events = []
    pending_call = None

    for line_num, obj in parse_jsonl(log_path):
        event_type = obj.get('event', '')

        if event_type == 'tool_call':
            if pending_call:
                events.append(pending_call)
            pending_call = {
                'ts': obj.get('ts', ''),
                'tool': obj.get('tool', ''),
                'input': obj.get('input', {}),
                'result': '',
                'log_line': line_num,
            }

        elif event_type == 'tool_result' and pending_call:
            pending_call['result'] = truncate_str(obj.get('content', ''), 2000)
            events.append(pending_call)
            pending_call = None

    if pending_call:
        events.append(pending_call)

    return events


def extract_tool_calls_stream_json(log_path: str):
    """Extract from raw Claude Code stream-json format."""
    pending_tools = {}
    events = []

    for line_num, obj in parse_jsonl(log_path):
        msg_type = obj.get('type')

        if msg_type == 'assistant':
            msg = obj.get('message', {})
            if not isinstance(msg, dict):
                continue
            for item in msg.get('content', []):
                if isinstance(item, dict) and item.get('type') == 'tool_use':
                    tool_id = item.get('id', '')
                    pending_tools[tool_id] = {
                        'tool': item.get('name', ''),
                        'input': item.get('input', {}),
                        'line_num': line_num,
                    }

        elif msg_type == 'user':
            msg = obj.get('message', {})
            if not isinstance(msg, dict):
                continue
            timestamp = obj.get('timestamp', '')
            for item in msg.get('content', []):
                if isinstance(item, dict) and item.get('type') == 'tool_result':
                    tool_id = item.get('tool_use_id', '')
                    result_content = item.get('content', '')
                    tool_info = pending_tools.pop(tool_id, None)
                    if tool_info is None:
                        continue
                    events.append({
                        'ts': timestamp,
                        'tool': tool_info['tool'],
                        'input': tool_info['input'],
                        'result': truncate_str(str(result_content), 2000),
                        'log_line': tool_info['line_num'],
                    })

    return events


def truncate_str(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len - 50] + f'\n... [truncated, {len(s)} total chars]'


def classify_event(event: dict) -> dict:
    """Classify and enrich an event based on tool type."""
    tool = event['tool']
    inp = event['input']
    result = event.get('result', '')

    out = {
        'ts': event.get('ts', ''),
        'tool': tool,
        'log_line': event.get('log_line', 0),
    }

    if tool == 'Edit':
        out['type'] = 'code_change'
        out['file'] = inp.get('file_path', '')
        out['old_text'] = truncate_str(inp.get('old_string', inp.get('oldText', '')), 500)
        out['new_text'] = truncate_str(inp.get('new_string', inp.get('newText', '')), 500)

    elif tool == 'Write':
        out['type'] = 'code_write'
        out['file'] = inp.get('file_path', '')
        out['content_preview'] = truncate_str(inp.get('content', ''), 500)

    elif tool == 'Read':
        out['type'] = 'file_read'
        out['file'] = inp.get('file_path', '')
        return out

    elif tool == 'Bash':
        cmd = inp.get('command', '')
        out['type'] = 'bash'
        out['command'] = truncate_str(cmd, 500)
        if 'lake' in cmd or 'lean' in cmd:
            out['subtype'] = 'build'
        elif 'grep' in cmd or 'rg ' in cmd:
            out['subtype'] = 'search'
        else:
            out['subtype'] = 'other'
        if 'error' in result.lower() or 'failed' in result.lower():
            out['has_errors'] = True
            out['result_preview'] = truncate_str(result, 1000)
        else:
            out['result_preview'] = truncate_str(result, 300)

    elif tool.startswith('mcp__lean-lsp__') or tool.startswith('mcp__lean_lsp__'):
        lean_tool = tool.split('__', 2)[-1] if '__' in tool else tool
        out['lean_tool'] = lean_tool

        if lean_tool == 'lean_goal':
            out['type'] = 'goal_state'
            out['file'] = inp.get('file_path', '')
            out['line'] = inp.get('line', 0)
            out['column'] = inp.get('column', 0)
            out['goal'] = truncate_str(result, 1500)

        elif lean_tool == 'lean_diagnostic_messages':
            out['type'] = 'diagnostics'
            out['file'] = inp.get('file_path', '')
            errors = []
            warnings = []
            for line in result.split('\n'):
                if 'error' in line.lower():
                    errors.append(line.strip()[:300])
                elif 'warning' in line.lower():
                    warnings.append(line.strip()[:200])
            out['errors'] = errors[:10]
            out['warnings'] = warnings[:5]
            out['error_count'] = len(errors)
            out['warning_count'] = len(warnings)
            if not errors and not warnings:
                out['clean'] = True

        elif lean_tool in ('lean_leansearch', 'lean_loogle', 'lean_local_search', 'lean_leanfinder'):
            out['type'] = 'lemma_search'
            out['query'] = truncate_str(str(inp.get('query', inp.get('search_string', ''))), 200)
            out['results_preview'] = truncate_str(result, 500)

        elif lean_tool == 'lean_build':
            out['type'] = 'build'
            out['result_preview'] = truncate_str(result, 1000)
            out['has_errors'] = 'error' in result.lower()

        else:
            out['type'] = 'lean_other'
            out['lean_tool'] = lean_tool
            out['result_preview'] = truncate_str(result, 300)

    elif tool in ('Glob', 'Grep'):
        out['type'] = 'search'
        out['pattern'] = inp.get('pattern', inp.get('query', ''))
        return out

    else:
        out['type'] = 'other'
        out['result_preview'] = truncate_str(result, 200)

    return out


def generate_summary_stats(events: list) -> dict:
    """Generate summary statistics from events."""
    stats = {
        'total_events': len(events),
        'edits': 0,
        'goal_checks': 0,
        'diagnostic_checks': 0,
        'builds': 0,
        'lemma_searches': 0,
        'files_edited': set(),
        'files_read': set(),
        'total_errors': 0,
        'clean_diagnostics': 0,
    }

    for ev in events:
        t = ev.get('type', '')
        if t == 'code_change':
            stats['edits'] += 1
            stats['files_edited'].add(ev.get('file', ''))
        elif t == 'goal_state':
            stats['goal_checks'] += 1
        elif t == 'diagnostics':
            stats['diagnostic_checks'] += 1
            stats['total_errors'] += ev.get('error_count', 0)
            if ev.get('clean'):
                stats['clean_diagnostics'] += 1
        elif t == 'build':
            stats['builds'] += 1
        elif t == 'lemma_search':
            stats['lemma_searches'] += 1
        elif t == 'file_read':
            stats['files_read'].add(ev.get('file', ''))

    stats['files_edited'] = sorted(stats['files_edited'])
    stats['files_read'] = sorted(stats['files_read'])
    return stats


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <agent_log.jsonl> [output.jsonl]", file=sys.stderr)
        sys.exit(1)

    log_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    fmt = detect_format(log_path)
    if fmt == 'archon':
        raw_events = extract_tool_calls_archon(log_path)
    else:
        raw_events = extract_tool_calls_stream_json(log_path)

    classified = [c for ev in raw_events if (c := classify_event(ev))]

    interesting = []
    for ev in classified:
        if ev.get('type') == 'file_read':
            if ev.get('file', '').endswith('.lean'):
                interesting.append(ev)
        else:
            interesting.append(ev)

    stats = generate_summary_stats(interesting)

    output_lines = [json.dumps({'type': 'summary', **stats}, ensure_ascii=False)]
    for ev in interesting:
        output_lines.append(json.dumps(ev, ensure_ascii=False))

    output_text = '\n'.join(output_lines) + '\n'

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(output_text)
        print(f"Extracted {len(interesting)} events ({stats['edits']} edits, "
              f"{stats['goal_checks']} goals, {stats['total_errors']} errors) → {output_path}",
              file=sys.stderr)
    else:
        sys.stdout.write(output_text)
        print(f"\nExtracted {len(interesting)} events", file=sys.stderr)


if __name__ == '__main__':
    main()

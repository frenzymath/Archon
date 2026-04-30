"""Claude Code runner with structured JSONL logging.

Wraps `claude -p` with stream-json parsing, cost tracking, and log output.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
import os

from archon import log


# ── prompt building ───────────────────────────────────────────────────


def build_plan_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
) -> str:
    return dedent(f"""\
        You are the plan agent for project '{project_name}'. Current stage: {stage}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/plan.md and {state_dir}/PROGRESS.md.
        All state files (PROGRESS.md, task_pending.md, task_done.md, USER_HINTS.md, task_results/) are in {state_dir}/.
        The .lean files are in {project_path}/.""")


def build_prover_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
) -> str:
    return dedent(f"""\
        You are the prover agent for project '{project_name}'. Current stage: {stage}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/prover-{stage}.md and {state_dir}/PROGRESS.md.
        All state files are in {state_dir}/. The .lean files are in {project_path}/.""")


def build_parallel_prover_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
) -> str:
    return dedent(f"""\
        You are a prover agent for project '{project_name}'. Current stage: {stage}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/prover-{stage}.md and {state_dir}/PROGRESS.md.
        Check your .lean file for /- USER: ... -/ comments for file-specific hints.

        IMPORTANT:
        - You own ONLY the file assigned below. Do NOT edit any other .lean file.
        - Write your results to {state_dir}/task_results/<your_file>.md when done.
        - Do NOT edit PROGRESS.md, task_pending.md, or task_done.md.
        - Missing Mathlib infrastructure is NEVER a valid reason to leave a sorry.
        - NEVER revert to a bare sorry. Always leave your partial proof attempt in the code.""")


def build_refactor_prompt(
    project_name: str, project_path: Path, state_dir: Path,
    directive: str,
) -> str:
    return dedent(f"""\
        You are the refactor agent for project '{project_name}'.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for project context, then read {state_dir}/prompts/refactor.md.

        DIRECTIVE FROM PLAN AGENT:
        {directive}

        Execute this directive. Keep all files compiling (insert sorry at broken proof sites).
        Document every change in {state_dir}/task_results/refactor.md.""")


def build_review_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
    session_num: int, session_dir: Path, attempts_file: Path,
    combined_prover_log: Path,
) -> str:
    return dedent(f"""\
        You are the review agent for project '{project_name}'. Current stage: {stage}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/review.md.
        Session number: {session_num}.
        Pre-processed attempt data: {attempts_file} (READ THIS FIRST).
        Prover log: {combined_prover_log}

        CRITICAL — Write your output files to EXACTLY these paths:
          {session_dir}/milestones.jsonl
          {session_dir}/summary.md
          {session_dir}/recommendations.md
          {state_dir}/PROJECT_STATUS.md""")


# ── JSONL log stream parser (embedded Python script) ──────────────────

# This is the Python script that gets piped Claude's stream-json output.
# It parses events, writes structured JSONL, and prints cost summaries.
_STREAM_PARSER = r'''
import sys, json, datetime

VERBOSE = '{verbose}' == 'True'
RAW = open('{raw_log}', 'a') if VERBOSE else None
JSONL = open('{jsonl}', 'a')

def emit(event_type, **fields):
    row = {{'ts': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), 'event': event_type, **fields}}
    JSONL.write(json.dumps(row) + '\n')
    JSONL.flush()

def terminal(s):
    print(s, flush=True)

last_result = ''

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
            elif bt == 'text':
                text = block.get('text', '').strip()
                if text:
                    emit('text', content=text)
                    last_result = text
            elif bt == 'tool_use':
                name = block.get('name', '?')
                inp = block.get('input', {{}})
                emit('tool_call', tool=name, input=inp)

    elif t == 'user' and 'message' in obj:
        msg = obj['message']
        if not isinstance(msg, dict):
            continue
        for block in msg.get('content', []):
            if block.get('type') == 'tool_result':
                content = block.get('content', '')
                if isinstance(content, str):
                    emit('tool_result', content=content)
                elif isinstance(content, list):
                    texts = [p.get('text','') for p in content if isinstance(p,dict) and p.get('type')=='text']
                    emit('tool_result', content='\n'.join(texts))

    elif t == 'result':
        cost = obj.get('total_cost_usd', 0) or obj.get('cost_usd', 0) or 0
        duration = obj.get('duration_ms', 0) or 0
        turns = obj.get('num_turns', 0) or 0
        session_id = obj.get('session_id', '') or ''
        result = obj.get('result', '')
        usage = obj.get('usage', {{}}) or {{}}
        model_usage = obj.get('modelUsage', {{}}) or {{}}
        summary = result if isinstance(result, str) and result else last_result

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


# ── run_claude ────────────────────────────────────────────────────────


def run_claude(
    prompt: str,
    *,
    cwd: Path,
    log_base: Path | None = None,
    verbose_logs: bool = False,
    extra_args: list[str] | None = None,
) -> bool:
    """Run `claude -p` with optional JSONL logging.

    Args:
        prompt: The prompt string to pass to Claude.
        cwd: Working directory (project path).
        log_base: If provided, enables JSONL logging to {log_base}.jsonl
                  (and optionally {log_base}.raw.jsonl).
        verbose_logs: If True, also write raw stream events.
        extra_args: Additional arguments to pass to claude.

    Returns:
        True if claude exited successfully, False otherwise.
    """
    claude_cmd = [
        "claude", "-p", prompt,
        "--dangerously-skip-permissions", "--permission-mode", "bypassPermissions",
    ]
    if extra_args:
        claude_cmd.extend(extra_args)

    if log_base is not None:
        log_base.parent.mkdir(parents=True, exist_ok=True)
        jsonl = f"{log_base}.jsonl"
        raw_log = f"{log_base}.raw.jsonl"

        claude_cmd.extend(["--verbose", "--output-format", "stream-json"])

        stderr_dest = raw_log if verbose_logs else os.devnull

        parser_script = _STREAM_PARSER.format(
            verbose=str(verbose_logs),
            raw_log=raw_log,
            jsonl=jsonl,
        )

        with open(stderr_dest, "a") as stderr_file:
            claude_proc = subprocess.Popen(
                claude_cmd,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                cwd=cwd,
            )
            parser_proc = subprocess.Popen(
                [sys.executable, "-u", "-c", parser_script],
                stdin=claude_proc.stdout,
                cwd=cwd,
            )
            claude_proc.stdout.close()  # allow SIGPIPE
            parser_proc.wait()
            claude_proc.wait()

        return claude_proc.returncode == 0
    else:
        r = subprocess.run(claude_cmd, cwd=cwd)
        return r.returncode == 0
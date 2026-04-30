"""Anthropic↔LiteLLM proxy bundled with Archon.

Lanes whose provider is ``openai`` or ``gemini`` route Claude Code
through this proxy: it accepts requests in Anthropic API shape and
translates them to LiteLLM (OpenAI / Gemini) calls. Each lane gets
its own proxy subprocess on a free port; ``ANTHROPIC_BASE_URL`` for
the lane points at ``http://127.0.0.1:<port>``.

The heavy dependencies (fastapi, uvicorn, litellm) are guarded behind
the ``proxy`` extras group:

    pip install archon[proxy]

so a default install stays small. Direct-API providers (kimi,
deepseek) don't need the proxy because they already speak the
Anthropic API.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def find_free_port() -> int:
    """Bind 127.0.0.1:0 to grab an OS-assigned free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def proxy_module() -> str:
    """Module path for `python -m`. Kept here so callers don't have to remember it."""
    return 'archon.proxy.server'


def start_proxy(
    *,
    port: int,
    env: dict[str, str],
    log_path: Path,
) -> subprocess.Popen:
    """Spawn the proxy as a subprocess. Returns the Popen handle.

    Stdout+stderr go to ``log_path`` so you can tail it from the
    dashboard. The caller is responsible for killing the process at
    end of the multilane round (see :func:`stop_proxy`).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    full_env = os.environ.copy()
    full_env.update(env)
    full_env['ARCHON_PROXY_PORT'] = str(port)
    log_fp = open(log_path, 'a', buffering=1)
    log_fp.write(f"\n--- proxy starting on port {port} at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_fp.flush()
    return subprocess.Popen(
        [sys.executable, '-m', proxy_module()],
        env=full_env,
        stdout=log_fp,
        stderr=log_fp,
        # New session so a Ctrl+C in the parent doesn't double-signal.
        start_new_session=True,
    )


def wait_for_proxy_ready(port: int, *, timeout: float = 15.0) -> bool:
    """Poll http://127.0.0.1:<port>/ until it answers or we time out."""
    deadline = time.monotonic() + timeout
    url = f'http://127.0.0.1:{port}/'
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as r:
                if r.status < 500:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.25)
    return False


def stop_proxy(proc: subprocess.Popen, *, timeout: float = 5.0) -> None:
    """Terminate the proxy, then SIGKILL after ``timeout`` if it lingers."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
    except Exception:
        pass


__all__ = [
    'find_free_port',
    'proxy_module',
    'start_proxy',
    'wait_for_proxy_ready',
    'stop_proxy',
]

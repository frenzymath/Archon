"""Process control helpers: signal a PID's process group, poll for exit, identify ours."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


def signal_pid_or_group(pid: int, sig: int) -> None:
    """Send `sig` to the entire process group of `pid`.

    Servers spawned with `start_new_session=True` are session leaders,
    so `getpgid(pid) == pid` and `killpg` reaches every child the
    server forked. Without process-group signalling, terminating the
    parent leaves grandchildren running and can hold the port open.
    Falls back to single-PID `kill` if the process is not a session leader.
    """
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = pid
    try:
        os.killpg(pgid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def wait_for_exit(pid: int, timeout: float, interval: float = 0.1) -> bool:
    """Poll until `pid` no longer exists or `timeout` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(interval)
    try:
        os.kill(pid, 0)
        return False
    except OSError:
        return True


def is_archon_ui_pid(pid: int) -> bool:
    """Best-effort: confirm `pid` is actually an archon UI server.

    Guards against killing an unrelated process when the OS has reused
    the PID of a previous dashboard. Falls back to True (proceed with
    the kill) if we can't introspect the process — better to be
    slightly aggressive on platforms without /proc than to leave a
    stale server running.
    """
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.exists():
        try:
            cmd = proc_cmdline.read_text(errors="replace").lower()
        except OSError:
            return True
        return any(tok in cmd for tok in ("tsx", "src/index.ts", "archon-ui"))
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode != 0:
            return False  # process doesn't exist
        cmd = r.stdout.strip().lower()
        return any(tok in cmd for tok in ("tsx", "src/index.ts", "archon-ui", "node"))
    except (OSError, subprocess.TimeoutExpired):
        return True

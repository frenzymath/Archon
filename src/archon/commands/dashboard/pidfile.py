"""Pid-file registry keyed by (project_path, port).

Two dashboards for the same project on different ports therefore have
distinct pid files — cleanup of one cannot trample the other's record.
The previous scheme used one pid file per project, which forced each
new `archon dashboard` invocation to either kill the previous instance
or leak its record.
"""

from __future__ import annotations

import hashlib
import os
import signal
from pathlib import Path

from archon import log

from .process import is_archon_ui_pid, signal_pid_or_group, wait_for_exit


def project_key(project_path: str) -> str:
    """Short hash key for a project path (matches bash `shasum -a 256`)."""
    return hashlib.sha256(project_path.encode()).hexdigest()[:16]


class PidFileRegistry:
    """Manages dashboard pid files for one project under `<ui_dir>/.archon-ui/`."""

    def __init__(self, instance_dir: Path, project_path: Path) -> None:
        self.instance_dir = instance_dir
        self.project_key = project_key(str(project_path))
        instance_dir.mkdir(parents=True, exist_ok=True)

    def pidfile_for(self, port: int) -> Path:
        return self.instance_dir / f"{self.project_key}-{port}.pid"

    def project_pidfiles(self) -> list[Path]:
        """All pid files belonging to this project (any port)."""
        return sorted(self.instance_dir.glob(f"{self.project_key}-*.pid"))

    def clean_stale(self) -> None:
        """Remove pid files whose recorded PID is no longer alive.

        Live instances are left alone — they represent dashboards the
        user deliberately started in another terminal. Without this
        distinction we'd silently kill the user's other working
        dashboards every time they ran `archon dashboard` again.
        """
        for pf in self.project_pidfiles():
            try:
                pid = int(pf.read_text().strip())
            except (ValueError, OSError):
                pf.unlink(missing_ok=True)
                continue
            try:
                os.kill(pid, 0)
            except OSError:
                pf.unlink(missing_ok=True)

    def kill_recorded_instance(self, pid_file: Path) -> bool:
        """Stop the instance recorded in `pid_file` (used by `--restart`).

        Sends SIGTERM to the server's process group, waits up to 5s for
        graceful Fastify shutdown, then escalates to SIGKILL. Returns
        True if something was actually killed. Skips if the recorded
        PID is stale or no longer looks like an archon UI server.
        """
        if not pid_file.exists():
            return False
        try:
            old_pid = int(pid_file.read_text().strip())
        except (ValueError, OSError):
            pid_file.unlink(missing_ok=True)
            return False

        try:
            os.kill(old_pid, 0)
        except OSError:
            pid_file.unlink(missing_ok=True)
            return False

        if not is_archon_ui_pid(old_pid):
            log.warn(
                f"PID {old_pid} no longer looks like an archon UI server "
                "— leaving it alone",
            )
            pid_file.unlink(missing_ok=True)
            return False

        log.step(f"Stopping previous UI server for this project (PID {old_pid})...")
        signal_pid_or_group(old_pid, signal.SIGTERM)
        if not wait_for_exit(old_pid, timeout=5.0):
            log.warn(f"PID {old_pid} did not exit after SIGTERM, forcing stop...")
            signal_pid_or_group(old_pid, signal.SIGKILL)
            wait_for_exit(old_pid, timeout=3.0)
        pid_file.unlink(missing_ok=True)
        return True

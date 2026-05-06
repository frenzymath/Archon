"""Spawn / supervise the Node dashboard server.

Handles:
  - Picking a free port (with retry on TOCTOU bind races)
  - Killing the server's process group on shutdown
  - Forwarding SIGTERM so the listening port releases cleanly
"""

from __future__ import annotations

import atexit
import platform
import signal
import subprocess
from pathlib import Path

import typer

from archon import log

from .pidfile import PidFileRegistry
from .port import find_free_port, port_in_use, wait_for_http
from .process import signal_pid_or_group, wait_for_exit


_MAX_PORT_ATTEMPTS = 8


class ServerProcess:
    """Lifecycle wrapper for the Node dashboard's server subprocess."""

    def __init__(
        self,
        *,
        server_dir: Path,
        project_path: Path,
        archon_dir: Path,
        registry: PidFileRegistry,
    ) -> None:
        self.server_dir = server_dir
        self.project_path = project_path
        self.archon_dir = archon_dir
        self.registry = registry
        self.proc: subprocess.Popen | None = None
        self.port: int = 0
        self.pid_file: Path | None = None
        self._cleanup_done = False
        self._spawn_kwargs: dict = {"cwd": server_dir}
        if platform.system() != "Windows":
            # `start_new_session=True` makes the Node server a session
            # leader so we can kill the whole tree on shutdown —
            # terminating just the parent PID would leave any children
            # holding the listening socket.
            self._spawn_kwargs["start_new_session"] = True

    def spawn(self, port: int) -> subprocess.Popen:
        cmd = [
            "node", "--import", "tsx",
            "src/index.ts", "--project", str(self.project_path), "--port", str(port),
        ]
        return subprocess.Popen(cmd, **self._spawn_kwargs)

    def shutdown(self, term_timeout: float = 5.0) -> None:
        """Bring down the server cleanly: SIGTERM, wait, SIGKILL."""
        if self.proc is None or self.proc.poll() is not None:
            return
        signal_pid_or_group(self.proc.pid, signal.SIGTERM)
        if not wait_for_exit(self.proc.pid, timeout=term_timeout):
            signal_pid_or_group(self.proc.pid, signal.SIGKILL)
            wait_for_exit(self.proc.pid, timeout=2.0)

    def start_with_retry(self, requested_port: int) -> int:
        """Bring the server up, advancing port on bind races. Returns final port."""
        port = self._resolve_initial_port(requested_port)
        self.port = port
        self.pid_file = self.registry.pidfile_for(port)
        self.proc = self.spawn(port)
        self.pid_file.write_text(str(self.proc.pid))

        expected_archon_path = str(self.archon_dir)
        for attempt in range(_MAX_PORT_ATTEMPTS):
            # Cold starts (tsx loader + module resolution + first build
            # cache miss) can take well over 4s on slow filesystems.
            # First attempt gets extra headroom so we don't kill a
            # healthy-but-slow server and churn through ports.
            wait_timeout = 12.0 if attempt == 0 else 4.0
            if wait_for_http(port, expected_archon_path, timeout=wait_timeout):
                return port
            self._handle_failed_attempt(port, wait_timeout)

            next_port = find_free_port(port)
            if next_port is None:
                log.error(f"Could not find a free port in range {port + 1}–{port + 11}")
                self.pid_file.unlink(missing_ok=True)
                raise typer.Exit(1)
            self.pid_file.unlink(missing_ok=True)
            port = next_port
            self.port = port
            self.pid_file = self.registry.pidfile_for(port)
            self.proc = self.spawn(port)
            self.pid_file.write_text(str(self.proc.pid))

        log.error(f"Server did not start after {_MAX_PORT_ATTEMPTS} port attempts")
        self.shutdown(term_timeout=2.0)
        self.pid_file.unlink(missing_ok=True)
        raise typer.Exit(1)

    def cleanup(self) -> None:
        """Idempotent shutdown — safe to call from atexit + signal handler + finally."""
        if self._cleanup_done:
            return
        self._cleanup_done = True
        self.shutdown(term_timeout=5.0)
        if self.pid_file is not None:
            self.pid_file.unlink(missing_ok=True)
        log.info("Dashboard stopped")

    def install_signal_handlers(self) -> None:
        atexit.register(self.cleanup)

        def _on_term(signum, frame):
            self.cleanup()
            raise SystemExit(128 + signum)

        # Forward SIGTERM (e.g. `kill <python-pid>` or a parent shell
        # exiting) to the server's process group before we exit, so the
        # listening port releases cleanly even when we aren't dying via
        # Ctrl+C.
        try:
            signal.signal(signal.SIGTERM, _on_term)
        except (ValueError, OSError):
            # Not on the main thread, or platform doesn't support it.
            pass

    # ── private ────────────────────────────────────────────────────────

    def _resolve_initial_port(self, requested_port: int) -> int:
        """Bind-test the requested port; advance to the next free one if busy."""
        if not port_in_use(requested_port):
            return requested_port
        log.warn(f"Port {requested_port} is already in use by another process or project")
        free_port = find_free_port(requested_port)
        if free_port is None:
            log.error(
                f"Could not find a free port in range "
                f"{requested_port + 1}–{requested_port + 11}",
            )
            log.step("Free the current port or pass an explicit --port")
            raise typer.Exit(1)
        log.panel(f"Port changed! Using [bold]{free_port}[/bold] instead", style="yellow")
        return free_port

    def _handle_failed_attempt(self, port: int, wait_timeout: float) -> None:
        """Log + clean up the dead/slow server before advancing to the next port."""
        died = self.proc is not None and self.proc.poll() is not None
        if died:
            log.warn(
                f"Port {port} was taken (likely by a parallel dashboard). "
                "Trying the next port…",
            )
        else:
            log.warn(
                f"Server on port {port} did not respond within {wait_timeout:g}s. "
                "Restarting on the next port…",
            )
            self.shutdown(term_timeout=2.0)

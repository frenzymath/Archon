"""Background services launched alongside the loop: dashboard + blueprint server.

Both are best-effort: a failure to bind a port, missing Node.js, or an
unbuilt blueprint just degrades gracefully — the loop continues.
"""

from __future__ import annotations

import atexit
import os
import shutil
import signal
import socket
import subprocess
import time
import webbrowser
from pathlib import Path

from archon import log
from archon.commands.tooling.blueprint import BlueprintServer


def install_signal_exit(*signal_names: str) -> None:
    """Convert terminating signals into ``SystemExit`` so ``atexit`` runs.

    Background services (the dashboard / blueprint server) are stopped by
    ``atexit``-registered cleanups. But ``atexit`` does NOT run when the
    process is killed by a default-disposition signal — and **SIGHUP**
    (closing the terminal, or an SSH session dropping) is exactly that.
    Because the child servers are spawned ``start_new_session=True`` (their
    own session), they don't receive the terminal's SIGHUP either, so they
    orphan and keep holding their listening ports. Re-raising the signal as
    ``SystemExit`` lets the registered cleanups fire and the ports release.

    Defaults to SIGHUP + SIGTERM (SIGINT already raises KeyboardInterrupt,
    which runs atexit on its own). Only installs over a default/ignored
    handler so a caller's existing handler is never clobbered. No-op off the
    main thread or where a signal is unsupported (e.g. SIGHUP on Windows).
    """
    names = signal_names or ("SIGHUP", "SIGTERM")

    def _raise_exit(signum, _frame):
        raise SystemExit(128 + signum)

    for name in names:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            if signal.getsignal(sig) in (signal.SIG_DFL, signal.SIG_IGN, None):
                signal.signal(sig, _raise_exit)
        except (ValueError, OSError):
            pass


class PortProbe:
    """Find an unbound TCP port to spawn a local service on."""

    @staticmethod
    def is_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) != 0

    @classmethod
    def find_free(cls, start: int = 8080, attempts: int = 20) -> int | None:
        for p in range(start, start + attempts):
            if cls.is_free(p):
                return p
        return None


class DashboardProcess:
    """Launches `archon dashboard <project>` in the background and waits for
    its port to bind. The child is always cleaned up with the parent process
    via `atexit`, including when startup takes longer than the initial probe.
    """

    def __init__(self, project_path: Path, *, open_browser: bool = False) -> None:
        self.project_path = project_path
        self.open_browser = open_browser
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.url: str | None = None

    def start(self) -> str | None:
        """Start the dashboard. Returns the URL, or None on any failure."""
        if not shutil.which("node") or not shutil.which("npm"):
            log.warn("Dashboard skipped: Node.js / npm not found (run: archon setup)")
            return None

        port = PortProbe.find_free(8080)
        if port is None:
            log.warn("Dashboard skipped: could not find a free port in 8080–8099")
            return None

        log_path = self.project_path / ".archon" / "logs" / "dashboard.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # The parent reports this URL even when startup outlasts the initial
        # readiness probe, so the child must not silently retry on another port.
        cmd = [
            "archon",
            "dashboard",
            str(self.project_path),
            "--port",
            str(port),
            "--strict-port",
        ]
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=open(log_path, "a", buffering=1),
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception as e:
            log.warn(f"Dashboard failed to start: {e}")
            return None

        # Register cleanup as soon as the child exists.  The dashboard can
        # legitimately take longer than the readiness probe (for example on a
        # cold npm/tsx start); it must still be reaped if the loop exits while
        # that child is finishing startup.
        atexit.register(self._cleanup)

        ready = self._wait_for_port(port, log_path)
        if not ready and self.proc.poll() is not None:
            return None

        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        if not ready:
            log.warn(
                f"Dashboard is still starting at {self.url}; "
                "the loop will continue and clean it up on exit."
            )
        log.panel(
            (
                f"Dashboard is live at [bold cyan]{self.url}[/bold cyan]"
                if ready
                else f"Dashboard is starting at [bold cyan]{self.url}[/bold cyan]"
            )
            + "\nWatch iterations, parallel provers, diffs, and the proof journal update live.",
            title="Archon Dashboard",
            style="cyan",
        )

        if self.open_browser:
            try:
                webbrowser.open(self.url)
            except Exception:
                pass

        return self.url

    def _wait_for_port(self, port: int, log_path: Path) -> bool:
        # Poll fast so a quick build failure doesn't hide behind a long wait.
        # Total budget ≈ 8 s. We probe the process first because a crashed
        # proc means the port will never bind, no matter how long we sleep.
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                log.warn(
                    f"Dashboard process exited before binding its port "
                    f"(exit code {self.proc.returncode}). See {log_path}."
                )
                return False
            if not PortProbe.is_free(port):
                return True
            time.sleep(0.1)

        log.warn(
            f"Dashboard did not bind port {port} within 8s; continuing while "
            f"it finishes startup. See {log_path} for details."
        )
        return False

    def _cleanup(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except Exception:
            try:
                self.proc.terminate()
            except Exception:
                pass


class BlueprintServerProcess:
    """Wraps a `BlueprintServer`. Starts immediately if `blueprint/web/`
    exists; otherwise waits for the first finalize step to build it,
    then `try_start_deferred` hooks it up.
    """

    def __init__(self, project_path: Path) -> None:
        self.server = BlueprintServer(project_path)
        self.url: str | None = None

    @property
    def available(self) -> bool:
        return self.server.available

    def start(self) -> str | None:
        if not self.server.available:
            log.info(
                "Blueprint server deferred: blueprint/web/ not built yet — "
                "will try again after the first iteration's finalize step."
            )
            return None

        proc, port = self.server.start()
        if proc is None or port is None:
            log.warn("Blueprint server failed to start")
            return None

        self.url = f"http://localhost:{port}"
        log.panel(
            f"Blueprint preview at [bold cyan]{self.url}[/bold cyan]\n"
            f"Serves the HTML rendering of blueprint/ — refreshes on each iteration's "
            f"`leanblueprint web` build.",
            title="Blueprint",
            style="cyan",
        )
        atexit.register(self.server.stop)
        return self.url

    def try_start_deferred(self) -> str | None:
        """Re-attempt start after a finalize step. Idempotent — once started,
        further calls are no-ops.
        """
        if self.url is not None:
            return self.url
        if not self.server.available:
            return None

        proc, port = self.server.start()
        if proc is None or port is None:
            return None
        self.url = f"http://localhost:{port}"
        log.panel(
            f"Blueprint preview at [bold cyan]{self.url}[/bold cyan]\n"
            f"First `leanblueprint web` build completed — server is live.",
            title="Blueprint",
            style="cyan",
        )
        atexit.register(self.server.stop)
        return self.url

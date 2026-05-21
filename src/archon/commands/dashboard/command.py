"""DashboardCommand: orchestrates `archon dashboard`."""

from __future__ import annotations

import atexit
import subprocess
import webbrowser
from importlib import resources
from pathlib import Path

import typer

from archon import log

from .build import build_client, check_node, install_if_needed, needs_build
from .pidfile import PidFileRegistry
from .server import ServerProcess


def _data_path(sub_path: str = "") -> Path:
    root = resources.files("archon")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


class DashboardCommand:
    """Runs the dashboard server (production or dev mode)."""

    def __init__(
        self,
        project_path: str,
        *,
        port: int = 8080,
        dev: bool = False,
        build_only: bool = False,
        open_browser: bool = False,
        restart: bool = False,
    ) -> None:
        self.project_path = project_path
        self.port = port
        self.dev = dev
        self.build_only = build_only
        self.open_browser = open_browser
        self.restart = restart

    def run(self) -> None:
        resolved = Path(self.project_path).resolve()
        archon_dir = resolved / ".archon"
        if not archon_dir.is_dir():
            log.error(f"No .archon/ directory found in {resolved}")
            log.step("Run: archon init first, or check the project path")
            raise typer.Exit(1)

        ui_dir = _data_path("ui")
        if not ui_dir.exists():
            log.error("UI files not found in package data — installation may be incomplete")
            raise typer.Exit(1)

        server_dir = ui_dir / "server"
        client_dir = ui_dir / "client"
        registry = PidFileRegistry(ui_dir / ".archon-ui", resolved)

        log.key_value({
            "Project": str(resolved),
            "Port": str(self.port),
            "Mode": "dev" if self.dev else "production",
        })

        check_node()
        install_if_needed(server_dir, "server")
        install_if_needed(client_dir, "client")

        if not self.dev:
            if needs_build(client_dir):
                log.step("Building client...")
                build_client(client_dir)
            else:
                log.success("Client up to date")

        if self.build_only:
            log.success("Build complete")
            return

        # Drop pid files for processes that have since died. Live
        # instances (the user's other dashboards in other terminals)
        # are intentionally left alone — running `archon dashboard`
        # should not silently kill whatever the user already has open.
        registry.clean_stale()
        if self.restart:
            registry.kill_recorded_instance(registry.pidfile_for(self.port))

        if self.dev:
            self._run_dev(server_dir, client_dir, registry, resolved)
            return

        self._run_production(server_dir, client_dir, archon_dir, registry, resolved)

    # ── dev mode ───────────────────────────────────────────────────────

    def _run_dev(
        self,
        server_dir: Path,
        client_dir: Path,
        registry: PidFileRegistry,
        resolved: Path,
    ) -> None:
        log.header("Dev Mode")
        log.key_value({
            "Dashboard": f"http://127.0.0.1:{self.port}",
            "Vite dev": "http://127.0.0.1:5173 (auto-opens)",
        })
        log.info("Press Ctrl+C to stop\n")

        server = ServerProcess(
            server_dir=server_dir,
            project_path=resolved,
            archon_dir=resolved / ".archon",
            registry=registry,
        )
        server.proc = server.spawn(self.port)
        server.port = self.port
        server.pid_file = registry.pidfile_for(self.port)
        server.pid_file.write_text(str(server.proc.pid))
        atexit.register(server.cleanup)

        vite = client_dir / "node_modules" / "vite" / "bin" / "vite.js"
        try:
            subprocess.run(
                ["node", str(vite), "--port", "5173"],
                cwd=client_dir,
            )
        except KeyboardInterrupt:
            pass
        finally:
            server.cleanup()

    # ── production mode ────────────────────────────────────────────────

    def _run_production(
        self,
        server_dir: Path,
        client_dir: Path,
        archon_dir: Path,
        registry: PidFileRegistry,
        resolved: Path,
    ) -> None:
        server = ServerProcess(
            server_dir=server_dir,
            project_path=resolved,
            archon_dir=archon_dir,
            registry=registry,
        )
        port = server.start_with_retry(self.port)

        # Use 127.0.0.1 over localhost: some systems resolve `localhost`
        # to ::1 first, which — if the server only listens on IPv4 —
        # hangs the browser at "waiting for host…". 127.0.0.1 always
        # picks the IPv4 stack.
        base_url = f"http://127.0.0.1:{port}"
        log.header("Archon Dashboard")
        log.key_value({
            "Dashboard": base_url,
            "Overview": f"{base_url}/",
            "Logs": f"{base_url}/logs",
            "Journal": f"{base_url}/journal",
            "Project": str(resolved),
            "PID": str(server.proc.pid),
            "PID file": str(server.pid_file),
        })
        log.step(f"Stop:  kill {server.proc.pid}  (or: kill $(cat {server.pid_file}))")

        if self.open_browser:
            _open_browser(base_url)

        server.install_signal_handlers()

        try:
            server.proc.wait()
        except KeyboardInterrupt:
            server.cleanup()

"""Start the Archon web dashboard."""

from __future__ import annotations

import atexit
import errno
import hashlib
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
import webbrowser
from importlib import resources
from pathlib import Path

import typer

from archon import log


def _data_path(sub_path: str = "") -> Path:
    root = resources.files("archon")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


def _has(binary: str) -> bool:
    return shutil.which(binary) is not None


def _port_in_use(port: int) -> bool:
    """Return True if the dashboard server can NOT bind to ``port``.

    The check mirrors the server's bind sequence in ``server/src/index.ts``:
    try IPv6 dual-stack ``::`` first, fall back to IPv4 ``0.0.0.0`` only on
    EAFNOSUPPORT/EADDRNOTAVAIL. SO_REUSEADDR matches libuv's defaults so we
    predict what the Node bind will actually see.

    A bind-test is the only reliable check for "is this port usable by the
    server". Connect-based probes (the previous implementation) gave false
    "free" results for ports bound to a specific interface or stuck in
    TIME_WAIT without SO_REUSEADDR, and they could block for the full
    socket timeout per attempt on filtered ports — turning ``_find_free_port``
    into a multi-second stall. ``bind`` returns immediately in every case.
    """
    # IPv6 dual-stack — what the server tries first.
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass  # platform may not allow toggling V6ONLY at runtime
            s.bind(("::", port))
            return False  # bound successfully — port is free
        finally:
            s.close()
    except OSError as e:
        # IPv6 disabled on this host — fall through to v4-only test, same
        # fallback path the server uses. Any other error (EADDRINUSE,
        # EACCES, …) means the server can't bind here either.
        if e.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, errno.EPROTONOSUPPORT):
            return True

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", port))
            return False
        finally:
            s.close()
    except OSError:
        return True


def _wait_for_http(port: int, expected_archon_path: str, timeout: float = 4.0) -> bool:
    """Return True once OUR archon UI is answering HTTP on ``port``.

    Critical: it's not enough to see *any* 200 response — anything else on
    port 8080 (a corporate proxy, a leftover dev server, an OpenAPI
    explorer that blanket-200s on `/api/*`) would falsely confirm our
    dashboard is live, the URL gets printed, and the user opens a broken
    page. Instead we hit ``/api/project`` and require the response body to
    parse as JSON containing ``archonPath`` matching the project we
    spawned the server for. Only our server can answer that correctly,
    so this rules out false positives end-to-end.

    Returns False if the deadline passes without a matching response —
    the caller's retry loop then either advances to a new port (if our
    Node died on bind) or kills + restarts.
    """
    url = f"http://127.0.0.1:{port}/api/project"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    try:
                        data = json.loads(resp.read().decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        data = None
                    if isinstance(data, dict) and data.get("archonPath") == expected_archon_path:
                        return True
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, OSError, TimeoutError):
            pass
        time.sleep(0.15)
    return False


def _find_free_port(start: int, attempts: int = 10) -> int | None:
    """Find a free port starting from `start + 1`."""
    for p in range(start + 1, start + 1 + attempts):
        if not _port_in_use(p):
            return p
    return None


def _project_key(project_path: str) -> str:
    """Return a short hash key for the project path (matches bash shasum -a 256)."""
    return hashlib.sha256(project_path.encode()).hexdigest()[:16]


def _is_archon_ui_pid(pid: int) -> bool:
    """Best-effort: confirm ``pid`` is actually an archon UI server.

    Guards against killing an unrelated process when the OS has reused the
    PID of a previous dashboard. Falls back to True (proceed with the kill)
    if we can't introspect the process — better to be slightly aggressive
    on platforms without /proc than to leave a stale server running.
    """
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.exists():
        try:
            cmd = proc_cmdline.read_text(errors="replace").lower()
        except OSError:
            return True
        return any(tok in cmd for tok in ("tsx", "src/index.ts", "archon-ui"))
    # Non-Linux: try `ps`. If that fails too, don't block the kill.
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


def _signal_pid_or_group(pid: int, sig: int) -> None:
    """Send ``sig`` to the entire process group of ``pid``, falling back to
    the single PID if it isn't a session leader.

    Servers spawned with ``start_new_session=True`` are session leaders, so
    ``getpgid(pid) == pid`` and ``killpg`` reaches every child the server
    forked. Without process-group signalling, terminating the parent leaves
    grandchildren running and can hold the port open.
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


def _wait_for_exit(pid: int, timeout: float, interval: float = 0.1) -> bool:
    """Poll until ``pid`` no longer exists or ``timeout`` elapses."""
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


def _pidfile_for(instance_dir: Path, project_key: str, port: int) -> Path:
    """Pid file path keyed by (project, port).

    Two dashboards for the same project on different ports therefore have
    distinct pid files — cleanup of one cannot trample the other's record.
    The previous scheme used one pid file per project, which forced each
    new ``archon dashboard`` invocation to either kill the previous
    instance (so it could write its own pid) or leak the previous record.
    """
    return instance_dir / f"{project_key}-{port}.pid"


def _project_pidfiles(instance_dir: Path, project_key: str) -> list[Path]:
    """All pid files belonging to ``project_key``, regardless of port."""
    return sorted(instance_dir.glob(f"{project_key}-*.pid"))


def _clean_stale_pidfiles(instance_dir: Path, project_key: str) -> None:
    """Remove pid files whose recorded PID is no longer alive.

    Live instances are left alone — they represent dashboards the user
    deliberately started in another terminal. Without this distinction
    we'd silently kill the user's other working dashboards every time
    they ran ``archon dashboard`` again.
    """
    for pf in _project_pidfiles(instance_dir, project_key):
        try:
            pid = int(pf.read_text().strip())
        except (ValueError, OSError):
            pf.unlink(missing_ok=True)
            continue
        try:
            os.kill(pid, 0)
        except OSError:
            pf.unlink(missing_ok=True)


def _kill_recorded_instance(pid_file: Path) -> bool:
    """Stop the instance recorded in ``pid_file`` (used by ``--restart``).

    Sends SIGTERM to the server's process group, waits up to 5s for
    graceful Fastify shutdown, then escalates to SIGKILL. Returns True if
    something was actually killed. Skips if the recorded PID is stale or
    no longer looks like an archon UI server.
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

    if not _is_archon_ui_pid(old_pid):
        log.warn(f"PID {old_pid} no longer looks like an archon UI server — leaving it alone")
        pid_file.unlink(missing_ok=True)
        return False

    log.step(f"Stopping previous UI server for this project (PID {old_pid})...")
    _signal_pid_or_group(old_pid, signal.SIGTERM)
    if not _wait_for_exit(old_pid, timeout=5.0):
        log.warn(f"PID {old_pid} did not exit after SIGTERM, forcing stop...")
        _signal_pid_or_group(old_pid, signal.SIGKILL)
        _wait_for_exit(old_pid, timeout=3.0)
    pid_file.unlink(missing_ok=True)
    return True


def _check_node() -> None:
    """Verify Node.js 18+ is available, or fail with setup hint."""
    if not _has("node") or not _has("npm"):
        log.error("Node.js and npm are required for the dashboard")
        log.step("Run: archon setup")
        raise typer.Exit(1)

    r = subprocess.run(["node", "-v"], capture_output=True, text=True)
    version_str = r.stdout.strip().lstrip("v")
    try:
        major = int(version_str.split(".")[0])
    except (ValueError, IndexError):
        major = 0

    if major < 18:
        log.error(f"Node.js 18+ required (found: {version_str})")
        log.step("Run: archon setup")
        raise typer.Exit(1)

    log.success(f"Node.js v{version_str}")


def _install_if_needed(directory: Path, name: str) -> None:
    """Run npm install if node_modules is missing or stale."""
    node_modules = directory / "node_modules"
    package_json = directory / "package.json"
    lock_marker = node_modules / ".package-lock.json"

    needs_install = False
    if not node_modules.exists():
        needs_install = True
    elif package_json.exists() and lock_marker.exists():
        if package_json.stat().st_mtime > lock_marker.stat().st_mtime:
            needs_install = True

    if needs_install:
        log.step(f"Installing {name} dependencies...")
        r = subprocess.run(
            ["npm", "install", "--no-fund", "--no-audit", "--loglevel=error"],
            cwd=directory, capture_output=True, text=True,
        )
        if r.returncode != 0:
            log.error(f"Failed to install {name} dependencies: {r.stderr.strip()}")
            raise typer.Exit(1)
        log.success(f"{name} dependencies installed")


def _needs_build(client_dir: Path) -> bool:
    """Check if client needs a rebuild."""
    dist = client_dir / "dist"
    index_html = dist / "index.html"
    if not dist.exists() or not index_html.exists():
        return True

    src_dir = client_dir / "src"
    if not src_dir.exists():
        return False

    index_mtime = index_html.stat().st_mtime
    for f in src_dir.rglob("*"):
        if f.is_file() and f.stat().st_mtime > index_mtime:
            return True
    return False


def _build_client(client_dir: Path) -> None:
    """Build the client via vite."""
    vite = client_dir / "node_modules" / "vite" / "bin" / "vite.js"
    r = subprocess.run(
        ["node", str(vite), "build", "--logLevel", "warn"],
        cwd=client_dir, capture_output=True, text=True,
    )
    if r.returncode != 0:
        log.error(f"Client build failed: {r.stderr.strip()}")
        raise typer.Exit(1)
    log.success("Client built")


def _open_browser(url: str) -> None:
    """Open a URL in the default browser."""
    try:
        webbrowser.open(url)
    except Exception:
        pass


# ── main command ──────────────────────────────────────────────────────


def dashboard(
    project_path: str = typer.Argument(".", help="Path to Lean project"),
    port: int = typer.Option(8080, "--port", "-p", help="Starting port. If busy, the next free port is chosen."),
    dev: bool = typer.Option(False, "--dev", help="Run in dev mode (vite dev + tsx watch)."),
    build_only: bool = typer.Option(False, "--build", help="Build client only, no server."),
    open_browser: bool = typer.Option(False, "--open", help="Open browser after starting."),
    restart: bool = typer.Option(
        False, "--restart",
        help="Stop the previous dashboard for this project on the chosen port "
             "before launching a new one. Without this flag, multiple dashboards "
             "for the same project coexist on different ports.",
    ),
) -> None:
    """Start the web dashboard for real-time monitoring.

    Shows iteration progress, parallel prover status, agent logs with
    live streaming, and proof journal milestones.

    Multiple dashboards for the same project can run simultaneously —
    each is given a different port. Pass ``--restart`` to instead replace
    the previous dashboard on the requested port.
    """
    resolved = Path(project_path).resolve()
    archon_dir = resolved / ".archon"

    if not archon_dir.is_dir():
        log.error(f"No .archon/ directory found in {resolved}")
        log.step("Run: archon init first, or check the project path")
        raise typer.Exit(1)

    # Locate UI directory from package data
    ui_dir = _data_path("ui")
    if not ui_dir.exists():
        log.error("UI files not found in package data — installation may be incomplete")
        raise typer.Exit(1)

    server_dir = ui_dir / "server"
    client_dir = ui_dir / "client"

    # Pid files now live under .archon-ui/ keyed by (project, port). The
    # final filename is decided after we resolve port conflicts below.
    instance_dir = ui_dir / ".archon-ui"
    instance_dir.mkdir(parents=True, exist_ok=True)
    project_key = _project_key(str(resolved))

    log.key_value({
        "Project": str(resolved),
        "Port": str(port),
        "Mode": "dev" if dev else "production",
    })

    # Check Node.js (should already be installed via archon setup)
    _check_node()

    # Install npm dependencies
    _install_if_needed(server_dir, "server")
    _install_if_needed(client_dir, "client")

    # Build client (skip in dev mode)
    if not dev:
        if _needs_build(client_dir):
            log.step("Building client...")
            _build_client(client_dir)
        else:
            log.success("Client up to date")

    if build_only:
        log.success("Build complete")
        return

    # Drop pid files for processes that have since died. Live instances
    # (the user's other dashboards in other terminals) are intentionally
    # left alone — running ``archon dashboard`` should not silently kill
    # whatever the user already has open.
    _clean_stale_pidfiles(instance_dir, project_key)

    # ``--restart`` means the user explicitly wants to replace the
    # previous dashboard on ``port``. Without it we leave running
    # instances alone and let the bind test below advance to a new port.
    if restart:
        _kill_recorded_instance(_pidfile_for(instance_dir, project_key, port))

    # Resolve port conflicts. Bind-test the requested port; advance if busy.
    if _port_in_use(port):
        log.warn(f"Port {port} is already in use by another process or project")
        free_port = _find_free_port(port)
        if free_port:
            port = free_port
            log.panel(f"Port changed! Using [bold]{port}[/bold] instead", style="yellow")
        else:
            log.error(f"Could not find a free port in range {port + 1}–{port + 11}")
            log.step("Free the current port or pass an explicit --port")
            raise typer.Exit(1)

    pid_file = _pidfile_for(instance_dir, project_key, port)

    # Start server
    server_cmd = [
        "node", "--import", "tsx",
        "src/index.ts", "--project", str(resolved), "--port", str(port),
    ]

    # Spawning kwargs shared by dev and production modes. ``start_new_session``
    # places the Node server in its own process group so we can kill the whole
    # tree on shutdown — terminating just the parent PID would leave any
    # children (rare, but possible during npm install fallbacks) holding the
    # listening socket. Windows has no equivalent here; archon's CLI is Unix-
    # only in practice (dashboard.py is invoked by `archon` shell entrypoint).
    spawn_kwargs: dict = {"cwd": server_dir}
    if platform.system() != "Windows":
        spawn_kwargs["start_new_session"] = True

    if dev:
        log.header("Dev Mode")
        log.key_value({
            "Dashboard": f"http://127.0.0.1:{port}",
            "Vite dev": "http://127.0.0.1:5173 (auto-opens)",
        })
        log.info("Press Ctrl+C to stop\n")

        server_proc = subprocess.Popen(server_cmd, **spawn_kwargs)
        pid_file.write_text(str(server_proc.pid))

        cleanup_done = {"done": False}
        def _cleanup_dev():
            if cleanup_done["done"]:
                return
            cleanup_done["done"] = True
            if server_proc.poll() is None:
                _signal_pid_or_group(server_proc.pid, signal.SIGTERM)
                if not _wait_for_exit(server_proc.pid, timeout=5.0):
                    _signal_pid_or_group(server_proc.pid, signal.SIGKILL)
                    _wait_for_exit(server_proc.pid, timeout=2.0)
            pid_file.unlink(missing_ok=True)
        atexit.register(_cleanup_dev)

        vite = client_dir / "node_modules" / "vite" / "bin" / "vite.js"
        try:
            subprocess.run(
                ["node", str(vite), "--port", "5173"],
                cwd=client_dir,
            )
        except KeyboardInterrupt:
            pass
        finally:
            _cleanup_dev()
        return

    # Production mode — launch with a retry loop so a TOCTOU race on the
    # port (two dashboards starting simultaneously both see 8080 as "free",
    # one wins the bind, the other dies with EADDRINUSE) bumps us forward
    # instead of leaving a dead process claiming success.
    def _spawn(p: int) -> subprocess.Popen:
        cmd = [
            "node", "--import", "tsx",
            "src/index.ts", "--project", str(resolved), "--port", str(p),
        ]
        return subprocess.Popen(cmd, **spawn_kwargs)

    def _shutdown_proc(proc: subprocess.Popen, term_timeout: float = 5.0) -> None:
        """Bring down a server process group cleanly: SIGTERM, wait, SIGKILL."""
        if proc.poll() is not None:
            return
        _signal_pid_or_group(proc.pid, signal.SIGTERM)
        if not _wait_for_exit(proc.pid, timeout=term_timeout):
            _signal_pid_or_group(proc.pid, signal.SIGKILL)
            _wait_for_exit(proc.pid, timeout=2.0)

    MAX_ATTEMPTS = 8
    server_proc = _spawn(port)
    pid_file.write_text(str(server_proc.pid))

    expected_archon_path = str(archon_dir)
    for attempt in range(MAX_ATTEMPTS):
        # Cold starts (tsx loader + module resolution + first build cache miss)
        # can take well over 4s on slow filesystems. Give the first attempt
        # extra headroom so we don't kill a healthy-but-slow server and churn
        # through ports for no reason.
        wait_timeout = 12.0 if attempt == 0 else 4.0
        if _wait_for_http(port, expected_archon_path, timeout=wait_timeout):
            break  # our server is live and answering
        # Either it died on bind (race with another dashboard) or it
        # didn't come up in time. Kill cleanly and advance to a fresh port.
        died = server_proc.poll() is not None
        if died:
            log.warn(f"Port {port} was taken (likely by a parallel dashboard). Trying the next port…")
        else:
            log.warn(f"Server on port {port} did not respond within {wait_timeout:g}s. Restarting on the next port…")
            _shutdown_proc(server_proc, term_timeout=2.0)

        next_port = _find_free_port(port)
        if next_port is None:
            log.error(f"Could not find a free port in range {port + 1}–{port + 11}")
            pid_file.unlink(missing_ok=True)
            raise typer.Exit(1)
        # Pid files are port-keyed; drop the old one before claiming the
        # new port's slot so we don't leave a phantom record behind.
        pid_file.unlink(missing_ok=True)
        port = next_port
        pid_file = _pidfile_for(instance_dir, project_key, port)
        server_proc = _spawn(port)
        pid_file.write_text(str(server_proc.pid))
    else:
        log.error(f"Server did not start after {MAX_ATTEMPTS} port attempts")
        _shutdown_proc(server_proc, term_timeout=2.0)
        pid_file.unlink(missing_ok=True)
        raise typer.Exit(1)

    # Use 127.0.0.1 over localhost: some systems resolve `localhost` to ::1
    # first, which — if the server only listens on IPv4 — hangs the browser
    # at "waiting for host…". 127.0.0.1 always picks the IPv4 stack.
    base_url = f"http://127.0.0.1:{port}"
    log.header("Archon Dashboard")
    log.key_value({
        "Dashboard": base_url,
        "Overview": f"{base_url}/",
        "Logs": f"{base_url}/logs",
        "Journal": f"{base_url}/journal",
        "Project": str(resolved),
        "PID": str(server_proc.pid),
        "PID file": str(pid_file),
    })
    log.step(f"Stop:  kill {server_proc.pid}  (or: kill $(cat {pid_file}))")

    if open_browser:
        _open_browser(base_url)

    # Wait for server (Ctrl+C to stop). Cleanup must be idempotent because
    # both atexit and the KeyboardInterrupt branch below call it, and a
    # double-kill of an already-dead PID would otherwise emit harmless but
    # confusing OSErrors.
    cleanup_done = {"done": False}
    def _cleanup_prod():
        if cleanup_done["done"]:
            return
        cleanup_done["done"] = True
        _shutdown_proc(server_proc, term_timeout=5.0)
        pid_file.unlink(missing_ok=True)
        log.info("Dashboard stopped")

    atexit.register(_cleanup_prod)

    # Forward SIGTERM (e.g., from `kill <python-pid>` or a parent shell that's
    # exiting) to the server's process group before we exit, so the listening
    # port is released cleanly even when we aren't dying via Ctrl+C.
    def _on_term(signum, frame):
        _cleanup_prod()
        # Re-raise default behaviour: exit with a conventional 128 + signum.
        raise SystemExit(128 + signum)
    try:
        signal.signal(signal.SIGTERM, _on_term)
    except (ValueError, OSError):
        pass  # not on the main thread, or platform doesn't support it

    try:
        server_proc.wait()
    except KeyboardInterrupt:
        _cleanup_prod()
"""Build/install helpers for the dashboard's Node + Vite frontend."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer

from archon import log


def check_node() -> None:
    """Verify Node.js 18+ is available, or fail with a setup hint."""
    if not shutil.which("node") or not shutil.which("npm"):
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


def install_if_needed(directory: Path, name: str) -> None:
    """Run `npm install` in `directory` if `node_modules` is missing or stale."""
    node_modules = directory / "node_modules"
    package_json = directory / "package.json"
    lock_marker = node_modules / ".package-lock.json"

    needs_install = False
    if not node_modules.exists():
        needs_install = True
    elif package_json.exists() and lock_marker.exists():
        if package_json.stat().st_mtime > lock_marker.stat().st_mtime:
            needs_install = True

    if not needs_install:
        return

    log.step(f"Installing {name} dependencies...")
    r = subprocess.run(
        ["npm", "install", "--no-fund", "--no-audit", "--loglevel=error"],
        cwd=directory, capture_output=True, text=True,
    )
    if r.returncode != 0:
        log.error(f"Failed to install {name} dependencies: {r.stderr.strip()}")
        raise typer.Exit(1)
    log.success(f"{name} dependencies installed")


def needs_build(client_dir: Path) -> bool:
    """Return True if any client source is newer than `dist/index.html`."""
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


def build_client(client_dir: Path) -> None:
    """Build the client via vite, exiting on failure."""
    vite = client_dir / "node_modules" / "vite" / "bin" / "vite.js"
    r = subprocess.run(
        ["node", str(vite), "build", "--logLevel", "warn"],
        cwd=client_dir, capture_output=True, text=True,
    )
    if r.returncode != 0:
        log.error(f"Client build failed: {r.stderr.strip()}")
        raise typer.Exit(1)
    log.success("Client built")

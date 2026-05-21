"""Install (and build) the dashboard's `npm` dependencies."""

from __future__ import annotations

import platform as _platform
import shutil
from pathlib import Path

from archon import log

from ..shell import data_path, run
from .base import DependencyCheck


class DashboardDepsCheck(DependencyCheck):
    """Installs server + client deps, then builds the client via Vite.

    Reuses an existing `node_modules` if it's up to date and matches the
    current platform. Native modules from a wrong-platform install
    (e.g. arm64 binaries on x86) trigger a clean reinstall.
    """

    name = "dashboard dependencies"

    def run(self) -> bool:
        ui_dir = data_path("ui")
        if not ui_dir.exists():
            log.warn("UI files not found in package data — skipping dashboard deps")
            return False

        server_dir = ui_dir / "server"
        client_dir = ui_dir / "client"
        ok = True

        for directory, name in ((server_dir, "server"), (client_dir, "client")):
            if not self._ensure_dir_deps(directory, name):
                ok = False

        ok = self._build_client(client_dir) and ok
        return ok

    # ── private ────────────────────────────────────────────────────────

    def _ensure_dir_deps(self, directory: Path, name: str) -> bool:
        package_json = directory / "package.json"
        if not package_json.exists():
            log.warn(f"No package.json in {name} directory — skipping")
            return True

        node_modules = directory / "node_modules"
        lock_marker = node_modules / ".package-lock.json"

        needs_install = False
        if not node_modules.exists():
            needs_install = True
        elif (lock_marker.exists()
                and package_json.stat().st_mtime > lock_marker.stat().st_mtime):
            needs_install = True
        elif self._has_wrong_platform_binaries(node_modules):
            log.warn(f"Dashboard {name} has native modules for a different platform")
            needs_install = True

        if not needs_install:
            log.success(f"Dashboard {name} dependencies up to date")
            return True

        return self._npm_install(directory, name, clean=True)

    @staticmethod
    def _has_wrong_platform_binaries(node_modules: Path) -> bool:
        system = _platform.system().lower()
        machine = _platform.machine().lower()

        if system == "linux":
            expected_fragments = ["linux"]
        elif system == "darwin":
            expected_fragments = ["darwin"]
        else:
            expected_fragments = ["win32", "windows"]

        if machine in ("x86_64", "amd64"):
            expected_fragments.append("x64")
        elif machine in ("arm64", "aarch64"):
            expected_fragments.append("arm64")

        for pkg_prefix in ("@esbuild", "@rollup"):
            pkg_dir = node_modules / pkg_prefix
            if not pkg_dir.is_dir():
                continue
            subdirs = [d.name for d in pkg_dir.iterdir() if d.is_dir()]
            if not subdirs:
                continue
            has_current = any(
                all(frag in d for frag in expected_fragments)
                for d in subdirs
            )
            if not has_current:
                return True

        return False

    @staticmethod
    def _npm_install(directory: Path, name: str, clean: bool = False) -> bool:
        if clean:
            node_modules = directory / "node_modules"
            package_lock = directory / "package-lock.json"
            if node_modules.exists():
                log.step(f"Removing {name} node_modules for clean install...")
                shutil.rmtree(node_modules, ignore_errors=True)
            if package_lock.exists():
                package_lock.unlink()

        log.step(f"Installing dashboard {name} dependencies...")
        r = run(
            ["npm", "install", "--no-fund", "--no-audit", "--loglevel=error"],
            cwd=str(directory),
        )
        if r.returncode != 0:
            log.error(f"Failed to install {name} dependencies: {r.stderr.strip()}")
            return False
        log.success(f"Dashboard {name} dependencies installed")
        return True

    def _build_client(self, client_dir: Path) -> bool:
        client_dist = client_dir / "dist" / "index.html"
        client_src = client_dir / "src"

        if not self._client_needs_build(client_dist, client_src):
            log.success("Dashboard client build up to date")
            return True

        if not (client_dir / "node_modules").exists():
            log.warn("Client node_modules missing — skipping build")
            return False

        vite = client_dir / "node_modules" / "vite" / "bin" / "vite.js"
        if not vite.exists():
            log.warn("Vite not found in node_modules — skipping build")
            return False

        log.step("Building dashboard client...")
        r = run(
            ["node", str(vite), "build", "--logLevel", "warn"],
            cwd=str(client_dir),
        )
        if r.returncode == 0:
            log.success("Dashboard client built")
            return True

        # Known npm/rollup optional-dep bug — clean reinstall and retry once.
        stderr = r.stderr or ""
        if "rollup" in stderr.lower() and (
            "cannot find module" in stderr.lower()
            or "npm has a bug" in stderr.lower()
        ):
            log.warn("Hit known rollup/npm optional dependency bug — retrying with clean install")
            if not self._npm_install(client_dir, "client", clean=True):
                return False

            log.step("Retrying client build...")
            r = run(
                ["node", str(vite), "build", "--logLevel", "warn"],
                cwd=str(client_dir),
            )
            if r.returncode == 0:
                log.success("Dashboard client built (after clean reinstall)")
                return True

        log.error(f"Client build failed: {(r.stderr or '').strip()}")
        return False

    @staticmethod
    def _client_needs_build(client_dist: Path, client_src: Path) -> bool:
        if not client_dist.exists():
            return True
        if not client_src.exists():
            return False
        dist_mtime = client_dist.stat().st_mtime
        for f in client_src.rglob("*"):
            if f.is_file() and f.stat().st_mtime > dist_mtime:
                return True
        return False

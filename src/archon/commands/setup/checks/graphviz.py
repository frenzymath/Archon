"""Install graphviz + dev headers (required to build pygraphviz).

`pygraphviz` is a C extension and pip will compile it from source if no
wheel is available on the platform. That build needs three things:

  1. the graphviz library + its development headers (`cgraph.h`),
  2. a working C compiler (`gcc` / `cc` / `clang`),
  3. the CPython development headers (`Python.h`).

This check ensures all three are present so `pip install leanblueprint`
— which pulls in `pygraphviz` transitively — does not fail on a fresh
machine.
"""

from __future__ import annotations

import sys
import sysconfig
from pathlib import Path

from archon import log

from ..shell import has, version
from .base import DependencyCheck


class GraphvizCheck(DependencyCheck):
    """graphviz binary + headers needed for pygraphviz, used by plastexdepgraph."""

    name = "graphviz"

    def run(self) -> bool:
        toolchain_ok = self._ensure_build_toolchain()
        graphviz_ok = self._ensure_graphviz()
        return toolchain_ok and graphviz_ok

    # ── graphviz library + dev headers ────────────────────────────────

    def _ensure_graphviz(self) -> bool:
        have_dot = has("dot")
        # The dev headers are harder to detect portably — we just check
        # for a well-known header file on Linux, and trust brew on macOS.
        dev_headers_present = (
            Path("/usr/include/graphviz/cgraph.h").exists()
            or Path("/usr/local/include/graphviz/cgraph.h").exists()
            or Path("/opt/homebrew/include/graphviz/cgraph.h").exists()
        )

        if have_dot and dev_headers_present:
            log.success(f"graphviz: {version(['dot', '-V'])}")
            return True

        if have_dot and not dev_headers_present:
            log.warn("graphviz is installed but development headers seem to be missing.")
            log.step(
                "These are required to build pygraphviz "
                "(used by leanblueprint's dep graph).",
            )
        else:
            log.step("graphviz not found, attempting install...")

        ok = self.installer.install_bundle(
            "graphviz (with dev headers)",
            {
                # brew's `graphviz` formula ships headers, no separate dev pkg.
                "brew": ["graphviz"],
                "apt-get": ["graphviz", "libgraphviz-dev"],
                "dnf": ["graphviz", "graphviz-devel"],
                "pacman": ["graphviz"],
            },
            install_urls={
                "Manual": "https://pygraphviz.github.io/documentation/stable/install.html",
            },
        )

        if has("dot"):
            log.success(f"graphviz installed: {version(['dot', '-V'])}")
            return ok
        log.warn(
            "graphviz not installed — the blueprint dependency graph will fail to build.",
        )
        return False

    # ── C compiler + Python.h (needed to build pygraphviz from source) ─

    def _ensure_build_toolchain(self) -> bool:
        compiler_present = self._has_compiler()
        headers_present = self._python_headers_present()
        if compiler_present and headers_present:
            return True

        if not compiler_present:
            log.step("C compiler not found — required to build pygraphviz from source.")
        if not headers_present:
            log.step("Python development headers (Python.h) not found.")

        if sys.platform == "darwin":
            # macOS: the package manager (brew) doesn't ship gcc/Python.h as a
            # separate bundle — Xcode Command Line Tools provide both.
            log.warn(
                "Install Xcode Command Line Tools manually: `xcode-select --install`",
            )
            return self._has_compiler() and self._python_headers_present()

        ok = self.installer.install_bundle(
            "C compiler + Python headers (for pygraphviz)",
            {
                "apt-get": ["build-essential", "python3-dev"],
                "dnf": ["gcc", "gcc-c++", "make", "python3-devel"],
                "pacman": ["base-devel"],
            },
        )

        if self._has_compiler() and self._python_headers_present():
            return ok
        log.warn(
            "Build toolchain still incomplete — pygraphviz will fail to compile.",
        )
        return False

    @staticmethod
    def _has_compiler() -> bool:
        return has("gcc") or has("cc") or has("clang")

    @staticmethod
    def _python_headers_present() -> bool:
        include = sysconfig.get_path("include")
        return bool(include) and (Path(include) / "Python.h").exists()

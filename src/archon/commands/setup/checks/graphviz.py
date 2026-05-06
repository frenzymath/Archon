"""Install graphviz + dev headers (required to build pygraphviz)."""

from __future__ import annotations

from pathlib import Path

from archon import log

from ..shell import has, version
from .base import DependencyCheck


class GraphvizCheck(DependencyCheck):
    """graphviz binary + headers needed for pygraphviz, used by plastexdepgraph."""

    name = "graphviz"

    def run(self) -> bool:
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

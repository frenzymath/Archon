"""Install poppler-utils so the Read tool can render PDF references.

``references/`` typically holds the source papers backing the formalization.
Claude Code's ``Read`` tool can ingest PDFs page-by-page, but it relies on
``pdftoppm`` (from ``poppler-utils``) to rasterize pages first; without it,
the agent gets a hard "Cannot read PDF" error and has to fall back to
``pdftotext``-style workarounds.

This is optional — Archon still functions if poppler is missing — but the
debug log from iter-162/163 showed agents repeatedly bouncing off PDF
references they were meant to read, so we install it by default.
"""

from __future__ import annotations

from archon import log

from ..shell import has, version
from .base import DependencyCheck


class PopplerCheck(DependencyCheck):
    """``pdftoppm`` + ``pdftotext`` from ``poppler-utils`` (PDF rendering)."""

    name = "poppler-utils"

    def run(self) -> bool:
        if has("pdftoppm") and has("pdftotext"):
            log.success(f"poppler-utils: {version(['pdftoppm', '-v'])}")
            return True

        log.step(
            "poppler-utils is optional but recommended — agents need "
            "pdftoppm to read PDF references via the Read tool. Attempting install...",
        )
        self.installer.install_bundle(
            "poppler-utils (pdftoppm, pdftotext)",
            {
                "brew": ["poppler"],
                "apt-get": ["poppler-utils"],
                "dnf": ["poppler-utils"],
                "pacman": ["poppler"],
            },
            install_urls={
                "Manual": "https://poppler.freedesktop.org/",
            },
        )

        if has("pdftoppm") and has("pdftotext"):
            log.success(f"poppler-utils installed: {version(['pdftoppm', '-v'])}")
            return True
        log.warn(
            "poppler-utils not installed — the Read tool will refuse PDF "
            "pages; agents will need a manual fallback to read references.",
        )
        return False

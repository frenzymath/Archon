"""Install the LaTeX + auxiliary tools needed by leanblueprint (pdf & web targets)."""

from __future__ import annotations

from archon import log

from ..shell import has
from .base import DependencyCheck


_TEX_LATEX_BINARIES = ("pdflatex", "xelatex", "lualatex", "latex")


class TexToolchainCheck(DependencyCheck):
    name = "TeX toolchain"

    def run(self) -> bool:
        missing = self._missing()
        if not missing:
            log.success(
                "TeX toolchain: latex, ghostscript, dvisvgm, pdf2svg, pdfcrop all present",
            )
            return True

        log.step(
            f"TeX toolchain incomplete (missing: {', '.join(missing)}), "
            "attempting install...",
        )
        self.installer.install_bundle(
            "TeX toolchain for leanblueprint",
            {
                # Homebrew: the full texlive cask is enormous; we install
                # the smaller pieces. Users who want a fuller TeX setup
                # should install MacTeX themselves.
                "brew": ["texlive", "ghostscript", "dvisvgm", "pdf2svg"],
                "apt-get": [
                    "texlive-latex-base",
                    "texlive-latex-extra",
                    "texlive-extra-utils",  # provides pdfcrop
                    "ghostscript",
                    "dvisvgm",
                    "pdf2svg",
                ],
                "dnf": [
                    "texlive-scheme-basic",
                    "texlive-collection-latexextra",
                    "texlive-pdfcrop",
                    "ghostscript",
                    "dvisvgm",
                    "pdf2svg",
                ],
                "pacman": [
                    "texlive-basic",
                    "texlive-latexextra",
                    "ghostscript",
                    "dvisvgm",
                    "pdf2svg",
                ],
            },
            install_urls={
                "TeX Live": "https://www.tug.org/texlive/",
                "MacTeX":   "https://www.tug.org/mactex/",
            },
        )

        still_missing = self._missing()
        if not still_missing:
            log.success("TeX toolchain installed")
            return True

        log.warn(f"TeX toolchain still incomplete: missing {', '.join(still_missing)}")
        log.step("The blueprint PDF/web build may fail until these are installed.")
        return False

    def _missing(self) -> list[str]:
        missing: list[str] = []
        if not any(has(b) for b in _TEX_LATEX_BINARIES):
            missing.append("latex")
        if not has("gs"):
            missing.append("ghostscript")
        if not has("dvisvgm"):
            missing.append("dvisvgm")
        if not has("pdf2svg"):
            missing.append("pdf2svg")
        if not has("pdfcrop"):
            missing.append("pdfcrop")
        return missing

"""SetupCommand: orchestrates `archon setup`'s dependency checks."""

from __future__ import annotations

import typer

from archon import log

from .checks import (
    ApiKeysCheck,
    ClaudeCodeCheck,
    CurlCheck,
    DashboardDepsCheck,
    GitCheck,
    GraphvizCheck,
    LeanBlueprintCheck,
    LeanToolchainCheck,
    NodeCheck,
    PopplerCheck,
    PythonCheck,
    RipgrepCheck,
    TexToolchainCheck,
    UvCheck,
)
from .installer import PackageInstaller, SudoMode
from .shell import shell_rc


class SetupCommand:
    """Runs the dependency checks in their canonical order."""

    def __init__(
        self,
        *,
        sudo_mode: SudoMode = "ask",
        skip_blueprint: bool = False,
    ) -> None:
        self.installer = PackageInstaller(sudo_mode=sudo_mode)
        self.skip_blueprint = skip_blueprint

    def run(self) -> None:
        self._check_prerequisites()
        self._check_python_tooling()
        self._check_claude_code()
        self._check_blueprint_deps()
        self._check_dashboard()
        self._check_api_keys()
        self._summarize()

    # ── private ────────────────────────────────────────────────────────

    def _check_prerequisites(self) -> None:
        """Hard prerequisites — exit if any of these fail."""
        log.rule("System prerequisites")
        fatal = False
        for cls in (GitCheck, PythonCheck, CurlCheck, LeanToolchainCheck):
            if not cls(self.installer).run():
                fatal = True
        if fatal:
            log.error("Required prerequisites missing — fix the errors above and re-run.")
            raise typer.Exit(1)

    def _check_python_tooling(self) -> None:
        log.rule("Python tooling & packages")
        UvCheck(self.installer).run()
        RipgrepCheck(self.installer).run()
        PopplerCheck(self.installer).run()

    def _check_claude_code(self) -> None:
        log.rule("Claude Code")
        ClaudeCodeCheck(self.installer).run()

    def _check_blueprint_deps(self) -> None:
        if self.skip_blueprint:
            log.rule("Blueprint (skipped)")
            log.info(
                "--skip-blueprint set — not installing graphviz, TeX, or leanblueprint.",
            )
            return

        log.rule("Blueprint system dependencies")
        GraphvizCheck(self.installer).run()
        TexToolchainCheck(self.installer).run()

        log.rule("leanblueprint CLI")
        LeanBlueprintCheck(self.installer).run()

    def _check_dashboard(self) -> None:
        log.rule("Dashboard dependencies")
        node_ok = NodeCheck(self.installer).run()
        if node_ok:
            DashboardDepsCheck(self.installer).run()
        else:
            log.warn("Skipping dashboard npm install — Node.js not available")

    def _check_api_keys(self) -> None:
        log.rule("Informal agent API keys (optional)")
        ApiKeysCheck(self.installer).run()

    def _summarize(self) -> None:
        log.rule("Setup complete")
        rc = shell_rc()
        log.success("All dependencies checked. You can now run: archon init")

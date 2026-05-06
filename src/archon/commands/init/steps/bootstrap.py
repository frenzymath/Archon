"""Run the deterministic Lean project bootstrap (lake / git / mathlib / blueprint)."""

from __future__ import annotations

import typer

from archon import log
from archon.commands.tooling.blueprint import Blueprint
from archon.commands.tooling.git import Git
from archon.commands.tooling.lake import Lake
from archon.commands.tooling.project import (
    BootstrapOptions,
    ProjectBootstrap,
    WorkspaceTemplates,
)

from .base import InitStep


class BootstrapStep(InitStep):
    """Replaces what used to be Claude's job in init.md steps 1-2.

    Safe to call on an already-initialized project — every sub-step is
    idempotent, and the bootstrap only commits if it actually did work.
    """

    name = "Bootstrapping Lean project (lake, git, mathlib, blueprint)"
    number = 3

    def run(self) -> None:
        ctx = self.ctx
        log.phase(self.number, self.name)

        if not Lake.available():
            log.error("`lake` is not on PATH. Run: archon setup")
            raise typer.Exit(1)
        if not Git.available():
            log.error("`git` is not on PATH. Run: archon setup")
            raise typer.Exit(1)
        if not Blueprint.available():
            log.warn(
                "`leanblueprint` is not on PATH — will skip blueprint scaffold "
                "(run: archon setup)",
            )

        options = BootstrapOptions(
            init_lake=True,
            add_mathlib=True,
            init_blueprint=Blueprint.available(),
            fetch_mathlib_cache=True,
            do_initial_build=True,
            project_title=ctx.project_path.name,
        )

        bootstrap = ProjectBootstrap(ctx.project_path, options)
        report = bootstrap.run()

        for a in report.actions:
            log.step(a)
        for w in report.warnings:
            log.warn(w)
        for s in report.skipped:
            log.info(f"Skipped: {s}")

        # Write README / references summary skeletons.
        templates = WorkspaceTemplates(ctx.project_path)
        if templates.ensure_readme():
            log.step("Wrote README.md skeleton")
        if templates.ensure_references_summary():
            log.step("Wrote references/summary.md skeleton")

        if report.stage_report:
            sr = report.stage_report
            log.info(
                f"Stage detection: {sr.stage} "
                f"({sr.lean_file_count} Lean files, {sr.decl_count} declarations, "
                f"{sr.sorry_count} sorries)"
            )

        log.success("Bootstrap complete")
        ctx.bootstrap_report = report

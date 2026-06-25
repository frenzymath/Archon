"""Regression tests for re-init stage reconciliation.

A re-init (keep/merge/overwrite) never runs the interactive semantic pass,
so a project whose PROGRESS.md was left at 'init' — but which already has
Lean declarations on disk — used to stay wedged at 'init', and
``archon loop`` / ``archon dag`` would then refuse to run. ``InitCommand.
_reconcile_stage_if_stuck`` advances such a project to its detected stage.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.commands.init.command import InitCommand
from archon.commands.init.context import InitContext
from archon.state import read_stage

# Mirrors src/archon/.archon-src/archon-template/PROGRESS.md: the stage sits on
# the line directly under the header (no blank line), which is what a real
# project that never finished its semantic pass looks like on disk.
_PROGRESS_TEMPLATE = """# Project Progress

## Current Stage
init

## Stages
- [ ] init
- [ ] autoformalize
- [ ] prover
- [ ] polish

## Current Objectives
"""


def _make_command(project: Path) -> InitCommand:
    state_dir = project / ".archon"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "PROGRESS.md").write_text(_PROGRESS_TEMPLATE)
    cmd = InitCommand(str(project))
    cmd.ctx = InitContext(
        project_path=project, state_dir=state_dir, fresh=False, model="x",
    )
    return cmd


class StageReconcileTests(unittest.TestCase):
    def test_project_with_declarations_advances_off_init(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            (project / "Foo.lean").write_text(
                "theorem foo : True := by sorry\n"
            )
            cmd = _make_command(project)
            cmd._reconcile_stage_if_stuck()
            # A decl with a sorry → 'prover'.
            self.assertEqual(
                read_stage(cmd.ctx.state_dir / "PROGRESS.md"), "prover"
            )

    def test_empty_project_stays_at_init(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            # No .lean files / no declarations.
            cmd = _make_command(project)
            cmd._reconcile_stage_if_stuck()
            self.assertEqual(
                read_stage(cmd.ctx.state_dir / "PROGRESS.md"), "init"
            )

    def test_already_advanced_stage_is_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            (project / "Foo.lean").write_text("theorem foo : True := trivial\n")
            cmd = _make_command(project)
            (cmd.ctx.state_dir / "PROGRESS.md").write_text(
                _PROGRESS_TEMPLATE.replace("init", "polish", 1)
            )
            cmd._reconcile_stage_if_stuck()
            self.assertEqual(
                read_stage(cmd.ctx.state_dir / "PROGRESS.md"), "polish"
            )


if __name__ == "__main__":
    unittest.main()

"""Tests for the five review-* subagent classes (Workstream E).

Each class is a thin specialization of ``ReviewSubagent`` whose only
responsibility is its ``name`` attribute. The tests verify:

* Class registration: all five exist, are importable, share the base.
* ``name`` follows the ``review-*`` convention.
* ``build_prompt`` produces a non-empty prompt that references the
  project paths and the role-specific prompt file.
* ``report_path`` uses the hierarchical layout when given a non-root
  ``parent_slug``, the flat layout otherwise.
* The CLI exposes a subcommand for each role.
* The wrapper script and installer recognize each role.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.subagents.base import ROOT_PARENT_SLUG, Subagent
from archon.subagents.review_base import ReviewSubagent
from archon.subagents.review_blueprint_consistency import (
    ReviewBlueprintConsistencySubagent,
)
from archon.subagents.review_comment_hygiene import ReviewCommentHygieneSubagent
from archon.subagents.review_definition_correctness import (
    ReviewDefinitionCorrectnessSubagent,
)
from archon.subagents.review_design_choices import ReviewDesignChoicesSubagent
from archon.subagents.review_mathlib_overlap import ReviewMathlibOverlapSubagent


REVIEW_CLASSES = [
    ReviewDefinitionCorrectnessSubagent,
    ReviewCommentHygieneSubagent,
    ReviewBlueprintConsistencySubagent,
    ReviewDesignChoicesSubagent,
    ReviewMathlibOverlapSubagent,
]


REVIEW_NAMES = [
    "review-definition-correctness",
    "review-comment-hygiene",
    "review-blueprint-consistency",
    "review-design-choices",
    "review-mathlib-overlap",
]


class ReviewClassesRegistrationTest(unittest.TestCase):
    def test_all_inherit_review_base(self):
        for cls in REVIEW_CLASSES:
            self.assertTrue(issubclass(cls, ReviewSubagent), cls)
            self.assertTrue(issubclass(cls, Subagent), cls)

    def test_names_match_convention(self):
        for cls, expected in zip(REVIEW_CLASSES, REVIEW_NAMES):
            self.assertEqual(cls.name, expected)
            self.assertTrue(cls.name.startswith("review-"))


class ReviewSubagentBuildPromptTest(unittest.TestCase):
    """Each build_prompt produces a non-empty prompt referencing the right paths."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.project = Path(self._td.name)
        (self.project / ".archon").mkdir()
        (self.project / ".archon" / "prompts").mkdir()

    def tearDown(self):
        self._td.cleanup()

    def test_every_class_emits_role_specific_prompt(self):
        for cls in REVIEW_CLASSES:
            sub = cls(self.project)
            prompt = sub.build_prompt(
                directive="audit this file", slug="probe-1", iter_num=7,
            )
            self.assertIn(cls.name, prompt)
            # Points at its own prompt file
            self.assertIn(f"prompts/{cls.name}.md", prompt)
            # Includes the directive verbatim
            self.assertIn("audit this file", prompt)
            # Stamps iter num correctly
            self.assertIn("007", prompt)
            # Identifies as read-only on project source
            self.assertIn("READ-ONLY", prompt)


class ReviewReportPathTest(unittest.TestCase):
    """report_path uses hierarchical layout when parent_slug != _root."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.project = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_root_uses_flat_layout(self):
        sub = ReviewCommentHygieneSubagent(self.project)
        p = sub.report_path("probe", parent_slug=ROOT_PARENT_SLUG)
        self.assertTrue(str(p).endswith(
            "/task_results/review-comment-hygiene-probe.md"
        ))

    def test_nested_uses_parent_dir(self):
        sub = ReviewDefinitionCorrectnessSubagent(self.project)
        p = sub.report_path("probe", parent_slug="audit-master")
        self.assertTrue(str(p).endswith(
            "/task_results/audit-master/review-definition-correctness-probe.md"
        ))


class ReviewCliRegistrationTest(unittest.TestCase):
    """The five new subagents must be exposed by `archon subagent`."""

    def test_cli_lists_all_review_subcommands(self):
        import typer.testing
        from archon.commands.subagent import app

        runner = typer.testing.CliRunner()
        result = runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0)
        for name in REVIEW_NAMES:
            self.assertIn(name, result.output)


class ReviewWrapperAndInstallerTest(unittest.TestCase):
    """The wrapper script + installer must recognize each new role name."""

    def test_wrapper_valid_roles_covers_review_set(self):
        # Static parse — load the wrapper source as text and check the
        # role list. We can't import the wrapper as a module because it
        # derives its role from sys.argv[0] at import time.
        wrapper = Path(__file__).parent.parent / "src" / "archon" / ".archon-src" / "tools" / "subagent_wrapper.py"
        src = wrapper.read_text(encoding="utf-8")
        for name in REVIEW_NAMES:
            self.assertIn(f'"{name}"', src, f"wrapper missing role {name!r}")

    def test_installer_subagent_roles_covers_review_set(self):
        from archon.commands.init.steps.skills import SkillsStep
        for name in REVIEW_NAMES:
            self.assertIn(name, SkillsStep._SUBAGENT_ROLES)


if __name__ == "__main__":
    unittest.main()

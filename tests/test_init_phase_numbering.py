"""Regression test: init step phase numbers are unique and sequential.

The reviewer observed the init output emitting
``Phase 1 → 2 → 4 → 5 → 6 → 7 → 9 → 8 → 9 → 9`` — duplicate ``9``s for
``EnvAndConfigStep`` / ``GitHooksStep`` / ``VersionStampStep``, plus
``InnerGitStep`` (8) running after the first 9. The numbers are
display-only but they shape the user's mental model of what's
happening, so this test pins them in canonical order.
"""

from __future__ import annotations

import unittest

from archon.commands.init.steps import (
    BootstrapStep,
    CopyPromptsStep,
    DisableConflictingPluginsStep,
    EnvAndConfigStep,
    GitHooksStep,
    InnerGitStep,
    LeanLspMcpStep,
    ReportProtectedStep,
    SemanticPassStep,
    SkillsStep,
    StateDirStep,
    VersionStampStep,
)


_FULL_INIT_ORDER = [
    (StateDirStep, 1),
    (CopyPromptsStep, 2),
    (BootstrapStep, 3),
    (LeanLspMcpStep, 4),
    (SkillsStep, 5),
    (DisableConflictingPluginsStep, 6),
    (SemanticPassStep, 7),
    (ReportProtectedStep, 8),
    (EnvAndConfigStep, 9),
    (InnerGitStep, 10),
    (GitHooksStep, 11),
    (VersionStampStep, 12),
]


class PhaseNumberingTest(unittest.TestCase):

    def test_step_numbers_match_canonical_order(self):
        for cls, expected in _FULL_INIT_ORDER:
            with self.subTest(cls=cls.__name__):
                self.assertEqual(
                    cls.number,
                    expected,
                    f"{cls.__name__}.number should be {expected}, "
                    f"is {cls.number}",
                )

    def test_step_numbers_are_unique(self):
        numbers = [cls.number for cls, _ in _FULL_INIT_ORDER]
        self.assertEqual(
            len(numbers),
            len(set(numbers)),
            f"duplicate phase numbers detected: {numbers}",
        )

    def test_step_numbers_are_dense_one_through_twelve(self):
        numbers = sorted(cls.number for cls, _ in _FULL_INIT_ORDER)
        self.assertEqual(numbers, list(range(1, 13)))


if __name__ == "__main__":
    unittest.main()

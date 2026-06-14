"""Tests for prover-stage prompt path normalization.

The plan agent occasionally writes ``## Current Stage`` with descriptive
text appended (e.g. ``prover (Iter-123: M1.b residual — Steps 1-4 of
the IsLocalization.of_le)``). The raw text contains parentheses and
em-dashes which break the prompt-file path resolution when embedded
into ``.archon/prompts/prover-<stage>.md``. The normalizer should pick
the canonical stage prefix and discard the descriptive tail.
"""

from __future__ import annotations

import unittest

from archon.prompts import (
    normalize_stage_for_prompt_path,
    build_parallel_prover_prompt,
    build_prover_prompt,
)


class NormalizeStageForPromptPathTest(unittest.TestCase):
    def test_passes_through_canonical_tokens(self):
        for s in ("autoformalize", "prover", "polish"):
            self.assertEqual(normalize_stage_for_prompt_path(s), s)

    def test_strips_trailing_descriptive_text(self):
        verbose = (
            "prover (Iter-123: M1.b residual — Steps 1-4 of the "
            "IsLocalization.of_le)"
        )
        self.assertEqual(
            normalize_stage_for_prompt_path(verbose), "prover",
        )

    def test_strips_trailing_descriptive_text_for_polish(self):
        verbose = "polish — final pass cleanup round"
        self.assertEqual(
            normalize_stage_for_prompt_path(verbose), "polish",
        )

    def test_case_insensitive_match(self):
        self.assertEqual(
            normalize_stage_for_prompt_path("PROVER"), "prover",
        )
        self.assertEqual(
            normalize_stage_for_prompt_path("AutoFormalize"), "autoformalize",
        )

    def test_unknown_falls_back_to_prover(self):
        self.assertEqual(
            normalize_stage_for_prompt_path("nonsense"), "prover",
        )
        self.assertEqual(normalize_stage_for_prompt_path(""), "prover")


class BuildProverPromptStagePathTest(unittest.TestCase):
    """End-to-end: the path-rendering path uses the normalized token."""

    def test_serial_prompt_uses_canonical_path(self):
        verbose = "prover (Iter-123: M1.b residual — Steps 1-4)"
        from pathlib import Path
        prompt = build_prover_prompt(
            project_name="proj",
            project_path=Path("/proj"),
            state_dir=Path("/proj/.archon"),
            stage=verbose,
            iter_num=123,
        )
        # The path embed must be the canonical one.
        self.assertIn("prompts/prover-prover.md", prompt)
        # The verbose text is still shown for human context.
        self.assertIn(verbose, prompt)
        # And the broken filename should NOT appear.
        self.assertNotIn(
            "prompts/prover-prover (Iter-123",
            prompt,
        )

    def test_parallel_prompt_uses_canonical_path(self):
        from pathlib import Path
        verbose = "polish — final pass cleanup"
        prompt = build_parallel_prover_prompt(
            project_name="proj",
            project_path=Path("/proj"),
            state_dir=Path("/proj/.archon"),
            stage=verbose,
            iter_num=42,
        )
        self.assertIn("prompts/prover-polish.md", prompt)
        self.assertIn(verbose, prompt)


if __name__ == "__main__":
    unittest.main()

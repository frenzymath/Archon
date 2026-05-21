"""Regression tests for the secret-scrub / secret-detect git hooks.

The reviewer flagged real gaps that let secrets through:

  * GitHub PATs (``ghp_…``, ``github_pat_…``, ``glpat-…``) were not in
    either ``SECRET_PATTERNS`` or ``ENV_KEY_NAMES``.
  * The env-assign value class ``[A-Za-z0-9_\\-]{16,}`` truncated at
    the first ``/`` or ``+``, so realistic ``AWS_SECRET_ACCESS_KEY``
    values matched only their first segment (and the rest was committed
    in the clear).
  * ``AWS_ACCESS_KEY_ID`` was in neither list, so the ``AKIA…`` shape
    slipped through entirely.
  * Slack tokens (``xox[abprs]-…``) weren't matched.

These tests load the hook modules directly and exercise the matchers
against representative-shape fake secrets.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


_HOOKS_DIR = Path(__file__).resolve().parent.parent / (
    "src/archon/.archon-src/git-hooks"
)


def _load_hook_module(filename: str, mod_name: str):
    # Hook files don't have a .py extension; spec_from_file_location
    # needs an explicit SourceFileLoader to know how to read them.
    from importlib.machinery import SourceFileLoader
    path = _HOOKS_DIR / filename
    loader = SourceFileLoader(mod_name, str(path))
    spec = importlib.util.spec_from_loader(mod_name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    loader.exec_module(mod)
    return mod


class PreCommitPatternsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_hook_module("pre-commit", "archon_pre_commit_hook")

    def _any_match(self, text: str) -> bool:
        for _label, pat in self.mod.SECRET_PATTERNS:
            if pat.search(text):
                return True
        return False

    def test_github_classic_pat_matches(self):
        # Fake but shape-accurate: ``ghp_`` + 36+ alphanumeric chars.
        token = "ghp_" + "A" * 36
        self.assertTrue(self._any_match(f"const t = '{token}'"))

    def test_github_oauth_pat_matches(self):
        token = "gho_" + "B" * 40
        self.assertTrue(self._any_match(token))

    def test_github_fine_pat_matches(self):
        # github_pat_<82 chars>.
        token = "github_pat_" + "C" * 82
        self.assertTrue(self._any_match(token))

    def test_gitlab_pat_matches(self):
        token = "glpat-" + "D" * 20
        self.assertTrue(self._any_match(token))

    def test_aws_access_key_id_matches(self):
        for prefix in ("AKIA", "ASIA"):
            token = prefix + "ABCDEFGHIJKLMNOP"  # 16 upper/digit
            with self.subTest(prefix=prefix):
                self.assertTrue(self._any_match(f"id = {token}"))

    def test_slack_token_matches(self):
        for variant in ("xoxb", "xoxa", "xoxp", "xoxr", "xoxs"):
            token = f"{variant}-1234567890abcdef"
            with self.subTest(variant=variant):
                self.assertTrue(self._any_match(token))

    def test_aws_secret_with_slash_matches_env_assign(self):
        # AWS secrets routinely contain '/' (and '+'). The previous
        # value class stopped at those, leaving the suffix in the clear.
        line = ('AWS_SECRET_ACCESS_KEY='
                "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        m = self.mod.ENV_ASSIGN.search(line)
        self.assertIsNotNone(m, "env-assign regex must match through slashes")
        # The captured value should run to end of secret, not stop at /
        self.assertEqual(
            m.group("val"),
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )

    def test_aws_access_key_id_env_assign(self):
        line = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        self.assertIsNotNone(self.mod.ENV_ASSIGN.search(line))

    def test_scrub_text_redacts_github_pat(self):
        token = "ghp_" + "Q" * 36
        scrubbed, n = self.mod._scrub_text(f"PAT = '{token}'")
        self.assertGreater(n, 0)
        self.assertNotIn(token, scrubbed)
        self.assertIn(self.mod.REDACTION, scrubbed)

    def test_idempotent_redaction(self):
        # A second pass over already-scrubbed content must be a no-op.
        original = "ghp_" + "Z" * 36
        scrubbed, n1 = self.mod._scrub_text(f"k = '{original}'")
        _, n2 = self.mod._scrub_text(scrubbed)
        self.assertGreater(n1, 0)
        self.assertEqual(n2, 0)


class PrePushPatternsTest(unittest.TestCase):
    """The pre-push hook keeps its own constants. They must stay in sync."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_hook_module("pre-push", "archon_pre_push_hook")

    def _any_match(self, text: str) -> bool:
        for _label, pat in self.mod.SECRET_PATTERNS:
            if pat.search(text):
                return True
        return False

    def test_pre_push_has_github_pattern(self):
        token = "ghp_" + "A" * 36
        self.assertTrue(self._any_match(token))

    def test_pre_push_has_aws_pattern(self):
        token = "AKIA" + "ABCDEFGHIJKLMNOP"
        self.assertTrue(self._any_match(token))

    def test_pre_push_env_assign_widened(self):
        line = ('AWS_SECRET_ACCESS_KEY='
                "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
        m = self.mod.ENV_ASSIGN.search(line)
        self.assertIsNotNone(m)


if __name__ == "__main__":
    unittest.main()

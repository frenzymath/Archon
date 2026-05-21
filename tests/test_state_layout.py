"""Tests for per-iteration state sidecars + config resolver.

Covers:

* ``resolve_recent_iter_window`` defaults and overrides.
* iter_state path helpers produce the right relative paths.
* ``init_iter_sidecar_dir`` creates the dir and is idempotent.
* ``read_recent_iter_sidecars`` returns at most ``window`` items,
  oldest-first, excluding the current iteration.
* ``format_recent_iter_sidecars_for_prompt`` truncates long content
  and omits empty sidecars.
* ``build_plan_prompt`` / ``build_review_prompt`` always emit the
  per-iter sidecar block and inject prior-iter content when present.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.commands.tooling.project_config import (
    ProjectConfig,
    resolve_recent_iter_window,
)
from archon.prompts import build_plan_prompt, build_review_prompt
from archon.state.iter_state import (
    IterSidecarSnapshot,
    format_recent_iter_sidecars_for_prompt,
    init_iter_sidecar_dir,
    iter_sidecar_dir,
    objectives_sidecar_path,
    plan_sidecar_path,
    read_recent_iter_sidecars,
    review_sidecar_path,
)


# ── config resolver ─────────────────────────────────────────────────


class RecentIterWindowTest(unittest.TestCase):
    def test_default_is_3(self):
        cfg = ProjectConfig()
        self.assertEqual(resolve_recent_iter_window(cfg), 3)

    def test_explicit_override(self):
        cfg = ProjectConfig(raw={'state': {'recent_iter_window': 5}})
        self.assertEqual(resolve_recent_iter_window(cfg), 5)

    def test_non_int_falls_back(self):
        cfg = ProjectConfig(raw={'state': {'recent_iter_window': 'oops'}})
        self.assertEqual(resolve_recent_iter_window(cfg), 3)


# ── iter_state paths ────────────────────────────────────────────────


class IterStatePathsTest(unittest.TestCase):
    def test_iter_dir_format(self):
        d = iter_sidecar_dir(Path('/p/.archon'), 7)
        self.assertEqual(str(d), '/p/.archon/iter/iter-007')

    def test_plan_path(self):
        p = plan_sidecar_path(Path('/p/.archon'), 12)
        self.assertEqual(str(p), '/p/.archon/iter/iter-012/plan.md')

    def test_review_path(self):
        p = review_sidecar_path(Path('/p/.archon'), 12)
        self.assertEqual(str(p), '/p/.archon/iter/iter-012/review.md')

    def test_objectives_path(self):
        p = objectives_sidecar_path(Path('/p/.archon'), 12)
        self.assertEqual(str(p), '/p/.archon/iter/iter-012/objectives.md')


# ── init_iter_sidecar_dir ───────────────────────────────────────────


class InitIterSidecarTest(unittest.TestCase):
    def test_creates_dir(self):
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            result = init_iter_sidecar_dir(sd, 5)
            self.assertTrue(result.is_dir())
            self.assertEqual(result.name, 'iter-005')

    def test_idempotent_preserves_content(self):
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            init_iter_sidecar_dir(sd, 5)
            (sd / 'iter' / 'iter-005' / 'plan.md').write_text('hello')
            init_iter_sidecar_dir(sd, 5)
            self.assertEqual(
                (sd / 'iter' / 'iter-005' / 'plan.md').read_text(), 'hello',
            )

    def test_creates_parent_iter_root(self):
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            self.assertFalse((sd / 'iter').exists())
            init_iter_sidecar_dir(sd, 1)
            self.assertTrue((sd / 'iter').is_dir())


# ── read_recent_iter_sidecars ───────────────────────────────────────


class ReadRecentIterSidecarsTest(unittest.TestCase):
    def _seed(self, sd: Path, iters_with_plan: dict[int, str]) -> None:
        for n, body in iters_with_plan.items():
            init_iter_sidecar_dir(sd, n)
            plan_sidecar_path(sd, n).write_text(body, encoding='utf-8')

    def test_empty_when_no_sidecars(self):
        with tempfile.TemporaryDirectory() as d:
            snaps = read_recent_iter_sidecars(Path(d), current_iter=5, window=3)
            self.assertEqual(snaps, [])

    def test_returns_window_oldest_first(self):
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            self._seed(sd, {1: 'p1', 2: 'p2', 3: 'p3', 4: 'p4', 5: 'p5'})
            snaps = read_recent_iter_sidecars(sd, current_iter=6, window=3)
            self.assertEqual([s.iter_num for s in snaps], [3, 4, 5])
            self.assertEqual(snaps[0].plan, 'p3')
            self.assertEqual(snaps[-1].plan, 'p5')

    def test_excludes_current_iter(self):
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            self._seed(sd, {1: 'p1', 2: 'p2', 3: 'p3'})
            snaps = read_recent_iter_sidecars(sd, current_iter=3, window=10)
            self.assertEqual([s.iter_num for s in snaps], [1, 2])

    def test_window_zero_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            self._seed(sd, {1: 'p1', 2: 'p2'})
            snaps = read_recent_iter_sidecars(sd, current_iter=3, window=0)
            self.assertEqual(snaps, [])

    def test_missing_files_yield_empty_strings(self):
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d)
            init_iter_sidecar_dir(sd, 1)
            review_sidecar_path(sd, 1).write_text('r1')
            snaps = read_recent_iter_sidecars(sd, current_iter=2, window=3)
            self.assertEqual(len(snaps), 1)
            self.assertEqual(snaps[0].plan, '')
            self.assertEqual(snaps[0].review, 'r1')


# ── format_recent_iter_sidecars_for_prompt ──────────────────────────


class FormatSidecarsTest(unittest.TestCase):
    def test_empty_input_returns_empty_string(self):
        self.assertEqual(format_recent_iter_sidecars_for_prompt([]), '')

    def test_only_empty_sidecars_returns_empty(self):
        snaps = [IterSidecarSnapshot(iter_num=1, plan='', review='', objectives='')]
        self.assertEqual(format_recent_iter_sidecars_for_prompt(snaps), '')

    def test_renders_plan_section_by_default(self):
        snaps = [IterSidecarSnapshot(iter_num=4, plan='decided X', review='', objectives='')]
        out = format_recent_iter_sidecars_for_prompt(snaps)
        self.assertIn('iter-004', out)
        self.assertIn('plan.md', out)
        self.assertIn('decided X', out)

    def test_truncates_long_sections(self):
        long = 'x' * 10000
        snaps = [IterSidecarSnapshot(iter_num=1, plan=long, review='', objectives='')]
        out = format_recent_iter_sidecars_for_prompt(snaps, per_section_max_chars=100)
        self.assertIn('(truncated)', out)
        self.assertLess(len(out), 400)

    def test_include_flags_select_sections(self):
        snaps = [IterSidecarSnapshot(
            iter_num=1, plan='P', review='R', objectives='O',
        )]
        plan_only = format_recent_iter_sidecars_for_prompt(
            snaps, include_plan=True, include_review=False, include_objectives=False,
        )
        self.assertIn('**plan.md**', plan_only)
        self.assertNotIn('**review.md**', plan_only)
        self.assertNotIn('**objectives.md**', plan_only)

        all_three = format_recent_iter_sidecars_for_prompt(
            snaps, include_plan=True, include_review=True, include_objectives=True,
        )
        self.assertIn('**plan.md**', all_three)
        self.assertIn('**review.md**', all_three)
        self.assertIn('**objectives.md**', all_three)


# ── prompt builder integration ──────────────────────────────────────


class PromptBuilderSidecarTest(unittest.TestCase):
    """Both prompt builders always emit the per-iter sidecar block."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.project = Path(self._td.name)
        self.state = self.project / '.archon'
        self.state.mkdir()
        (self.state / 'logs').mkdir()

    def tearDown(self):
        self._td.cleanup()

    def test_plan_prompt_emits_sidecar_block(self):
        prompt = build_plan_prompt(
            'p', self.project, self.state, 'init', iter_num=1,
        )
        self.assertIn('Per-iteration sidecars', prompt)
        self.assertIn('iter-001', prompt)
        self.assertIn('plan.md', prompt)

    def test_review_prompt_emits_sidecar_block(self):
        sd = self.state / 'proof-journal' / 'sessions' / 'session_1'
        sd.mkdir(parents=True)
        prompt = build_review_prompt(
            'p', self.project, self.state, 'init',
            session_num=1, session_dir=sd,
            attempts_file=sd / 'attempts.jsonl',
            combined_prover_log=self.state / 'logs' / 'iter-001' / 'prover.jsonl',
            iter_num=1,
        )
        self.assertIn('Per-iteration sidecars', prompt)
        self.assertIn('iter-001', prompt)
        self.assertIn('review.md', prompt)

    def test_plan_prompt_injects_prior_iter_content(self):
        init_iter_sidecar_dir(self.state, 1)
        plan_sidecar_path(self.state, 1).write_text(
            '# iter-001 plan\n\nDecided to refactor LineBundle.', encoding='utf-8',
        )
        prompt = build_plan_prompt(
            'p', self.project, self.state, 'init', iter_num=2,
        )
        self.assertIn('Decided to refactor LineBundle', prompt)


if __name__ == '__main__':
    unittest.main()

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from archon.multilane.config import load_multilane_config, redacted_lane_summary, resolve_runtime_paths
from archon.multilane.dispatch import build_assignment_prompt, build_jobs_from_progress, plan_assignments, preview_round
from archon.multilane.worktree import ensure_lane_worktree, prepare_lane, sync_lane_worktree
from archon.state import parse_objective_files
from archon.prompts import build_plan_prompt
from archon.session_log import read_last_session_end, session_end_indicates_success
from archon.commands.loop.lane_round import (
    assignment_code_snapshot_files,
    assignment_success,
    non_archon_dirty_files,
    select_writeback_rows,
)
from archon.commands.tooling.inner_git import InnerGit


def _bootstrap_inner_git_project(project: Path) -> InnerGit:
    """Initialize the project with a one-commit inner git so lane tests can branch from it.

    Mirrors what `archon init` does for the inner repo, minus the rest of
    the deterministic setup (lake, blueprint, …). We just need an inner
    repo with one commit on `main`.
    """
    project.mkdir(parents=True, exist_ok=True)
    (project / 'README.md').write_text('hello\n')
    inner = InnerGit(project)
    inner.init(initial_branch='main')
    inner.ensure_initial_commit('archon: test bootstrap')
    return inner



class MultiLaneConfigTests(unittest.TestCase):
    def test_load_json_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            path.write_text(json.dumps({
                'enabled': True,
                'base_ref': 'main',
                'lanes': [
                    {
                        'lane_id': 'claude-sonnet',
                        'provider': 'anthropic',
                        'worktree': {'path': '../wt-sonnet', 'branch': 'lane/sonnet'},
                        'execution': {'max_parallel_jobs': 2},
                    }
                ],
            }))
            config = load_multilane_config(path)
            self.assertTrue(config.enabled)
            self.assertEqual(config.base_ref, 'main')
            self.assertEqual(len(config.enabled_lanes()), 1)
            self.assertEqual(config.enabled_lanes()[0].worktree.branch, 'lane/sonnet')

    def test_local_override_and_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / 'config.json'
            local = root / 'config.local.json'
            base.write_text(json.dumps({
                'enabled': True,
                'lanes': [
                    {
                        'lane_id': 'kimi',
                        'provider': 'kimi',
                        'env': {'ANTHROPIC_AUTH_TOKEN': 'secret-token', 'ANTHROPIC_BASE_URL': 'https://api.kimi.com/coding/'},
                        'worktree': {'path': '../wt-kimi', 'branch': 'lane/kimi'},
                    }
                ]
            }))
            local.write_text(json.dumps({
                'lanes': {
                    'kimi': {
                        'claude_settings_path': '/tmp/kimi-settings.json',
                        'claude_config_dir': '/root/.claude-kimi',
                    }
                }
            }))
            config = load_multilane_config(base, local)
            lane = config.lane_by_id('kimi')
            self.assertIsNotNone(lane)
            assert lane is not None
            self.assertEqual(lane.claude_settings_path, '/tmp/kimi-settings.json')
            summary = redacted_lane_summary(lane)
            self.assertEqual(summary['env']['ANTHROPIC_AUTH_TOKEN'], '<redacted>')
            self.assertEqual(summary['claude_settings_path'], '<configured>')

    def test_runtime_paths_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / '.archon'
            paths = resolve_runtime_paths(state_dir)
            self.assertTrue(paths.runtime_dir.is_dir())
            self.assertTrue(paths.reports_dir.is_dir())
            self.assertTrue(paths.lanes_dir.is_dir())


class MultiLaneLoopGuardTests(unittest.TestCase):
    def test_assignment_success_requires_single_target_change(self):
        ok, reason = assignment_success(
            ok=True,
            assigned_file='Foo.lean',
            changed_files=['Foo.lean'],
            escaped_files=[],
            summary_path='/tmp/Foo.md',
        )
        self.assertTrue(ok)
        self.assertIsNone(reason)

        ok, reason = assignment_success(
            ok=True,
            assigned_file='Foo.lean',
            changed_files=[],
            escaped_files=[],
            summary_path='/tmp/Foo.md',
        )
        self.assertFalse(ok)
        self.assertEqual(reason, 'no_file_change')

        ok, reason = assignment_success(
            ok=True,
            assigned_file='Foo.lean',
            changed_files=['Foo.lean', 'Bar.lean'],
            escaped_files=[],
            summary_path='/tmp/Foo.md',
        )
        self.assertFalse(ok)
        self.assertEqual(reason, 'cross_file_change')

        ok, reason = assignment_success(
            ok=True,
            assigned_file='Foo.lean',
            changed_files=['Foo.lean'],
            escaped_files=['Other/Foo.lean'],
            summary_path='/tmp/Foo.md',
        )
        self.assertFalse(ok)
        self.assertEqual(reason, 'escaped_worktree')

    def test_assignment_success_rejects_remaining_sorry(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'Foo.lean'
            target.write_text("theorem foo : True := by\n  sorry\n")
            ok, reason = assignment_success(
                ok=True,
                assigned_file='Foo.lean',
                changed_files=['Foo.lean'],
                escaped_files=[],
                summary_path='/tmp/Foo.md',
                assigned_file_path=str(target),
            )
            self.assertFalse(ok)
            self.assertEqual(reason, 'placeholder_remaining')

    def test_assignment_code_snapshot_files_detects_escaped_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / 'main'
            lane = root / 'lane'
            main.mkdir()
            lane.mkdir()
            log_path = root / 'prover.jsonl'
            log_path.write_text(
                json.dumps({'event': 'code_snapshot', 'file': 'Foo.lean'}) + '\n' +
                json.dumps({'event': 'code_snapshot', 'file': '../main/Bar.lean'}) + '\n'
            )

            changed, escaped, source = assignment_code_snapshot_files(log_path, lane, main)
            self.assertEqual(changed, ['Foo.lean'])
            self.assertEqual(escaped, ['Bar.lean'])
            self.assertEqual(source, 'code_snapshot')

    def test_non_archon_dirty_files_filters_runtime_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'project'
            root.mkdir()
            subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True)
            subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True, capture_output=True)
            subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=root, check=True, capture_output=True)
            (root / '.archon').mkdir()
            (root / '.archon' / 'meta.json').write_text('{}')
            (root / 'Foo.lean').write_text('-- baseline\n')
            subprocess.run(['git', 'add', '.'], cwd=root, check=True, capture_output=True)
            subprocess.run(['git', 'commit', '-m', 'init'], cwd=root, check=True, capture_output=True)

            (root / '.archon' / 'meta.json').write_text('{"changed": true}')
            (root / 'Foo.lean').write_text('-- changed\n')

            self.assertEqual(non_archon_dirty_files(root), ['Foo.lean'])


class MultiLaneDispatchTests(unittest.TestCase):
    def test_assignment_prompt_overrides_result_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lane_path = root / 'lane-project'
            lane_path.mkdir()
            state_dir = root / '.archon'
            state_dir.mkdir()
            assignment = __import__('archon.multilane.types', fromlist=['LaneAssignment']).LaneAssignment(
                assignment_id='lane-a--job-001',
                lane_id='lane-a',
                job_id='job-001',
                assigned_file='Foo.lean',
                worktree_path=str(lane_path),
                log_path=str(root / 'log'),
                result_path=str(root / 'results' / 'Foo.md'),
                state_view_path=str(state_dir),
            )
            prompt = build_assignment_prompt(
                project_name='demo',
                lane_project_path=lane_path,
                state_dir=state_dir,
                stage='prover',
                assignment=assignment,
                iter_num=42,
            )
            self.assertIn('CRITICAL WORKTREE RULES', prompt)
            self.assertIn(str(root / 'results' / 'Foo.md'), prompt)
            self.assertIn('Your assigned file: Foo.lean', prompt)
            self.assertIn(str(state_dir), prompt)
            self.assertIn('Archon iteration: 042', prompt)

    def test_parse_objective_files_ignores_reference_mentions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            state_dir = project / '.archon'
            project.mkdir()
            state_dir.mkdir(parents=True)
            progress = state_dir / 'PROGRESS.md'
            progress.write_text(
                '# Project Progress\n\n'
                '## Current Stage\nprover\n\n'
                '## Current Objectives\n'
                '- **`Foo.lean`** — solve the main theorem\n'
                '- **`Bar/Baz.lean`** — use ideas from `Ref/Helper.lean` but do not edit it\n'
                '\n'
                'Strategy notes:\n'
                '1. compare with `Tail/Basic.lean`\n'
                '2. see `Tail/Summable.lean` for reusable lemmas\n'
                '\n'
                '## Next unlock layer\n'
                '- `Later.lean`\n'
            )
            (project / 'Foo.lean').write_text('-- foo\n')
            (project / 'Bar').mkdir()
            (project / 'Bar' / 'Baz.lean').write_text('-- baz\n')
            (project / 'Ref').mkdir()
            (project / 'Ref' / 'Helper.lean').write_text('-- helper\n')
            (project / 'Tail').mkdir()
            (project / 'Tail' / 'Basic.lean').write_text('-- basic\n')
            (project / 'Tail' / 'Summable.lean').write_text('-- summable\n')
            (project / 'Later.lean').write_text('-- later\n')

            files = parse_objective_files(progress, project)
            rels = [str(path.relative_to(project)) for path in files]
            self.assertEqual(rels, ['Bar/Baz.lean', 'Foo.lean'])
    def test_parse_objective_files_accepts_heading_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            state_dir = project / '.archon'
            project.mkdir()
            state_dir.mkdir(parents=True)
            progress = state_dir / 'PROGRESS.md'
            progress.write_text(
                '# Project Progress\n\n'
                '## Current Stage\nprover\n\n'
                '## Current Objectives\n'
                '### 1. **`Foo.lean`** — first target\n'
                '### 2. **`Bar/Baz.lean`** — second target\n'
                '- note: compare with `Tail/Basic.lean`\n'
            )
            (project / 'Foo.lean').write_text('-- foo\n')
            (project / 'Bar').mkdir()
            (project / 'Bar' / 'Baz.lean').write_text('-- baz\n')
            (project / 'Tail').mkdir()
            (project / 'Tail' / 'Basic.lean').write_text('-- basic\n')

            files = parse_objective_files(progress, project)
            rels = [str(path.relative_to(project)) for path in files]
            self.assertEqual(rels, ['Bar/Baz.lean', 'Foo.lean'])

    def test_parse_objective_files_skips_do_not_touch_headings(self):
        """Plan agents sometimes list off-limits files in `## Current Objectives`.
        Without filtering, the dispatcher fans out provers that immediately
        stop with no-op task results — wasted API time. Lines containing
        explicit stop markers ("DO NOT TOUCH", "off-limits") should be
        dropped from the objective list."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'project'
            state_dir = project / '.archon'
            project.mkdir()
            state_dir.mkdir(parents=True)
            progress = state_dir / 'PROGRESS.md'
            progress.write_text(
                '# Project Progress\n\n'
                '## Current Stage\nprover\n\n'
                '## Current Objectives\n'
                '### 1. **`SheafCompose.lean`** — fill the instance\n'
                '### 2. **`Functor.lean`** — fill the definition\n'
                '### 3. The protected files (`Genus.lean`, `Jacobian.lean`, `AbelJacobi.lean`) — DO NOT TOUCH\n'
                '- The bullet form is also off-limits: `Other.lean`\n'
            )
            for rel in (
                'SheafCompose.lean', 'Functor.lean',
                'Genus.lean', 'Jacobian.lean', 'AbelJacobi.lean',
                'Other.lean',
            ):
                (project / rel).write_text('-- stub\n')

            files = parse_objective_files(progress, project)
            rels = sorted(str(p.relative_to(project)) for p in files)
            self.assertEqual(rels, ['Functor.lean', 'SheafCompose.lean'])

    def test_iteration_runtime_paths_follow_requested_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            state_dir = project / '.archon'
            project.mkdir()
            state_dir.mkdir(parents=True)
            progress = state_dir / 'PROGRESS.md'
            progress.write_text(
                '# Project Progress\n\n'
                '## Current Stage\nprover\n\n'
                '## Current Objectives\n'
                '- work on `Foo.lean`\n'
            )
            (project / 'Foo.lean').write_text('-- foo\n')
            config_path = root / 'config.json'
            config_path.write_text(json.dumps({
                'enabled': True,
                'lanes': [
                    {
                        'lane_id': 'lane-a',
                        'provider': 'anthropic',
                        'worktree': {'path': '../wt-a', 'branch': 'lane/a'},
                    }
                ]
            }))
            config = load_multilane_config(config_path)
            _, assignments = preview_round(
                config=config,
                progress_file=progress,
                project_path=project,
                state_dir=state_dir,
                iteration=2,
                stage='prover',
            )
            self.assertEqual(assignments[0].job_id, 'job-002-001')
            self.assertIn('/iter-002/', assignments[0].log_path)
            self.assertIn('/iter-002/', assignments[0].result_path)
            self.assertIn('/iter-002/state', assignments[0].state_view_path)

    def test_preview_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            state_dir = project / '.archon'
            project.mkdir()
            state_dir.mkdir(parents=True)
            progress = state_dir / 'PROGRESS.md'
            progress.write_text(
                '# Project Progress\n\n'
                '## Current Stage\nprover\n\n'
                '## Current Objectives\n'
                '- work on `Foo.lean`\n'
                '- work on `Bar/Baz.lean`\n'
            )
            (project / 'Foo.lean').write_text('-- foo\n')
            (project / 'Bar').mkdir()
            (project / 'Bar' / 'Baz.lean').write_text('-- baz\n')
            config_path = root / 'config.json'
            config_path.write_text(json.dumps({
                'enabled': True,
                'lanes': [
                    {
                        'lane_id': 'lane-a',
                        'provider': 'anthropic',
                        'worktree': {'path': '../wt-a', 'branch': 'lane/a'},
                    },
                    {
                        'lane_id': 'lane-b',
                        'provider': 'openrouter',
                        'worktree': {'path': '../wt-b', 'branch': 'lane/b'},
                    }
                ]
            }))
            config = load_multilane_config(config_path)
            jobs = build_jobs_from_progress(
                progress_file=progress,
                project_path=project,
                iteration=1,
                stage='prover',
            )
            self.assertEqual(len(jobs), 2)
            assignments = plan_assignments(
                config=config,
                jobs=jobs,
                state_dir=state_dir,
                iteration=1,
            )
            self.assertEqual(len(assignments), 4)
            summary, preview_assignments = preview_round(
                config=config,
                progress_file=progress,
                project_path=project,
                state_dir=state_dir,
                iteration=1,
                stage='prover',
            )
            self.assertEqual(len(preview_assignments), 4)
            self.assertEqual(summary['assignment_count'], 4)
            self.assertEqual(summary['per_lane_assignments']['lane-a'], 2)
            self.assertEqual(summary['per_lane_assignments']['lane-b'], 2)




    def test_runner_session_end_success_heuristic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'log.jsonl'
            path.write_text(
                json.dumps({'event': 'text', 'content': 'hello'}) + '\n' +
                json.dumps({'event': 'session_end', 'summary': 'Done. Summary: build passes'}) + '\n'
            )
            row = read_last_session_end(path)
            self.assertIsNotNone(row)
            self.assertTrue(session_end_indicates_success(row))
            self.assertFalse(session_end_indicates_success({'summary': 'API Error: The server had an error while processing your request'}))



    def test_select_writeback_rows_prefers_successful_claude_lane(self):
        rows = [
            {'assignment_id': 'kimi--1', 'lane_id': 'kimi', 'success': True, 'assigned_file_only': True, 'verification_passed': True, 'assigned_file': 'B.lean', 'worktree_path': '/tmp/kimi'},
            {'assignment_id': 'claude-main--2', 'lane_id': 'claude-main', 'success': True, 'assigned_file_only': True, 'verification_passed': True, 'assigned_file': 'C.lean', 'worktree_path': '/tmp/claude'},
            {'assignment_id': 'claude-main--1', 'lane_id': 'claude-main', 'success': True, 'assigned_file_only': True, 'verification_passed': True, 'assigned_file': 'A.lean', 'worktree_path': '/tmp/claude'},
        ]
        selected = select_writeback_rows(rows, preferred_lane_id='claude-main', limit=1)
        self.assertEqual([row['assignment_id'] for row in selected], ['claude-main--1'])

class MultiLanePromptTests(unittest.TestCase):
    def test_build_plan_prompt_can_ignore_multilane(self):
        prompt = build_plan_prompt(
            'demo',
            Path('/tmp/project'),
            Path('/tmp/project/.archon'),
            'prover',
            7,
            ignore_multilane=True,
        )
        self.assertIn('IMPORTANT EXPERIMENTAL MULTI-LANE RULES', prompt)
        self.assertIn('Keep the plan lane-agnostic', prompt)
        self.assertIn('Archon iteration: 007', prompt)


class MultiLaneDashboardSummaryTests(unittest.TestCase):
    def test_read_multilane_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            archon_dir = Path(tmp) / '.archon'
            (archon_dir / 'multilane' / 'runtime').mkdir(parents=True)
            (archon_dir / 'multilane' / 'reports').mkdir(parents=True)
            (archon_dir / 'multilane' / 'lanes' / 'lane-a' / 'iter-002' / 'provers').mkdir(parents=True)
            (archon_dir / 'multilane' / 'lanes' / 'lane-a' / 'iter-002' / 'results').mkdir(parents=True)
            (archon_dir / 'multilane' / 'config.json').write_text(json.dumps({'enabled': True, 'lanes': []}))
            (archon_dir / 'multilane' / 'config.local.json').write_text('{}')
            (archon_dir / 'multilane' / 'runtime' / 'iter-002-assignments.jsonl').write_text(
                json.dumps({'lane_id': 'lane-a'}) + '\n' + json.dumps({'lane_id': 'lane-a'}) + '\n'
            )
            (archon_dir / 'multilane' / 'runtime' / 'iter-002-results.jsonl').write_text(
                json.dumps({'lane_id': 'lane-a', 'success': True}) + '\n'
            )
            (archon_dir / 'multilane' / 'reports' / 'iter-002-execution.md').write_text('# report\n')
            (archon_dir / 'multilane' / 'lanes' / 'lane-a' / 'iter-002' / 'provers' / 'Foo.jsonl').write_text('{}\n')
            (archon_dir / 'multilane' / 'lanes' / 'lane-a' / 'iter-002' / 'results' / 'Foo.md').write_text('ok\n')

            summary = json.loads(subprocess.run([
                'npx',
                '--yes',
                'tsx',
                '-e',
                "import { readMultiLaneSummary } from './src/archon/ui/server/src/utils/multilane.ts';\n"
                "console.log(JSON.stringify(readMultiLaneSummary(process.argv[1])));",
                str(archon_dir),
            ], cwd=str(Path(__file__).resolve().parent.parent), check=True, capture_output=True, text=True).stdout)

            self.assertTrue(summary['enabled'])
            self.assertEqual(summary['latestIteration']['slug'], 'iter-002')
            self.assertEqual(summary['latestIteration']['assignmentCount'], 2)
            self.assertEqual(summary['latestIteration']['resultCount'], 1)
            self.assertEqual(summary['latestIteration']['successCount'], 1)
            self.assertEqual(summary['latestIteration']['lanes'][0]['proverLogCount'], 1)

    def test_read_multilane_summary_respects_enabled_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            archon_dir = Path(tmp) / '.archon'
            (archon_dir / 'multilane').mkdir(parents=True)
            (archon_dir / 'multilane' / 'config.json').write_text(json.dumps({'enabled': False, 'lanes': []}))

            summary = json.loads(subprocess.run([
                'npx',
                '--yes',
                'tsx',
                '-e',
                "import { readMultiLaneSummary } from './src/archon/ui/server/src/utils/multilane.ts';\n"
                "console.log(JSON.stringify(readMultiLaneSummary(process.argv[1])));",
                str(archon_dir),
            ], cwd=str(Path(__file__).resolve().parent.parent), check=True, capture_output=True, text=True).stdout)

            self.assertFalse(summary['enabled'])


class MultiLaneWorktreeTests(unittest.TestCase):
    def test_ensure_lane_worktree_and_prepare_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            _bootstrap_inner_git_project(project)

            config_path = root / 'config.json'
            settings_path = root / 'kimi-settings.json'
            settings_path.write_text('{"env":{"ANTHROPIC_BASE_URL":"https://api.kimi.com/coding/"}}')
            config_path.write_text(json.dumps({
                'enabled': True,
                'base_ref': 'main',
                'lanes': [
                    {
                        'lane_id': 'kimi',
                        'provider': 'kimi',
                        'claude_settings_path': str(settings_path),
                        'worktree': {'path': '../wt-kimi', 'branch': 'lane/kimi'},
                    }
                ]
            }))
            config = load_multilane_config(config_path)
            lane = config.lane_by_id('kimi')
            assert lane is not None
            created = ensure_lane_worktree(project, lane, base_ref='main')
            self.assertTrue(created['created'])
            prepared = prepare_lane(project, lane, base_ref='main')
            self.assertTrue(Path(prepared['health']['path']).exists())
            self.assertTrue(Path(prepared['settings_local_json']).exists())

    def test_sync_lane_worktree_resets_to_main_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / 'project'
            inner = _bootstrap_inner_git_project(project)

            config_path = root / 'config.json'
            config_path.write_text(json.dumps({
                'enabled': True,
                'base_ref': 'HEAD',
                'lanes': [
                    {
                        'lane_id': 'kimi',
                        'provider': 'kimi',
                        'worktree': {'path': '../wt-kimi', 'branch': 'lane/kimi'},
                    }
                ]
            }))
            config = load_multilane_config(config_path)
            lane = config.lane_by_id('kimi')
            assert lane is not None
            ensure_lane_worktree(project, lane, base_ref='HEAD')

            (project / 'README.md').write_text('main update\n')
            inner.add_all()
            inner.commit('main update')
            expected = inner.head_sha(short=False)

            lane_path = (project / lane.worktree.path).resolve()
            (lane_path / 'README.md').write_text('lane drift\n')
            (lane_path / 'scratch.txt').write_text('temp\n')

            health = sync_lane_worktree(project, lane, base_ref='HEAD')
            actual = subprocess.run(['git', '-C', str(lane_path), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
            self.assertEqual(actual, expected)
            self.assertEqual(health['synced_to'], expected)
            self.assertFalse((lane_path / 'scratch.txt').exists())


if __name__ == '__main__':
    unittest.main()

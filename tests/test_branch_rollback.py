"""Tests for ``archon branch`` rollback on failed checkout.

The reviewer reproduced a corruption: SIGKILL mid-phase leaves the
inner-git index partially written. ``archon branch alt --from <sha>
--force`` then created the new branch, but ``git checkout -f`` failed
because of the corrupt index. The previous code left the new branch
pointing at a commit unreachable from any other ref and HEAD in a
half-moved state.

These tests pin the new behaviour:
  * ``--force`` on a dirty inner-git requires an extra confirmation.
  * A failed ``_switch`` rolls back: deletes the just-created branch
    and restores HEAD.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import typer

from archon.commands.branch import BranchCommand


class GateInnerDirtyTest(unittest.TestCase):

    def _cmd(self, *, force: bool) -> BranchCommand:
        return BranchCommand(
            "alt", "/tmp/proj", from_ref="abc1234", force=force,
        )

    def test_force_with_dirty_inner_requires_confirmation(self):
        cmd = self._cmd(force=True)
        # User declines → Exit(1)
        with patch("typer.confirm", return_value=False):
            with self.assertRaises(typer.Exit) as cm:
                cmd._gate_inner_dirty("hint")
            self.assertEqual(cm.exception.exit_code, 1)

    def test_force_with_dirty_inner_proceeds_on_confirm(self):
        cmd = self._cmd(force=True)
        with patch("typer.confirm", return_value=True):
            # Should not raise.
            cmd._gate_inner_dirty("hint")

    def test_no_force_dirty_inner_existing_behaviour(self):
        cmd = self._cmd(force=False)
        with patch("typer.confirm", return_value=False):
            with self.assertRaises(typer.Exit) as cm:
                cmd._gate_inner_dirty("hint")
            # Pre-existing semantics: clean cancel, exit 0.
            self.assertEqual(cm.exception.exit_code, 0)


class RollbackOnFailedCheckoutTest(unittest.TestCase):

    def test_rollback_deletes_branch_we_created(self):
        inner = MagicMock()
        BranchCommand._rollback(
            inner, "alt",
            created=True, pre_branch="main", pre_sha="deadbeefcafe",
        )
        inner.delete_branch.assert_called_once_with("alt", force=True)

    def test_rollback_does_not_delete_pre_existing_branch(self):
        inner = MagicMock()
        BranchCommand._rollback(
            inner, "alt",
            created=False, pre_branch="main", pre_sha="deadbeefcafe",
        )
        inner.delete_branch.assert_not_called()

    def test_rollback_restores_symbolic_ref_when_pre_branch_known(self):
        inner = MagicMock()
        BranchCommand._rollback(
            inner, "alt",
            created=True, pre_branch="main", pre_sha="deadbeefcafe",
        )
        # First positional arg to _run is the git args list.
        run_calls = [c.args[0] for c in inner._run.call_args_list]
        self.assertIn(
            ["symbolic-ref", "HEAD", "refs/heads/main"],
            run_calls,
            f"Expected symbolic-ref restore; got {run_calls!r}",
        )

    def test_rollback_falls_back_to_sha_when_detached(self):
        # pre_branch=None simulates a detached-HEAD pre-fork state.
        inner = MagicMock()
        BranchCommand._rollback(
            inner, "alt",
            created=True, pre_branch=None, pre_sha="deadbeefcafe",
        )
        run_calls = [c.args[0] for c in inner._run.call_args_list]
        self.assertIn(
            ["update-ref", "--no-deref", "HEAD", "deadbeefcafe"],
            run_calls,
        )

    def test_rollback_survives_delete_failure(self):
        inner = MagicMock()
        inner.delete_branch.side_effect = RuntimeError("boom")
        # Must not raise even when delete fails — caller is about to
        # re-raise the original checkout error.
        BranchCommand._rollback(
            inner, "alt",
            created=True, pre_branch="main", pre_sha="deadbeefcafe",
        )


class RunRollsBackOnSwitchFailureTest(unittest.TestCase):
    """End-to-end: ``run`` calls _rollback when _switch raises."""

    def _make_inner(self):
        inner = MagicMock()
        inner.is_dirty.return_value = False
        inner.has_branch.return_value = False
        inner.current_branch.return_value = "main"
        inner.head_sha.return_value = "deadbeefcafe"
        return inner

    def test_failed_switch_triggers_rollback_and_propagates_exit(self):
        cmd = BranchCommand(
            "alt", "/tmp/proj", from_ref="abc1234", force=False,
        )
        inner = self._make_inner()
        tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, tmp)
        resolved = Path(tmp)

        with patch("archon.commands.branch._resolve_project",
                   return_value=(resolved, inner)), \
             patch("archon.commands.branch.warn_if_mismatch"), \
             patch.object(BranchCommand, "_refuse_if_outer_dirty"), \
             patch.object(BranchCommand, "_drop_stale_state_dirs"), \
             patch.object(BranchCommand, "_switch",
                          side_effect=typer.Exit(1)) as p_switch, \
             patch.object(BranchCommand, "_rollback") as p_rollback:
            with self.assertRaises(typer.Exit):
                cmd.run()
            p_switch.assert_called_once()
            p_rollback.assert_called_once()
            # Confirm rollback got the right "created=True" hint.
            kwargs = p_rollback.call_args.kwargs
            self.assertTrue(kwargs["created"])
            self.assertEqual(kwargs["pre_branch"], "main")
            self.assertEqual(kwargs["pre_sha"], "deadbeefcafe")


def _rmtree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

"""Regression: the inner repo must never track its own ``git-dir``.

``_stage_all`` skips ``git-dir`` when force-adding ``.archon/`` children, but
the plain ``git add -A`` it runs first will stage anything the outer
``.gitignore`` doesn't exclude. When a project has no outer ``.gitignore``
rule for ``.archon/`` (e.g. no outer git repo, or a pre-rule upgrade) that
``git add -A`` used to commit ``.archon/git-dir/`` into the inner repo itself:
every commit then churned the index/refs/objects, leaving the tree
*perpetually dirty*. An in-place merge would commit, yet ``is_dirty()`` stayed
True and ``archon branch`` would refuse to settle.

The fix lists ``.archon/git-dir/`` in the inner repo's ``info/exclude`` so the
protection is self-contained — independent of the outer ``.gitignore``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archon.commands.tooling.inner_git import InnerGit


def _has_git() -> bool:
    return InnerGit.available()


pytestmark = pytest.mark.skipif(not _has_git(), reason="git not on PATH")


def _project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "MyLib").mkdir(parents=True)
    (proj / "MyLib" / "B.lean").write_text("theorem goal : True := trivial\n")
    (proj / ".archon").mkdir(parents=True)
    (proj / ".archon" / "PROGRESS.md").write_text("## obj\n")
    # Deliberately NO outer .gitignore — this is the unprotected case.
    return proj


def test_commit_leaves_clean_tree_without_outer_gitignore(tmp_path: Path):
    proj = _project(tmp_path)
    inner = InnerGit(proj)
    inner.init()
    inner.ensure_initial_commit("init")

    (proj / "MyLib" / "B.lean").write_text(
        "theorem goal : True := trivial\n-- merged improvement\n"
    )
    assert inner.commit("archon[merge]: merged A into B (in-place)") is True

    # The whole point: a fresh commit must settle, not stay dirty because the
    # inner git-dir is tracking itself.
    assert inner.is_dirty() is False


def test_inner_git_dir_never_tracked(tmp_path: Path):
    proj = _project(tmp_path)
    inner = InnerGit(proj)
    inner.init()
    inner.ensure_initial_commit("init")
    inner.commit("second", allow_empty=True)

    tracked = inner._run(["ls-files"]).stdout.split()
    assert not any(t.startswith(".archon/git-dir/") for t in tracked), tracked
    # The real project files are still tracked.
    assert "MyLib/B.lean" in tracked
    assert ".archon/PROGRESS.md" in tracked


def test_default_excludes_list_git_dir():
    assert ".archon/git-dir/\n" in InnerGit._DEFAULT_EXCLUDES


def test_ensure_excludes_migrates_old_repo(tmp_path: Path):
    """An existing repo with stale excludes (no git-dir rule) is migrated and
    a subsequent commit settles clean."""
    proj = _project(tmp_path)
    inner = InnerGit(proj)
    inner.init()

    # Simulate a pre-fix repo: strip the git-dir rule from info/exclude.
    exclude = inner.git_dir / "info" / "exclude"
    stale = exclude.read_text().replace(".archon/git-dir/\n", "")
    exclude.write_text(stale)

    assert inner.ensure_excludes() is True  # rewrites to current defaults
    assert ".archon/git-dir/" in exclude.read_text()

    inner.ensure_initial_commit("init")
    (proj / "MyLib" / "B.lean").write_text("theorem goal : True := trivial\n-- x\n")
    inner.commit("edit")
    assert inner.is_dirty() is False

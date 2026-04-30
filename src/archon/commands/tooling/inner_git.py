"""Archon's private git repo, nested inside `.archon/git-dir`.

The inner repo shares a working tree with the outer (mathematician's) repo,
but uses a separate git directory. Every `git` call is routed through this
class, which sets `GIT_DIR` and `GIT_WORK_TREE` explicitly.

Key design choices:
- `GIT_DIR=<project>/.archon/git-dir` — inner commits stay private. The
  directory is named `git-dir` rather than `.git` so the inner repo
  doesn't see it as a nested-submodule boundary when it tracks the
  project root (`git add -A` would otherwise fail with
  "does not have a commit checked out").
- `GIT_WORK_TREE=<project>` — the same files on disk as the outer repo.
  An `archon branch` switch therefore actually rewrites the mathematician's
  working copy, which is the intended behavior: "switch to strategy B"
  means "put strategy B's files on disk".
- The outer repo's `.gitignore` lists `.archon/`, so the inner git
  directory is invisible to the outer repo.
- This file never touches the outer `.git` directory. The one exception
  needed for bootstrap (`leanblueprint new` requires an outer commit)
  lives in project.py, not here.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


class InnerGitError(RuntimeError):
    def __init__(self, args: list[str], stdout: str, stderr: str, returncode: int):
        super().__init__(
            f"git {' '.join(args)} exited with code {returncode}\n"
            f"stdout: {stdout.strip()}\nstderr: {stderr.strip()}"
        )
        self.args = args
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class InnerGit:
    """Thin wrapper around `git` that always operates on the inner repo.

    Safe to construct even if the inner repo does not exist yet — call
    `init()` before doing anything else.
    """

    def __init__(
        self,
        project_path: str | Path,
        bot_name: str = "Archon",
        bot_email: str = "archon@frenzymath.com",
    ):
        self.project_path = Path(project_path).resolve()
        self.state_dir = self.project_path / ".archon"
        self.git_dir = self.state_dir / "git-dir"

        self.env = os.environ.copy()
        self.env.update({
            "GIT_DIR":            str(self.git_dir),
            "GIT_WORK_TREE":      str(self.project_path),
            "GIT_AUTHOR_NAME":    bot_name,
            "GIT_AUTHOR_EMAIL":   bot_email,
            "GIT_COMMITTER_NAME": bot_name,
            "GIT_COMMITTER_EMAIL": bot_email,
        })

    # ── low-level ─────────────────────────────────────────────────────

    @staticmethod
    def available() -> bool:
        return shutil.which("git") is not None

    def _run(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", *args],
            cwd=str(self.project_path),
            env=self.env,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise InnerGitError(args, result.stdout, result.stderr, result.returncode)
        return result

    # ── introspection ─────────────────────────────────────────────────

    def is_initialized(self) -> bool:
        return self.git_dir.is_dir()

    def has_commits(self) -> bool:
        if not self.is_initialized():
            return False
        r = self._run(["rev-parse", "HEAD"], check=False)
        return r.returncode == 0

    def is_dirty(self) -> bool:
        """True if tracked files differ from HEAD (ignores untracked)."""
        if not self.is_initialized():
            return False
        r = self._run(["status", "--porcelain", "--untracked-files=no"], check=False)
        return bool(r.stdout.strip())

    def current_branch(self) -> str | None:
        if not self.is_initialized():
            return None
        r = self._run(["rev-parse", "--abbrev-ref", "HEAD"], check=False)
        return r.stdout.strip() if r.returncode == 0 else None

    def list_branches(self) -> list[str]:
        if not self.is_initialized():
            return []
        r = self._run(["for-each-ref", "--format=%(refname:short)", "refs/heads/"], check=False)
        if r.returncode != 0:
            return []
        return [b.strip() for b in r.stdout.splitlines() if b.strip()]

    def log_oneline(self, n: int = 20) -> str:
        if not self.is_initialized():
            return ""
        r = self._run(
            ["log", "--graph", "--oneline", "--all", f"-n{n}"],
            check=False,
        )
        return r.stdout

    def head_sha(self, short: bool = True) -> str | None:
        if not self.has_commits():
            return None
        args = ["rev-parse"]
        if short:
            args.append("--short")
        args.append("HEAD")
        r = self._run(args, check=False)
        return r.stdout.strip() or None

    def last_commit_subject(self) -> str | None:
        if not self.has_commits():
            return None
        r = self._run(["log", "-1", "--pretty=%s"], check=False)
        return r.stdout.strip() or None

    # ── setup ─────────────────────────────────────────────────────────

    def init(self, initial_branch: str = "main") -> bool:
        """Initialize the inner repo if missing. Returns True iff created."""
        if self.is_initialized():
            return False
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # `git init --bare` creates a pure git dir — we'll then flip
        # core.bare=false and use it with GIT_DIR + GIT_WORK_TREE. This
        # avoids `--separate-git-dir`, which leaves a `.git` pointer file
        # at the worktree root that would confuse the outer mathematician
        # git repo.
        #
        # Pass a clean env (no GIT_DIR/GIT_WORK_TREE) so git doesn't
        # get confused while initializing.
        clean_env = {k: v for k, v in os.environ.items()
                     if not k.startswith("GIT_")}
        r = subprocess.run(
            ["git", "init", "--bare", "-q", f"--initial-branch={initial_branch}",
             str(self.git_dir)],
            env=clean_env,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            # Older git fallback (< 2.28 — no --initial-branch flag).
            r = subprocess.run(
                ["git", "init", "--bare", "-q", str(self.git_dir)],
                env=clean_env,
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                raise InnerGitError(
                    ["init"], r.stdout,
                    r.stderr or "git init failed",
                    r.returncode,
                )

        # --bare repos have core.bare=true, but we use this as a regular
        # repo with an external worktree. Flip the flag.
        self._run(["config", "core.bare", "false"])

        self._write_default_excludes()
        return True

    # The inner repo must TRACK .archon/ so `archon branch` (time-travel)
    # can restore iteration logs, proof-journal sessions, PROGRESS.md,
    # task_*.md, and Claude's raw session streams at any past commit.
    #
    # Tricky part: the outer project's .gitignore usually lists `.archon/`
    # (to hide agent state from the mathematician's git). `.gitignore`
    # files are worktree-level — any `GIT_DIR` operating on that worktree
    # reads them. And their precedence BEATS info/exclude, so we can't
    # override the outer ignore from here. Instead, `_stage_all` below
    # force-adds .archon/ children with `git add -f`, which bypasses the
    # ignore entirely.
    #
    # Build artifacts and OS junk stay excluded — they're regenerable
    # and potentially huge.
    _DEFAULT_EXCLUDES = (
        "# Archon inner-repo excludes — managed by InnerGit._write_default_excludes\n"
        "# .archon/ tracking is handled by _stage_all (git add -f), not here:\n"
        "# outer .gitignore's `.archon/` rule wins over info/exclude, so any\n"
        "# `!.archon/` negation at this level would be silently ignored.\n"
        "# Build artifacts and OS junk.\n"
        ".lake/\n"
        "lake-packages/\n"
        ".DS_Store\n"
        "*.pyc\n"
        "__pycache__/\n"
        ".git/\n"
    )

    def _write_default_excludes(self) -> None:
        """Write the inner repo's info/exclude from defaults (idempotent)."""
        info_dir = self.git_dir / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
        exclude_file = info_dir / "exclude"
        exclude_file.write_text(self._DEFAULT_EXCLUDES, encoding="utf-8")

    def ensure_excludes(self) -> bool:
        """Refresh info/exclude to current defaults on an existing repo.

        Returns True if the file was changed. Used to migrate projects
        that were initialized under older archon versions (which
        excluded ``.archon/logs/**/*.raw.jsonl`` from tracking — losing
        the raw Claude session streams on time-travel).
        """
        if not self.is_initialized():
            return False
        exclude_file = self.git_dir / "info" / "exclude"
        try:
            current = exclude_file.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current == self._DEFAULT_EXCLUDES:
            return False
        self._write_default_excludes()
        return True

    def ensure_initial_commit(self, message: str = "archon: initial state") -> bool:
        """Make the first commit iff the inner repo has none yet.

        Stages everything the inner repo can see (respecting excludes) so
        the first commit captures the project's current state at init time.
        """
        if self.has_commits():
            return False
        self._stage_all()
        r = self._run(["status", "--porcelain"], check=False)
        if not r.stdout.strip():
            self._run(["commit", "--allow-empty", "-q", "-m", message])
        else:
            self._run(["commit", "-q", "-m", message])
        return True

    # ── actions ───────────────────────────────────────────────────────

    def _stage_all(self) -> None:
        """Stage the entire working tree, force-tracking .archon/.

        Plain `git add -A` respects the outer project's `.gitignore`,
        which typically excludes `.archon/` — so the inner repo would
        commit zero agent state and `archon branch` time-travel would
        restore nothing but .lean files. To bypass that, we force-add
        each child of `.archon/` explicitly (except `git-dir`, which is
        the inner repo itself and must never be committed into itself).
        This is per-child rather than blanket `git add -f .archon/` so
        the git-dir exclusion still holds when -f overrides ignores.
        """
        self._run(["add", "-A"])
        archon_dir = self.project_path / ".archon"
        if not archon_dir.is_dir():
            return
        for entry in sorted(archon_dir.iterdir()):
            if entry.name == "git-dir":
                continue
            # -f bypasses outer .gitignore; -A picks up new + modified + deleted.
            self._run(["add", "-f", "-A", "--", f".archon/{entry.name}"])

    def add_all(self) -> None:
        if not self.is_initialized():
            return
        self._stage_all()

    def commit(self, message: str, allow_empty: bool = False) -> bool:
        """Stage everything and commit. Returns True iff a commit was made."""
        if not self.is_initialized():
            return False
        self._stage_all()
        r = self._run(["status", "--porcelain"], check=False)
        if not r.stdout.strip() and not allow_empty:
            return False
        args = ["commit", "-q", "-m", message]
        if allow_empty:
            args.insert(1, "--allow-empty")
        self._run(args)
        return True

    def commit_phase(
        self,
        *,
        iter_num: int,
        phase: str,
        summary: str,
        file_slug: str | None = None,
    ) -> tuple[bool, str | None]:
        """Commit in the standard archon[NNN/phase(/slug)]: summary format.

        Returns (made_commit, short_sha).
        """
        prefix = f"archon[{iter_num:03d}/{phase}"
        if file_slug:
            prefix += f"/{file_slug}"
        prefix += "]"
        # Cap summary length so `git log --oneline` stays readable.
        summary = summary.strip().splitlines()[0] if summary else ""
        summary = summary[:140]
        msg = f"{prefix}: {summary}" if summary else prefix

        made = self.commit(msg)
        if not made:
            return False, None
        return True, self.head_sha(short=True)

    def create_branch(self, name: str, *, from_ref: str = "HEAD") -> None:
        """Create a new branch at `from_ref`. Fails if the name already exists."""
        if not self.is_initialized():
            raise InnerGitError(
                ["branch", name],
                "", "inner repo not initialized", 1,
            )
        # Normalize to a safe branch name.
        safe = _safe_branch_name(name)
        if safe in self.list_branches():
            raise InnerGitError(
                ["branch", safe],
                "", f"branch already exists: {safe}", 1,
            )
        self._run(["branch", safe, from_ref])

    def checkout(self, name: str, *, force: bool = False) -> None:
        """Switch to an existing branch or commit. Rewrites working tree.

        When ``force=True``, passes ``-f`` to ``git checkout`` so local
        modifications to tracked files are overwritten. Untracked files
        from later commits are NOT removed by checkout itself — call
        :py:meth:`clean_untracked` after to make the tree fully match the
        target.
        """
        if not self.is_initialized():
            raise InnerGitError(
                ["checkout", name], "", "inner repo not initialized", 1,
            )
        safe = _safe_branch_name(name)
        args = ["checkout"]
        if force:
            args.append("-f")
        args.append(safe)
        self._run(args)

    def has_branch(self, name: str) -> bool:
        return _safe_branch_name(name) in self.list_branches()


# ── helpers ───────────────────────────────────────────────────────────


_BRANCH_SANITIZE = re.compile(r"[^A-Za-z0-9._/-]+")


def _safe_branch_name(name: str) -> str:
    """Convert a freeform strategy name into a git-safe branch name."""
    name = name.strip().replace(" ", "-")
    name = _BRANCH_SANITIZE.sub("-", name)
    name = name.strip("-/.")
    return name or "unnamed-strategy"

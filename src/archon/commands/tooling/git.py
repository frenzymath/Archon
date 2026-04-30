"""Thin, idempotent wrapper around `git`.

The class sets committer identity via env vars so it works even on machines
where the user hasn't configured a global `user.name` / `user.email`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    def __init__(self, args: list[str], stdout: str, stderr: str, returncode: int):
        super().__init__(
            f"git {' '.join(args)} exited with code {returncode}\n"
            f"stdout: {stdout.strip()}\nstderr: {stderr.strip()}"
        )
        self.args = args
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class Git:
    def __init__(
        self,
        repo_path: str | Path,
        bot_name: str = "Archon",
        bot_email: str = "archon@frenzymath.com",
        auto_init: bool = True,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.repo_path.mkdir(parents=True, exist_ok=True)
        self.env = os.environ.copy()
        self.env.update({
            "GIT_AUTHOR_NAME":    bot_name,
            "GIT_AUTHOR_EMAIL":   bot_email,
            "GIT_COMMITTER_NAME": bot_name,
            "GIT_COMMITTER_EMAIL": bot_email,
        })

        if auto_init and not (self.repo_path / ".git").is_dir():
            self._run(["init", "-q"])

    # ── low-level ─────────────────────────────────────────────────────

    @staticmethod
    def available() -> bool:
        return shutil.which("git") is not None

    def _run(self, args: list[str], check: bool = True, silent: bool = False) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", *args],
            cwd=str(self.repo_path),
            env=self.env,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0 and not silent:
            raise GitError(args, result.stdout, result.stderr, result.returncode)
        return result

    # ── introspection ─────────────────────────────────────────────────

    def is_repo(self) -> bool:
        return (self.repo_path / ".git").is_dir()

    def has_commits(self) -> bool:
        r = self._run(["rev-parse", "HEAD"], check=False)
        return r.returncode == 0

    def is_dirty(self) -> bool:
        """True if there are uncommitted changes (staged or unstaged)."""
        r = self._run(["status", "--porcelain"], check=False)
        return bool(r.stdout.strip())

    def current_branch(self) -> str | None:
        r = self._run(["rev-parse", "--abbrev-ref", "HEAD"], check=False)
        return r.stdout.strip() if r.returncode == 0 else None

    # ── actions ───────────────────────────────────────────────────────

    def ensure_initial_commit(self, message: str = "first commit") -> bool:
        """Create an initial (empty) commit iff the repo has no commits yet.

        Returns True if a commit was made, False if one already existed.
        """
        if self.has_commits():
            return False
        self._run(["commit", "--allow-empty", "-q", "-m", message])
        return True

    def add_and_commit(self, message: str, paths: list[str] | None = None) -> bool:
        """Stage and commit. Returns True if a commit was made, False if clean.

        If `paths` is None, stages everything (`git add .`).
        """
        if paths:
            self._run(["add", "--", *paths])
        else:
            self._run(["add", "."])

        status = self._run(["status", "--porcelain"]).stdout.strip()
        if not status:
            return False
        self._run(["commit", "-q", "-m", message])
        return True

    def ensure_gitignore_entry(self, entry: str, comment: str | None = None) -> bool:
        """Append `entry` to .gitignore if not already present.

        Returns True if the file was modified.
        """
        gi = self.repo_path / ".gitignore"
        if gi.exists():
            lines = [l.strip() for l in gi.read_text(encoding="utf-8").splitlines()]
            if entry in lines:
                return False
            with gi.open("a", encoding="utf-8") as f:
                if comment:
                    f.write(f"\n# {comment}\n")
                else:
                    f.write("\n")
                f.write(f"{entry}\n")
        else:
            with gi.open("w", encoding="utf-8") as f:
                if comment:
                    f.write(f"# {comment}\n")
                f.write(f"{entry}\n")
        return True

    def log_oneline(self, n: int = 20) -> str:
        return self._run(["log", "--graph", "--oneline", "--all", f"-n{n}"]).stdout
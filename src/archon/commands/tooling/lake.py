"""Thin, idempotent wrapper around the `lake` CLI.

This class is intentionally *mechanical*: every method either performs a
deterministic action or reports what it found. Decisions about *whether*
to call these methods live in `ProjectBootstrap`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class LakeError(RuntimeError):
    """A `lake` invocation failed with a non-zero exit code."""

    def __init__(self, args: list[str], stdout: str, stderr: str, returncode: int):
        super().__init__(
            f"lake {' '.join(args)} exited with code {returncode}\n"
            f"stdout: {stdout.strip()}\nstderr: {stderr.strip()}"
        )
        self.args = args
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@dataclass
class LakefileInfo:
    """Describes what lakefile, if any, is present."""
    path: Path | None       # absolute path, or None if no lakefile exists
    kind: str | None        # "lean" | "toml" | None
    has_mathlib: bool       # True iff mathlib is already declared as a dep
    project_name: str | None  # extracted from the lakefile if possible


class Lake:
    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).resolve()
        self.repo_path.mkdir(parents=True, exist_ok=True)
        self.env = os.environ.copy()

    # ── low-level ─────────────────────────────────────────────────────

    @staticmethod
    def available() -> bool:
        return shutil.which("lake") is not None

    def _run(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["lake", *args],
            cwd=str(self.repo_path),
            env=self.env,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            raise LakeError(args, result.stdout, result.stderr, result.returncode)
        return result

    # ── introspection ─────────────────────────────────────────────────

    def lakefile_info(self) -> LakefileInfo:
        """Detect which lakefile exists and what it declares.

        This is deliberately regex-based. It isn't a full parser; it just
        answers the three questions init needs: do we have a lakefile, is
        mathlib already a dep, what's the project name.
        """
        lean_path = self.repo_path / "lakefile.lean"
        toml_path = self.repo_path / "lakefile.toml"

        if lean_path.exists():
            text = lean_path.read_text(encoding="utf-8", errors="replace")
            return LakefileInfo(
                path=lean_path,
                kind="lean",
                has_mathlib=bool(re.search(r"require\s+mathlib\b", text)),
                project_name=_extract_lean_package_name(text),
            )

        if toml_path.exists():
            text = toml_path.read_text(encoding="utf-8", errors="replace")
            return LakefileInfo(
                path=toml_path,
                kind="toml",
                has_mathlib=bool(re.search(r'name\s*=\s*"mathlib"', text)),
                project_name=_extract_toml_package_name(text),
            )

        return LakefileInfo(path=None, kind=None, has_mathlib=False, project_name=None)

    def has_project(self) -> bool:
        return self.lakefile_info().path is not None

    # ── actions ───────────────────────────────────────────────────────

    def init_project(
        self,
        project_name: str | None = None,
        template: str | None = None,
        if_absent: bool = True,
    ) -> str:
        """Run `lake init`. Idempotent when `if_absent=True`.

        `template` may be "math" for a Mathlib-oriented project. Pass None
        to use lake's default.
        """
        info = self.lakefile_info()
        if info.path is not None and if_absent:
            return f"Lake project already initialized ({info.kind}): {info.path.name}"

        name = project_name or _slugify(self.repo_path.name) or "project"
        args = ["init", name]
        if template:
            args.append(template)
        result = self._run(args)
        return result.stdout.strip()

    def update(self) -> str:
        return self._run(["update"]).stdout.strip()

    def build(self, target: str = "") -> str:
        args = ["build"]
        if target:
            args.append(target)
        return self._run(args).stdout.strip()

    def exe(self, exe_name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return self._run(["exe", exe_name, *args], check=check)

    def get_mathlib_cache(self) -> str:
        """Download precompiled olean files. No-op if cache exe isn't present.

        Returns the stdout of `lake exe cache get`, or a note if cache is
        unavailable (e.g. Mathlib not yet resolved).
        """
        result = self.exe("cache", "get", check=False)
        if result.returncode != 0:
            return f"(mathlib cache unavailable: {result.stderr.strip() or 'unknown'})"
        return result.stdout.strip()

    def add_mathlib_dependency(self, if_absent: bool = True) -> bool:
        """Ensure Mathlib is declared in the lakefile.

        Returns True if the lakefile was modified (and an `update`+cache
        should follow), False if Mathlib was already present.
        """
        info = self.lakefile_info()
        if info.path is None:
            raise FileNotFoundError(
                f"No lakefile in {self.repo_path}. Call init_project() first."
            )

        if info.has_mathlib and if_absent:
            return False

        if info.kind == "lean":
            with info.path.open("a", encoding="utf-8") as f:
                f.write(
                    '\nrequire mathlib from git '
                    '"https://github.com/leanprover-community/mathlib4.git"\n'
                )
        elif info.kind == "toml":
            with info.path.open("a", encoding="utf-8") as f:
                f.write(
                    '\n[[require]]\n'
                    'name = "mathlib"\n'
                    'git = "https://github.com/leanprover-community/mathlib4.git"\n'
                )
        return True


# ── helpers ───────────────────────────────────────────────────────────


def _slugify(name: str) -> str:
    """Convert a directory name into a valid Lean package identifier.

    Lake package names must start with a letter. We replace separators with
    underscores and strip invalid chars.
    """
    name = re.sub(r"[^\w]+", "_", name).strip("_")
    if not name:
        return ""
    if not name[0].isalpha():
        name = "p_" + name
    return name


def _extract_lean_package_name(text: str) -> str | None:
    m = re.search(r"package\s+([A-Za-z_][\w]*)", text)
    return m.group(1) if m else None


def _extract_toml_package_name(text: str) -> str | None:
    m = re.search(r'^\s*name\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None
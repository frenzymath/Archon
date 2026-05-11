#!/usr/bin/env python3
"""Generic Archon subagent wrapper — invoked by Claude in the autonomous loop.

This single source file is installed under three role-specific names by
``SkillsStep`` at ``archon init`` time::

    .claude/tools/archon-refactor-agent.py
    .claude/tools/archon-analogy-agent.py
    .claude/tools/archon-challenger-agent.py

Each invocation derives its role from ``sys.argv[0]`` so we don't have
to maintain three near-identical wrappers. Heavy logic stays in the
archon package; this script's only job is to forward the directive to
``archon subagent <role>``.

Usage (Claude calls one of the three names via Bash)::

    python3 .claude/tools/archon-<role>-agent.py \\
        --slug <slug> --directive-file <path>

Iteration number comes from ``ARCHON_ITER_NUM`` (set by the loop's plan
phase). The script fails loudly if it's missing rather than silently
defaulting, because a wrong iter_num routes the JSONL log to the wrong
directory.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


_VALID_ROLES = ("refactor", "analogy", "challenger")


def _detect_role() -> str:
    """Derive the role from this script's own filename.

    The installer creates ``archon-<role>-agent.py``; we strip the
    ``archon-`` prefix and ``-agent`` suffix and check the result is one
    of the three known roles.
    """
    stem = Path(sys.argv[0]).stem
    if stem.startswith("archon-") and stem.endswith("-agent"):
        role = stem[len("archon-") : -len("-agent")]
        if role in _VALID_ROLES:
            return role
    valid = ", ".join(_VALID_ROLES)
    print(
        f"Cannot derive subagent role from script name {sys.argv[0]!r}. "
        f"Expected archon-<role>-agent.py with role in: {valid}",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> int:
    role = _detect_role()

    p = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        description=f"Invoke the Archon {role} subagent on a directive file.",
    )
    p.add_argument("--slug", required=True)
    p.add_argument("--directive-file", required=True)
    args = p.parse_args()

    if not shutil.which("archon"):
        print(
            "archon CLI not found on PATH. Install Archon or activate "
            "its venv before running the loop.",
            file=sys.stderr,
        )
        return 1

    iter_num = os.environ.get("ARCHON_ITER_NUM")
    if not iter_num:
        print(
            "ARCHON_ITER_NUM not set in environment. This script is "
            "meant to be invoked by the Archon loop, which sets it "
            "before launching Claude.",
            file=sys.stderr,
        )
        return 1

    return subprocess.run([
        "archon", "subagent", role,
        "--project-path", os.getcwd(),
        "--slug", args.slug,
        "--directive-file", args.directive_file,
        "--iter-num", iter_num,
    ]).returncode


if __name__ == "__main__":
    sys.exit(main())

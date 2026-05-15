#!/usr/bin/env python3
"""Generic Archon subagent wrapper — invoked by Claude in the autonomous loop.

Installed once at ``.claude/tools/archon-subagent.py``. There is no
per-role script anymore: the role comes from ``--name <subagent>``,
which the archon CLI looks up in the descriptor registry
(``.archon/subagents/<name>.md`` + built-in defaults).

Usage (Claude calls this via Bash)::

    python3 .claude/tools/archon-subagent.py \\
        --name <subagent-name> \\
        --slug <slug> \\
        --directive-file <path> \\
        [--write-domain <glob>]...

Hierarchical dispatch:

* The plan agent invokes this wrapper directly; the wrapper sees no
  ``ARCHON_SUBAGENT_SLUG`` in env and passes ``--parent-slug _root``.
* When a subagent (e.g. coordinator) spawns a child via Bash, the
  parent subagent's slug is exported in env by ``Subagent.run``; the
  wrapper picks it up and forwards.

Iteration number comes from ``ARCHON_ITER_NUM`` (set by the loop's
plan phase). The script fails loudly if it's missing rather than
silently defaulting, because a wrong iter_num would route the JSONL
log to the wrong directory.
"""

import argparse
import os
import shutil
import subprocess
import sys


_PARENT_SLUG_ENV_VAR = "ARCHON_SUBAGENT_SLUG"
_ROOT_PARENT_SLUG = "_root"


def main() -> int:
    p = argparse.ArgumentParser(
        prog="archon-subagent.py",
        description="Invoke an Archon subagent on a directive file.",
    )
    p.add_argument(
        "--name", required=True,
        help="Name of the subagent to invoke. Must match a descriptor "
             "in `.archon/subagents/<name>.md` or a built-in default.",
    )
    p.add_argument("--slug", required=True)
    p.add_argument("--directive-file", required=True)
    p.add_argument(
        "--write-domain", action="append", default=[],
        help="Glob pattern this subagent is allowed to write to. "
             "Repeat for multiple. Validated against the parent's "
             "recorded domain.",
    )
    p.add_argument(
        "--parent-slug", default=None,
        help="Slug of the subagent that spawned this one. Usually left "
             "unset — the wrapper reads ARCHON_SUBAGENT_SLUG from env "
             "and uses that, or '_root' for plan-agent-launched calls.",
    )
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

    parent_slug = (
        args.parent_slug
        or os.environ.get(_PARENT_SLUG_ENV_VAR)
        or _ROOT_PARENT_SLUG
    )

    cmd = [
        "archon", "subagent", args.name,
        "--project-path", os.getcwd(),
        "--slug", args.slug,
        "--directive-file", args.directive_file,
        "--iter-num", iter_num,
        "--parent-slug", parent_slug,
    ]
    for glob in args.write_domain:
        cmd.extend(["--write-domain", glob])

    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())

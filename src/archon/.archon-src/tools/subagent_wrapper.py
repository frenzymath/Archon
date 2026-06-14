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
plan phase). When the wrapper is invoked outside the loop (e.g. by a
review agent whose env doesn't inherit the loop's exports) it falls
back to deriving the iter number from the highest-numbered
``.archon/logs/iter-NNN/`` directory and logs a warning. If neither
mechanism produces a value, the script exits non-zero with a clear
error rather than silently picking a default — a wrong iter_num
routes the JSONL log to the wrong directory and the dispatch is
effectively lost.
"""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path


_PARENT_SLUG_ENV_VAR = "ARCHON_SUBAGENT_SLUG"
_ROOT_PARENT_SLUG = "_root"


def _resolve_archon_cmd() -> list[str] | None:
    """Return the command prefix that invokes the archon CLI, or None.

    Resolution order, most-to-least authoritative:

    1. ``ARCHON_CLI_BIN`` — an absolute ``archon`` console-script path
       stamped into the env by a parent ``codex`` run (see
       ``CodexAgent.build_env`` → ``_stamp_archon_cli``). This is the path
       that actually matters inside the Codex sandbox: codex runs each tool
       command in a login shell (``bash -lc``) that re-derives ``PATH`` from
       the user profile, dropping the venv ``bin/`` — so ``shutil.which`` and
       a bare ``python3`` both miss archon. Non-``PATH`` env vars survive
       that shell, so this stamped absolute path is the reliable handle.
    2. ``archon`` console script on ``PATH`` — the normal, non-sandboxed case.
    3. ``ARCHON_PYTHON`` — an absolute interpreter path stamped alongside
       ``ARCHON_CLI_BIN`` that can import ``archon``; invoked as
       ``<python> -m archon``.
    4. ``<this python> -m archon`` when ``archon`` is importable by the
       interpreter running this wrapper.

    Returns None only when all four miss, so the caller emits one clear error.
    """
    cli_bin = os.environ.get("ARCHON_CLI_BIN")
    if cli_bin and os.path.isfile(cli_bin):
        return [cli_bin]
    exe = shutil.which("archon")
    if exe:
        return [exe]
    stamped_py = os.environ.get("ARCHON_PYTHON")
    if stamped_py and os.path.isfile(stamped_py):
        return [stamped_py, "-m", "archon"]
    if importlib.util.find_spec("archon") is not None:
        return [sys.executable, "-m", "archon"]
    return None


def _derive_iter_num_from_logs(project_path: Path) -> str | None:
    """Return the highest-numbered ``iter-NNN`` under ``.archon/logs/`` or None.

    Best-effort fallback for when ``ARCHON_ITER_NUM`` isn't in the
    environment (e.g. the review agent dispatched a subagent in a shell
    that didn't inherit the loop's exports). Returns the zero-padded
    canonical form ``"NNN"`` so the rest of the script can treat it as
    if the env var had been set.
    """
    logs = project_path / ".archon" / "logs"
    if not logs.is_dir():
        return None
    nums: list[int] = []
    for d in logs.iterdir():
        if not d.is_dir():
            continue
        if not d.name.startswith("iter-"):
            continue
        tail = d.name[5:]
        if tail.isdigit():
            nums.append(int(tail))
    if not nums:
        return None
    return f"{max(nums):03d}"


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

    archon_cmd = _resolve_archon_cmd()
    if archon_cmd is None:
        print(
            "archon CLI not found: neither an `archon` executable on PATH "
            f"nor an importable `archon` module for {sys.executable}. Install "
            "Archon or activate its venv before running the loop.",
            file=sys.stderr,
        )
        return 1

    iter_num = os.environ.get("ARCHON_ITER_NUM")
    if not iter_num:
        derived = _derive_iter_num_from_logs(Path(os.getcwd()))
        if derived is not None:
            print(
                f"ARCHON_ITER_NUM not set in environment; "
                f"derived iter={derived} from .archon/logs/iter-{derived}/. "
                f"This is the wrapper's fallback path — normally the Archon "
                f"loop sets ARCHON_ITER_NUM before launching Claude.",
                file=sys.stderr,
            )
            iter_num = derived
        else:
            print(
                "ARCHON_ITER_NUM not set in environment AND no "
                "`.archon/logs/iter-NNN/` directories were found under the "
                "current working directory. This script is meant to be "
                "invoked by the Archon loop, which sets ARCHON_ITER_NUM "
                "before launching Claude. Run it inside an initialised "
                "Archon project (one whose `.archon/logs/` already has an "
                "iter dir), or set ARCHON_ITER_NUM explicitly.",
                file=sys.stderr,
            )
            return 2

    parent_slug = (
        args.parent_slug
        or os.environ.get(_PARENT_SLUG_ENV_VAR)
        or _ROOT_PARENT_SLUG
    )

    cmd = [
        *archon_cmd, "subagent", args.name,
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

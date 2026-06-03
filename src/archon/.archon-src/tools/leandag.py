#!/usr/bin/env python3
"""Report blueprint-coverage gaps via leandag — invoked by Claude in the loop.

Installed at ``.claude/tools/archon-leandag.py``. The dag agent and its
blueprint-writers call this mid-session to see what the blueprint is
still missing after their edits:

    python3 .claude/tools/archon-leandag.py            # markdown summary
    python3 .claude/tools/archon-leandag.py --json     # machine-readable

It reports: Lean declarations with no blueprint entry, broken ``\\uses{}``
references, and which declarations are unproved / ready to prove.

Thin wrapper over ``archon dag-gaps`` (same pattern as
``archon-subagent.py``): the heavy lifting lives in the Archon package,
which has ``leandag`` installed, so this works regardless of which
``python3`` is on PATH inside the agent's shell.
"""

import os
import shutil
import subprocess
import sys


def main() -> int:
    if not shutil.which("archon"):
        print(
            "archon CLI not found on PATH. Install Archon or activate "
            "its venv before running the loop.",
            file=sys.stderr,
        )
        return 1

    cmd = ["archon", "dag-gaps", "--project-path", os.getcwd()]
    cmd.extend(sys.argv[1:])  # forward --json and any future flags
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())

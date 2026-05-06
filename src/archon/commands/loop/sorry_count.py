"""Sorry-count probe for the loop's progress reporting."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .utils import data_path


def count_sorries(project_path: Path) -> int | None:
    """Count remaining `sorry` placeholders in the project's `.lean` files.

    Tries the bundled analyzer first (it skips comments / sorryAx /
    identifiers ending in `sorry`); falls back to a coarse `grep` if the
    analyzer isn't available or its output format is unexpected.
    Returns `None` only if both probes fail.
    """
    analyzer = data_path("skills/lean4/lib/scripts/sorry_analyzer.py")
    if analyzer.exists():
        try:
            r = subprocess.run(
                [sys.executable, str(analyzer), str(project_path),
                 "--format=summary", "--exit-zero-on-findings"],
                capture_output=True, text=True, timeout=60,
            )
            if r.stdout.strip():
                first = r.stdout.strip().splitlines()[0]
                m = re.search(r"Sorry Summary:\s*(\d+)\s+total", first)
                if m:
                    return int(m.group(1))
        except Exception:
            pass

    try:
        r = subprocess.run(
            ["bash", "-c",
             "find " + str(project_path) + " -name '*.lean' -not -path '*/.lake/*' "
             "-not -path '*/lake-packages/*' "
             "| xargs grep -c 'sorry' 2>/dev/null "
             "| grep -v ':0$' | awk -F: '{s+=$2} END {print s}'"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip())
    except Exception:
        pass

    return None

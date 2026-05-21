"""Prover environment helpers: ARCHON_* env vars and baseline snapshots.

`ProverEnvironment` is a context manager that mutates `os.environ` for
serial/single-process runs. `prover_env_dict` returns the same vars as a
plain dict — used by the multi-lane runner, which feeds env overrides
directly into the per-lane Claude subprocess instead of touching the
global environment (multiple lanes run concurrently in threads).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def prover_env_dict(
    snap_dir: Path | str,
    prover_jsonl: Path | str,
    project_path: Path | str,
    serial_mode: bool = False,
) -> dict[str, str]:
    env_vars = {
        "ARCHON_SNAPSHOT_DIR": str(snap_dir),
        "ARCHON_PROVER_JSONL": str(prover_jsonl),
        "ARCHON_PROJECT_PATH": str(project_path),
    }
    if serial_mode:
        env_vars["ARCHON_SERIAL_MODE"] = "true"
    return env_vars


def snapshot_baseline(file_path: Path, snap_dir: Path) -> None:
    """Copy `file_path` to `<snap_dir>/baseline.lean` (best-effort)."""
    snap_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(file_path, snap_dir / "baseline.lean")
    except OSError:
        pass


class ProverEnvironment:
    """Context manager that sets/restores `ARCHON_*` env vars.

    Used by serial and parallel-fork prover runs. Multi-lane uses
    `prover_env_dict` directly because each lane has its own subprocess
    env and global mutation would race.
    """

    def __init__(
        self,
        snap_dir: Path | str,
        prover_jsonl: Path | str,
        project_path: Path | str,
        *,
        serial_mode: bool = False,
    ) -> None:
        self._env_vars = prover_env_dict(
            snap_dir, prover_jsonl, project_path, serial_mode=serial_mode,
        )
        self._old: dict[str, str | None] = {}

    def __enter__(self) -> "ProverEnvironment":
        for k, v in self._env_vars.items():
            self._old[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for k, prior in self._old.items():
            if prior is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prior

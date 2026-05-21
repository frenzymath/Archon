"""Filesystem lock for the multi-lane round.

Prevents two concurrent `archon loop` invocations from racing on the
same project's lane worktrees. Stale locks (pid no longer alive) are
reaped automatically.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from archon import log
from archon.state import utcnow_iso


class MultiLaneLock:
    """Context-manager lock for a project's multi-lane round."""

    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / 'multilane' / 'execute.lock.json'
        self._acquired = False

    def __enter__(self) -> "MultiLaneLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding='utf-8'))
            except Exception:
                payload = {}
            pid = payload.get('pid')
            if isinstance(pid, int) and pid > 0:
                try:
                    os.kill(pid, 0)
                except OSError:
                    pass
                else:
                    log.error(
                        f'Multi-lane execute already running for this project (pid {pid}).',
                    )
                    raise typer.Exit(1)
            self.path.unlink(missing_ok=True)
        self.path.write_text(
            json.dumps({'pid': os.getpid(), 'startedAt': utcnow_iso()}) + '\n',
            encoding='utf-8',
        )
        self._acquired = True

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass
        self._acquired = False

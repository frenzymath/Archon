"""Per-iteration filesystem semaphore for hierarchical subagent dispatch.

When subagents spawn child subagents, the total number of concurrent
Claude processes can blow past ``max_parallel`` configured in
``LoopOptions``. ``SlotPool`` is a slot-based semaphore implemented
via atomic ``rename`` on N empty marker files, shared across every
``archon subagent`` invocation in one iteration regardless of depth.

Why filesystem? Each ``archon subagent`` invocation is its own Python
process (Bash -> wrapper -> CLI). They can't share an in-process
``threading.Semaphore``, but they can share a directory.

Lifecycle:

1. Loop initializes the slot dir at iteration start with
   :meth:`SlotPool.init`. This also reaps any stale leases left by a
   crashed prior run.
2. Each ``Subagent.run()`` call wraps its inner ``agent.run()`` in
   ``SlotPool.from_env().slot()`` so concurrent holders are bounded.
3. The plan/coordinator agents themselves do NOT hold a slot while
   waiting on children -- they spawn via Bash (which blocks) but the
   slot is held by the wrapper process running ``agent.run()``. This
   prevents deadlock when a single thread of execution holds a slot
   while spawning N children.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from archon import log


SLOTS_ENV_VAR = "ARCHON_DISPATCH_SLOTS_DIR"
MAX_PARALLEL_ENV_VAR = "ARCHON_DISPATCH_MAX_PARALLEL"

# A slot held longer than this is assumed to belong to a crashed
# process. The in-loop idle-timeout kills agents after 15 minutes of
# no JSONL activity, so a healthy slot turnover is much faster; 1
# hour is safe.
DEFAULT_STALE_AFTER_S = 3600.0


class SlotPool:
    """Slot-based filesystem semaphore.

    Slot files live as ``slots_dir/slot-NNN`` when free and
    ``slots_dir/.held/slot-NNN`` when held. Acquire by atomic-renaming
    free -> held; release by atomic-renaming held -> free.
    """

    def __init__(
        self,
        slots_dir: Path,
        *,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
    ) -> None:
        self.slots_dir = Path(slots_dir)
        self.held_dir = self.slots_dir / ".held"
        self.stale_after_s = stale_after_s

    # ── lifecycle ──────────────────────────────────────────────────

    @classmethod
    def init(
        cls,
        slots_dir: Path,
        max_parallel: int,
        *,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
    ) -> "SlotPool":
        """Create the slot dir + ``max_parallel`` slot files.

        Idempotent: if some slots already exist (held + free total
        matches ``max_parallel``), no extras are created. Stale leases
        are reaped before counting so a crashed prior run does not
        starve the new run.
        """
        if max_parallel < 1:
            max_parallel = 1
        slots_dir = Path(slots_dir)
        slots_dir.mkdir(parents=True, exist_ok=True)
        held = slots_dir / ".held"
        held.mkdir(exist_ok=True)
        cls._reap_stale(held, slots_dir, stale_after_s)

        existing_free = len(list(slots_dir.glob("slot-*")))
        existing_held = len(list(held.glob("slot-*")))
        total = existing_free + existing_held
        if total < max_parallel:
            for i in range(total, max_parallel):
                (slots_dir / f"slot-{i:03d}").touch()
        return cls(slots_dir, stale_after_s=stale_after_s)

    @classmethod
    def from_env(cls) -> "SlotPool | None":
        """Build a SlotPool from ``ARCHON_DISPATCH_SLOTS_DIR``, else None.

        Subagents call this. When the env var is unset (the CLI flow
        ``archon refactor run`` for example), they degrade to
        unbounded concurrency -- preserving pre-dispatch-semaphore
        behaviour.
        """
        raw = os.environ.get(SLOTS_ENV_VAR)
        if not raw:
            return None
        return cls(Path(raw))

    # ── acquire / release ──────────────────────────────────────────

    def acquire(self, timeout_s: float | None = None) -> Path:
        """Block until a slot is available; return its held path."""
        deadline = (time.monotonic() + timeout_s) if timeout_s else None
        while True:
            # Reap stale leases on every attempt so a crashed sibling
            # doesn't starve us forever.
            self._reap_stale(self.held_dir, self.slots_dir, self.stale_after_s)

            for slot in sorted(self.slots_dir.glob("slot-*")):
                if not slot.is_file():
                    continue
                target = self.held_dir / slot.name
                try:
                    slot.rename(target)
                except OSError:
                    # Another process won this slot. Try the next one.
                    continue
                # Refresh mtime so the reaper doesn't recycle our slot
                # before we get a chance to touch it again.
                try:
                    target.touch()
                except OSError:
                    pass
                return target

            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError(
                    f"Could not acquire dispatch slot within {timeout_s}s "
                    f"(slots_dir={self.slots_dir})"
                )
            time.sleep(0.5)

    def release(self, held_slot: Path) -> None:
        target = self.slots_dir / held_slot.name
        try:
            held_slot.rename(target)
        except OSError:
            # If the reaper already moved it back, that's fine.
            pass

    @contextmanager
    def slot(self, timeout_s: float | None = None) -> Iterator[Path]:
        held = self.acquire(timeout_s)
        try:
            yield held
        finally:
            self.release(held)

    # ── inspection (for tests + dashboards) ────────────────────────

    def free_count(self) -> int:
        return len(list(self.slots_dir.glob("slot-*")))

    def held_count(self) -> int:
        if not self.held_dir.exists():
            return 0
        return len(list(self.held_dir.glob("slot-*")))

    # ── internals ──────────────────────────────────────────────────

    @staticmethod
    def _reap_stale(
        held_dir: Path, slots_dir: Path, stale_after_s: float,
    ) -> int:
        """Move slots whose mtime is older than threshold back to free.

        Returns the number of slots reaped (for logging / tests).
        """
        if not held_dir.exists():
            return 0
        now = time.time()
        reaped = 0
        for f in held_dir.glob("slot-*"):
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if now - mtime > stale_after_s:
                target = slots_dir / f.name
                try:
                    f.rename(target)
                    reaped += 1
                except OSError:
                    pass
        if reaped:
            log.warn(
                f"SlotPool: reaped {reaped} stale slot(s) from "
                f"{held_dir} (older than {stale_after_s}s)"
            )
        return reaped


__all__ = [
    "SlotPool",
    "SLOTS_ENV_VAR",
    "MAX_PARALLEL_ENV_VAR",
    "DEFAULT_STALE_AFTER_S",
]

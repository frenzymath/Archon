"""Compactor for task_done.md.

Each closed-target entry can grow to multi-paragraph prover-narrative
(file/line/proof-strategy/axiom-check, etc.). The compactor keeps the
last 5 iterations of closures verbatim and shrinks older entries to a
one-line summary, while preserving any cross-reference to a Mathlib
gap or technique that's still relevant.
"""

from __future__ import annotations

from .base import Compactor


class TaskDoneCompactor(Compactor):
    name = "compact-task-done"
    config_key = "task_done_md"
    target_relpath = "task_done.md"
    prompt_filename = "compactor-task-done.md"

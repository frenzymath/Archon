"""Compactor for task_pending.md.

Each pending target accumulates an attempts log over iterations. The
compactor keeps the latest attempt verbatim and the documented dead
ends (these are the file's primary value), and shrinks intermediate
attempt narratives.
"""

from __future__ import annotations

from .base import Compactor


class TaskPendingCompactor(Compactor):
    name = "compact-task-pending"
    config_key = "task_pending_md"
    target_relpath = "task_pending.md"
    prompt_filename = "compactor-task-pending.md"

"""Compactor for PROJECT_STATUS.md.

Written by the review agent after each session. The "Knowledge Base"
section is the single most important part of the file (it captures
errors not to reproduce and reusable patterns). The compactor leaves
Knowledge Base intact and only shrinks "Overall Progress"'s old
session narratives.
"""

from __future__ import annotations

from .base import Compactor


class ProjectStatusCompactor(Compactor):
    name = "compact-project-status"
    config_key = "project_status_md"
    target_relpath = "PROJECT_STATUS.md"
    prompt_filename = "compactor-project-status.md"

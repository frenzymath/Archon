"""Small path/format helpers shared across the loop package."""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path


def data_path(sub_path: str = "") -> Path:
    """Resolve a path inside the bundled `archon/.archon-src/` data tree."""
    root = resources.files("archon").joinpath(".archon-src")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


def relpath(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def file_slug(rel: str) -> str:
    """Filesystem-safe slug for a relative `.lean` file path."""
    return rel.replace("/", "_").replace(os.sep, "_").removesuffix(".lean")


def describe_finalize(do_git: bool, do_lake: bool, do_bp: bool) -> str:
    parts = []
    if do_git:
        parts.append("git")
    if do_lake:
        parts.append("lake build")
    if do_bp:
        parts.append("blueprint web")
    return ", ".join(parts) if parts else "disabled"

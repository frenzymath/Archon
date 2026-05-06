"""Iteration / session directory helpers + meta.json writer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_iter_num(log_dir: Path) -> int:
    """Return the next iteration number (1-based)."""
    max_n = 0
    if log_dir.exists():
        for d in log_dir.iterdir():
            if d.is_dir() and d.name.startswith("iter-"):
                try:
                    n = int(d.name.split("iter-")[1])
                    max_n = max(max_n, n)
                except ValueError:
                    pass
    return max_n + 1


def next_session_num(state_dir: Path) -> int:
    journal_dir = state_dir / "proof-journal" / "sessions"
    max_n = 0
    if journal_dir.exists():
        for d in journal_dir.iterdir():
            if d.is_dir() and d.name.startswith("session_"):
                try:
                    n = int(d.name.split("session_")[1])
                    max_n = max(max_n, n)
                except ValueError:
                    pass
    return max_n + 1


def write_meta(meta_file: Path, **kwargs: object) -> None:
    """Write/update key-value pairs in an iteration meta.json.

    Supports dotted keys like ``provers.file_slug.status=running``.
    """
    data: dict = {}
    if meta_file.exists():
        try:
            data = json.loads(meta_file.read_text())
        except Exception:
            pass

    for key, value in kwargs.items():
        keys = key.split(".")
        d = data
        for part in keys[:-1]:
            if part not in d or not isinstance(d[part], dict):
                d[part] = {}
            d = d[part]
        d[keys[-1]] = value

    meta_file.write_text(json.dumps(data, indent=2))


def archive_task_results(state_dir: Path, dest_dir: Path) -> None:
    """Move existing task_results/*.md to an archive directory.

    If `dest_dir` is an iteration directory (name matches `iter-*`),
    archives land in `{dest_dir}/task_results-archive/` (single subdir
    per iteration). Otherwise — for backwards compatibility with code
    that passes `logs/` — archives land in
    `{dest_dir}/task_results-TIMESTAMP/`.
    """
    results_dir = state_dir / "task_results"
    if not results_dir.exists():
        return
    md_files = list(results_dir.glob("*.md"))
    if not md_files:
        return

    if dest_dir.name.startswith("iter-"):
        archive = dest_dir / "task_results-archive"
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archive = dest_dir / f"task_results-{stamp}"

    archive.mkdir(parents=True, exist_ok=True)

    # When archiving into an iteration directory, a later archive call
    # within the same iteration would clobber earlier files. Guard by
    # renaming if a file with the same name already exists.
    for f in md_files:
        target = archive / f.name
        if target.exists():
            stamp = datetime.now().strftime("%H%M%S")
            target = archive / f"{f.stem}.{stamp}{f.suffix}"
        f.rename(target)

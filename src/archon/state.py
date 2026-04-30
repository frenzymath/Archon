"""Project state helpers for Archon loop.

Reads/writes .archon/ state files: PROGRESS.md, iteration metadata, task results.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


# ── stage / progress ──────────────────────────────────────────────────


def read_stage(progress_file: Path, force_stage: str | None = None) -> str:
    """Read the current stage from PROGRESS.md (or return forced override)."""
    if force_stage:
        return force_stage
    if not progress_file.exists():
        raise FileNotFoundError(f"PROGRESS.md not found at {progress_file}")
    lines = progress_file.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## Current Stage"):
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    raise ValueError("Could not read current stage from PROGRESS.md")


def is_complete(progress_file: Path, force_stage: str | None = None) -> bool:
    try:
        return read_stage(progress_file, force_stage) == "COMPLETE"
    except (FileNotFoundError, ValueError):
        return False


# ── objective file parsing ────────────────────────────────────────────


def parse_objective_files(progress_file: Path, project_path: Path) -> list[Path]:
    """Extract .lean file paths from ## Current Objectives in PROGRESS.md."""
    if not progress_file.exists():
        return []

    text = progress_file.read_text()

    # Extract the Current Objectives section
    in_section = False
    section_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## Current Objectives"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            section_lines.append(line)

    section_text = "\n".join(section_lines)

    # Find .lean filenames in **bold** or `backticks`
    pattern = re.compile(r'(?:\*\*|`)([^*`]+\.lean)(?:\*\*|`)')
    candidates = pattern.findall(section_text)

    # Resolve to actual paths
    results: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        # Search for the file in the project, excluding .lake/lake-packages
        for match in project_path.rglob(f"*{candidate}"):
            if ".lake" in match.parts or "lake-packages" in match.parts:
                continue
            resolved = str(match.resolve())
            if resolved not in seen:
                seen.add(resolved)
                results.append(match)
                break

    return sorted(results)


# ── iteration directory ───────────────────────────────────────────────


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


def write_meta(meta_file: Path, **kwargs: object) -> None:
    """Write/update key-value pairs in an iteration meta.json.

    Supports dotted keys like provers.file_slug.status=running.
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


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── cost summary ──────────────────────────────────────────────────────


class CostData:
    """Structured cost summary data."""
    def __init__(
        self,
        duration_ms: float,
        cost_usd: float,
        input_tokens: int,
        output_tokens: int,
        turns: int,
        models: dict[str, dict],
    ) -> None:
        self.duration_ms = duration_ms
        self.cost_usd = cost_usd
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.turns = turns
        self.models = models  # {model_name: {in, out, cost}}

    def totals_dict(self) -> dict[str, str]:
        """Return top-level metrics as a display dict."""
        d: dict[str, str] = {}
        if self.duration_ms:
            d["Duration"] = f"{self.duration_ms / 60000:.1f}min"
        if self.cost_usd:
            d["Cost"] = f"${self.cost_usd:.4f}"
        if self.input_tokens or self.output_tokens:
            d["Tokens"] = f"in={self.input_tokens:,}  out={self.output_tokens:,}"
        if self.turns:
            d["Turns"] = str(self.turns)
        return d

    def model_rows(self) -> list[tuple[str, str, str, str]]:
        """Return per-model rows as (model, in, out, cost)."""
        return [
            (m, f"{u['in']:,}", f"{u['out']:,}", f"${u['cost']:.4f}")
            for m, u in self.models.items()
        ]


def cost_summary(directory: Path) -> CostData | None:
    """Aggregate session_end events from all .jsonl files under directory."""
    if not directory.exists():
        return None

    rows: list[dict] = []
    for jsonl in directory.rglob("*.jsonl"):
        if jsonl.name == "provers-combined.jsonl":
            continue
        for line in jsonl.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("event") == "session_end":
                    rows.append(r)
            except json.JSONDecodeError:
                pass

    if not rows:
        return None

    cost = sum(r.get("total_cost_usd", 0) or 0 for r in rows)
    dur = sum(r.get("duration_ms", 0) or 0 for r in rows)
    tin = sum(r.get("input_tokens", 0) or 0 for r in rows)
    tout = sum(r.get("output_tokens", 0) or 0 for r in rows)
    turns = sum(r.get("num_turns", 0) or 0 for r in rows)

    models: dict[str, dict] = {}
    for r in rows:
        for m, u in (r.get("model_usage") or {}).items():
            if m not in models:
                models[m] = {"in": 0, "out": 0, "cost": 0.0}
            models[m]["in"] += u.get("inputTokens", 0) or 0
            models[m]["out"] += u.get("outputTokens", 0) or 0
            models[m]["cost"] += u.get("costUSD", 0) or 0

    return CostData(dur, cost, tin, tout, turns, models)


# ── task results ──────────────────────────────────────────────────────


def archive_task_results(state_dir: Path, dest_dir: Path) -> None:
    """Move existing task_results/*.md to an archive directory.

    If `dest_dir` is an iteration directory (name matches `iter-*`), archives
    land in `{dest_dir}/task_results-archive/` (single subdir per iteration).
    Otherwise — for backwards compatibility with code that passes `logs/` —
    archives land in `{dest_dir}/task_results-TIMESTAMP/`.
    """
    results_dir = state_dir / "task_results"
    if not results_dir.exists():
        return
    md_files = list(results_dir.glob("*.md"))
    if not md_files:
        return

    if dest_dir.name.startswith("iter-"):
        # Co-locate archive with iteration data.
        archive = dest_dir / "task_results-archive"
    else:
        # Legacy path: flat at logs/ with timestamp suffix.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archive = dest_dir / f"task_results-{stamp}"

    archive.mkdir(parents=True, exist_ok=True)

    # When archiving into an iteration directory, a later archive call within
    # the same iteration would clobber earlier files. Guard by renaming if a
    # file with the same name already exists.
    for f in md_files:
        target = archive / f.name
        if target.exists():
            # Rare case: two archiving events in the same iteration.
            stamp = datetime.now().strftime("%H%M%S")
            target = archive / f"{f.stem}.{stamp}{f.suffix}"
        f.rename(target)


# ── proof journal sessions ───────────────────────────────────────────


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
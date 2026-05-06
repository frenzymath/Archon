"""Aggregate Claude session-end cost rows into a structured summary."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CostData:
    """Aggregated usage across one or more Claude sessions."""

    duration_ms: float
    cost_usd: float
    input_tokens: int
    output_tokens: int
    turns: int
    models: dict[str, dict] = field(default_factory=dict)
    """Per-model usage: ``{model_name: {'in': int, 'out': int, 'cost': float}}``."""

    def totals_dict(self) -> dict[str, str]:
        """Top-level metrics formatted for the cost table display."""
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
        """Per-model rows as (model, in, out, cost)."""
        return [
            (m, f"{u['in']:,}", f"{u['out']:,}", f"${u['cost']:.4f}")
            for m, u in self.models.items()
        ]


def cost_summary(directory: Path) -> CostData | None:
    """Aggregate session_end events from all .jsonl files under `directory`."""
    if not directory.exists():
        return None

    rows = list(_iter_session_end_rows(directory))
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
            slot = models.setdefault(m, {"in": 0, "out": 0, "cost": 0.0})
            slot["in"] += u.get("inputTokens", 0) or 0
            slot["out"] += u.get("outputTokens", 0) or 0
            slot["cost"] += u.get("costUSD", 0) or 0

    return CostData(dur, cost, tin, tout, turns, models)


def safe_jsonl_lines(path: Path) -> list[str]:
    """Read JSONL lines tolerantly.

    Multilane stores per-lane prover logs at
    ``.archon/multilane/lanes/<lane>/iter-NNN/provers/<file>.jsonl`` and
    symlinks them into ``logs/iter-NNN/provers/`` as
    ``<file>__<lane>.jsonl`` for the dashboard. These symlinks can dangle
    when:
      - a lane was cancelled mid-flight before its parser wrote the
        target file (``.raw.jsonl`` is the most common case);
      - a prior run on the same iter dir had a different lane set
        enabled (e.g. iter-004 first ran with kimi, now resumes with
        only anthropic + deepseek), leaving stale `__kimi.*.jsonl`
        symlinks pointing at non-existent targets;
      - the lane worktree was deleted between runs.

    Anything that walks the iter tree must tolerate this — crashing the
    whole loop because one symlink is stale is unacceptable. Returns
    an empty list for missing / unreadable paths.
    """
    try:
        if not path.is_file():
            # is_file() returns False for dangling symlinks, missing
            # files, and non-regular entries. Either way we can't read
            # it.
            return []
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []


def _iter_session_end_rows(directory: Path):
    """Yield every session_end JSON object under `directory`'s .jsonl files."""
    for jsonl in directory.rglob("*.jsonl"):
        # Skip the merged combined log so we don't double-count rows
        # already counted from the per-prover logs.
        if jsonl.name == "provers-combined.jsonl":
            continue
        for line in safe_jsonl_lines(jsonl):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("event") == "session_end":
                yield r

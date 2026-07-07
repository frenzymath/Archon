"""Aggregate Claude session-end cost rows into a structured summary."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OperationCost:
    """Detailed consumption metadata for a single operation."""

    name: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    """Estimate USD cost based on token counts and model pricing.

    CANONICAL model pricing table. Three mirrors exist that cannot import this
    (two self-contained ``python -c`` stream-parser templates in
    ``archon/agent.py`` and ``archon/agents/codex.py``, and a TypeScript copy in
    ``ui/server/src/routes/summary.ts``). Keep all four in sync when pricing
    changes — a drift here is what let deepseek-reasoner bill at chat rates.
    """
    m = model.lower()
    if "sonnet" in m:
        return (input_tokens * 3.0 + output_tokens * 15.0 + cache_read_tokens * 0.30) / 1_000_000.0
    elif "haiku" in m:
        return (input_tokens * 0.8 + output_tokens * 4.0 + cache_read_tokens * 0.08) / 1_000_000.0
    elif "opus" in m:
        return (input_tokens * 15.0 + output_tokens * 75.0) / 1_000_000.0
    elif "flash" in m:
        return (input_tokens * 0.075 + output_tokens * 0.30) / 1_000_000.0
    elif "pro" in m:
        return (input_tokens * 1.25 + output_tokens * 5.0) / 1_000_000.0
    elif "gpt-4o-mini" in m:
        return (input_tokens * 0.15 + output_tokens * 0.60) / 1_000_000.0
    elif "gpt-4o" in m:
        return (input_tokens * 2.50 + output_tokens * 10.00) / 1_000_000.0
    elif "o1-mini" in m or "o3-mini" in m:
        return (input_tokens * 1.10 + output_tokens * 4.40) / 1_000_000.0
    elif "o1-preview" in m or "o1" in m:
        return (input_tokens * 15.00 + output_tokens * 60.00) / 1_000_000.0
    elif "deepseek-reasoner" in m or "deepseek-r1" in m:
        return (input_tokens * 0.55 + output_tokens * 2.19) / 1_000_000.0
    elif "deepseek-chat" in m or "deepseek-v3" in m or "deepseek" in m:
        return (input_tokens * 0.14 + output_tokens * 0.28) / 1_000_000.0
    return (input_tokens * 2.50 + output_tokens * 10.00) / 1_000_000.0


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
    operations: list[OperationCost] = field(default_factory=list)
    """Per-operation breakdown."""

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

    # Calculate costs, incorporating estimated pricing for zero-cost outputs
    cost = 0.0
    dur = 0.0
    tin = 0
    tout = 0
    turns = 0
    operations = []

    for jsonl, r in rows:
        r_tin = r.get("input_tokens", 0) or 0
        r_tout = r.get("output_tokens", 0) or 0
        r_dur = r.get("duration_ms", 0) or 0
        r_turns = r.get("num_turns", 0) or 0
        r_cache = r.get("cache_read_input_tokens", 0) or 0

        # Find primary model name
        m_usage = r.get("model_usage") or {}
        model_name = "unknown"
        if m_usage:
            sorted_m = sorted(
                m_usage.items(),
                key=lambda x: (x[1].get("costUSD", 0.0) or 0.0, x[1].get("outputTokens", 0) or 0),
                reverse=True
            )
            if sorted_m:
                model_name = sorted_m[0][0]
        else:
            model_name = r.get("model") or "unknown"

        # Determine cost (with estimation fallback)
        r_cost = r.get("total_cost_usd", 0.0) or r.get("cost_usd", 0.0) or 0.0
        if not r_cost and (r_tin or r_tout):
            r_cost = estimate_cost_usd(model_name, r_tin, r_tout, r_cache)

        cost += r_cost
        dur += r_dur
        tin += r_tin
        tout += r_tout
        turns += r_turns

        # Operation name relative to the base directory
        try:
            op_name = str(jsonl.relative_to(directory))
            if op_name.endswith(".jsonl"):
                op_name = op_name[:-6]
        except ValueError:
            op_name = jsonl.stem

        operations.append(OperationCost(
            name=op_name,
            model=model_name,
            input_tokens=r_tin,
            output_tokens=r_tout,
            cache_read_tokens=r_cache,
            cost_usd=r_cost,
        ))

    models: dict[str, dict] = {}
    for jsonl, r in rows:
        for m, u in (r.get("model_usage") or {}).items():
            slot = models.setdefault(m, {"in": 0, "out": 0, "cost": 0.0})
            m_in = u.get("inputTokens", 0) or 0
            m_out = u.get("outputTokens", 0) or 0
            slot["in"] += m_in
            slot["out"] += m_out
            m_cost = u.get("costUSD", 0.0) or 0.0
            if not m_cost and (m_in or m_out):
                m_cost = estimate_cost_usd(m, m_in, m_out)
            slot["cost"] += m_cost

    return CostData(
        duration_ms=dur,
        cost_usd=cost,
        input_tokens=tin,
        output_tokens=tout,
        turns=turns,
        models=models,
        operations=operations,
    )


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
    """Yield (jsonl_path, session_end_event) under `directory`'s .jsonl files."""
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
                yield jsonl, r

"""Compactor for STRATEGY.md.

The plan agent grows STRATEGY.md every iteration; left alone, it
becomes a 100k-char wall of text where each Phase heading carries an
inline log of every iteration that touched it. The compactor:

1. Forces a tabular header at the top with phase × iters-remaining ×
   LOC-remaining estimates (adds it if missing, refreshes the numbers
   if stale).
2. Strips inline iter-XXX completion notes from Phase headings —
   they belong in "Where we currently sit" / "Revision log".
3. Compresses old "Where we currently sit" entries (last 2 iters
   verbatim, older merged into one-liners).
4. Compresses "Revision log" entries (last 3 iters verbatim, older
   one-line, consecutive same-strategy iters merged).
"""

from __future__ import annotations

from .base import Compactor


class StrategyCompactor(Compactor):
    name = "compact-strategy"
    config_key = "strategy_md"
    target_relpath = "STRATEGY.md"
    prompt_filename = "compactor-strategy.md"

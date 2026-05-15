"""ReviewCommentHygieneSubagent — flag in-source operational metadata.

Scans Lean (and optionally blueprint) sources for accumulated
iteration-history comments ("Iter-046 (Mathlib gap-fill)", "iter-091
review reported '13'…"), stale TODOs ("TODO left for iter X"), and
docstring/body mismatches that have drifted across refactors. Pure
pattern-scan — cheapest reviewer to run, useful at every iteration.
"""

from __future__ import annotations

from .review_base import ReviewSubagent


class ReviewCommentHygieneSubagent(ReviewSubagent):
    name = "review-comment-hygiene"

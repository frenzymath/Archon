"""ReviewDesignChoicesSubagent — flag suboptimal architectural decisions.

Compares the implementation strategy against alternatives. Surfaces
duplications of Mathlib infrastructure (e.g. building a parallel
pipeline where transport-through-forgetful would suffice — the
StructureSheafModuleK case), proofs that re-derive existing API,
design decisions that take a longer path than necessary.

Heavier reasoning agent — uses Mathlib search (lean_loogle /
lean_local_search) and Web Search where authorized. Land it last in
the review subagent rotation; cheapest reviewers run first.
"""

from __future__ import annotations

from .review_base import ReviewSubagent


class ReviewDesignChoicesSubagent(ReviewSubagent):
    name = "review-design-choices"

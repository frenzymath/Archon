"""ReviewBlueprintConsistencySubagent — check Lean ↔ blueprint drift.

For each Lean declaration annotated by a blueprint chapter (via
``\\lean{...}``), verifies that the Lean signature matches the
blueprint's statement. Reports drift in both directions: Lean changed
but blueprint didn't, blueprint refined but Lean didn't.
"""

from __future__ import annotations

from .review_base import ReviewSubagent


class ReviewBlueprintConsistencySubagent(ReviewSubagent):
    name = "review-blueprint-consistency"

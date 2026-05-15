"""ReviewMathlibOverlapSubagent — surface project↔Mathlib structural mirrors.

For each new Lean file (or one named by the directive), searches
Mathlib for declarations whose signatures parallel the file's
contents. Reports cases where a file structurally mirrors existing
Mathlib code so the plan agent can consider a transport-through-
forgetful or re-export route rather than carrying a duplicate.
"""

from __future__ import annotations

from .review_base import ReviewSubagent


class ReviewMathlibOverlapSubagent(ReviewSubagent):
    name = "review-mathlib-overlap"

"""ReviewDefinitionCorrectnessSubagent — detect stand-in / wrong definitions.

Scans the project's Lean files for definitions whose docstring or
implementation hints they are "approximations", "stand-ins", or
"first-pass" placeholders that diverge from the blueprint's intended
meaning. Designed to catch the LineBundle-style failure where a wrong
definition lands under sorry-filling pressure and stays load-bearing
for many iterations.

See ``.archon/prompts/review-definition-correctness.md`` for the
full audit playbook.
"""

from __future__ import annotations

from .review_base import ReviewSubagent


class ReviewDefinitionCorrectnessSubagent(ReviewSubagent):
    name = "review-definition-correctness"

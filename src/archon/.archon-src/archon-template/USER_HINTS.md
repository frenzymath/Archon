<!--
USER_HINTS.md — two sections with different lifecycles.

## Temporary hints
  Consumed by the next plan phase and then cleared. Use for one-shot
  steering: "try route X this iter", "skip Lane F this round".

## Persistent hints
  NEVER auto-cleared. These are standing directives that survive every
  iteration reset. The plan agent treats them as HIGHER PRIORITY than
  any conflicting instruction in its own prompt or in
  .archon/prompts/plan.md. Use for project-wide constraints:
    - "never accept axiom X"
    - "don't touch theorem Y until I say so"
    - "always run mathlib-build mode on Lane I"

Format for both sections (one bullet per hint, timestamped):
  - [YYYY-MM-DDTHH:MM:SSZ] hint text

Hints are written by 'archon discuss' or directly by you. In discuss,
the agent will ask which section to target; in a direct edit, place your
bullet under the appropriate heading.

File-specific hints (one .lean file only) belong as /- USER: ... -/
comments inside that file — NOT here.
-->

## Temporary hints


## Persistent hints


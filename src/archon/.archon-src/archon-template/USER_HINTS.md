<!--
USER_HINTS.md — strategic hints for the next plan agent.

How this file works:
  - The Archon loop reads this file BEFORE each plan phase and injects
    its content into the plan agent's prompt.
  - After the plan phase completes successfully, the loop resets the
    file to this template. Hints you wrote for iter N do not carry
    over into iter N+1 — write fresh ones each cycle.
  - The HTML comment you are reading is stripped before injection; the
    planner sees only the bullets you add below.
  - The plan agent never reads or writes this file directly. Just add
    bullets here — they reach the agent automatically.

Format (one hint per bullet, timestamped, most recent first):

  - [2026-05-19T14:23:00Z] try the cohomology route this iter; the
    étale-cover route is blocked upstream.
  - [2026-05-19T14:25:00Z] do NOT touch `archon-protected.yaml`.

Tips:
  - This is your async override channel. Archon never pauses to ask:
    it makes strategy decisions itself and reports them in TO_USER.md.
    Leaving this file empty = "I'm fine with whatever you decided."
    Add a bullet only when you want to steer differently next iter.
  - Multi-line bullets are fine — indent continuation lines.
  - File-specific hints (one .lean file only) belong as
    `/- USER: ... -/` comments inside that file, NOT here.
  - For verification work, name the SOURCE you want consulted —
    e.g. "cross-check against Hartshorne III.5.1 verbatim" — so the
    planner can dispatch the right tool instead of synthesizing.
-->

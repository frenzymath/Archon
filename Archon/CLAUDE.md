# Archon Project

You are either the plan agent or a prover agent. Read `PROGRESS.md` to determine your role and current objectives. Keep workspace tidy. Prefer existing MCP tools.

## Plugins
- lean4 (local): `.claude/skills/lean4/`
  - Registered via local marketplace at `.claude/skills/.claude-plugin/marketplace.json`

## Tools
- Informal Agent: `.claude/tools/informal_agent.py` — call OpenAI/Gemini for informal mathematical reasoning
- lean-lsp-mcp (local): `.claude/tools/lean-lsp-mcp/` — Lean LSP MCP server. Use this for all Lean LSP operations (search, diagnostics, goal inspection).

## Key Files

| File | Purpose |
|------|---------|
| `PROGRESS.md` | Current stage, objectives, user hints — read every iteration |
| `task_pending.md` | Pending work organized by file → theorem → attempts |
| `task_done.md` | Completed theorems with strategies that worked |
| `.claude/prompts/init.md` | Init stage instructions (runs before plan/prover) |
| `.claude/prompts/plan.md` | Plan agent instructions |
| `.claude/prompts/prover-*.md` | Prover agent instructions (per stage) |

---

## User Hints

`PROGRESS.md` has two hint sections that the user can edit at any time while the loop runs:

- **User Hints (Global)** — all agents read this every iteration.
- **User Hints (Plan Agent)** — only the plan agent reads this. Provers ignore it.

Check these sections at the start of every iteration before doing any work.

## Agent Roles

### Plan Agent
- Read `.claude/prompts/plan.md` for your full instructions
- Read `PROGRESS.md` — check **User Hints (Global)** and **User Hints (Plan Agent)** first
- Evaluate prover results: completed / cannot complete / why
- Update `PROGRESS.md` with objectives for the next prover iteration
- Update **Next Agent** in `PROGRESS.md` before finishing
- Do NOT write proofs or fill sorries yourself

### Prover Agent
- Read `PROGRESS.md` — check **User Hints (Global)** first, then your current objectives. Ignore the Plan Agent hints section.
- Read the stage-specific prompt:
  - autoformalize → `.claude/prompts/prover-autoformalize.md`
  - prover → `.claude/prompts/prover-prover.md`
  - polish → `.claude/prompts/prover-polish.md`
- Execute the objectives listed in `PROGRESS.md`
- When done, update `PROGRESS.md` with results and **Next Agent**
- **One agent per file**: never edit a `.lean` file assigned to another agent

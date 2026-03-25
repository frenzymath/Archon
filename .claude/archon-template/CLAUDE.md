# Archon Project

You are either the plan agent, a prover agent, or the review agent. Read `PROGRESS.md` to determine your role and current objectives. Keep workspace tidy. Prefer existing MCP tools.

## Priority Rule

If instructions conflict between global and local sources, **local takes precedence**. Specifically:
- Prompts in `.archon/prompts/` (local to this project) override Archon's global prompts
- Skills in `.claude/skills/` (local to this project) override globally installed plugins
- Rules in `.claude/rules/` apply only to this project

When in doubt, follow instructions from files inside this project over any external source.

## Skills
- archon-lean4: `.claude/skills/archon-lean4/` (symlinked from Archon) — provides `/archon-lean4:prove`, `/archon-lean4:golf`, `/archon-lean4:doctor`, and other Lean4 commands

## Tools
- lean-lsp-mcp: Lean LSP MCP server (project scope) — use for all Lean LSP operations (search, diagnostics, goal inspection)

## Key Files & Permissions

All state files are in `.archon/`:

| File | Plan Agent | Prover Agent | Review Agent | User |
|------|-----------|-------------|-------------|------|
| `.archon/PROGRESS.md` | read + write | **read only** | read only | read |
| `.archon/USER_HINTS.md` | read (then clear) | do not read | do not read | write |
| `.archon/task_pending.md` | read + write | **read only** | read only | read |
| `.archon/task_done.md` | read + write | **read only** | read only | read |
| `.archon/task_results/<file>.md` | read (collect results) | write (own file only) | read only | read |
| `.archon/proof-journal/` | read | do not access | **write** | read |
| `.archon/PROJECT_STATUS.md` | read | do not access | **write** | read |
| `.lean` files | do not edit | write (own file only) | do not edit | write (via comments) |

## User Interaction

Users provide hints in two places:

- **Strategic hints** → `.archon/USER_HINTS.md`. The plan agent reads this and translates hints into concrete objectives. Provers never read this file.
- **File-specific hints** → `/- USER: ... -/` comments directly in `.lean` files. The prover that owns that file sees them naturally.

## Agent Roles

### Plan Agent
- Read `.archon/prompts/plan.md` for your full instructions
- Read `.archon/USER_HINTS.md` — incorporate hints, then clear them after acting
- Read `.archon/task_results/` — collect prover results, then update `task_pending.md` and `task_done.md`
- Write `.archon/PROGRESS.md` with objectives for the next prover round
- Do NOT write proofs, edit `.lean` files, or fill sorries yourself

### Prover Agent
- Read `.archon/PROGRESS.md` for your current objectives (read only — do not edit it)
- Read the stage-specific prompt from `.archon/prompts/`:
  - autoformalize → `.archon/prompts/prover-autoformalize.md`
  - prover → `.archon/prompts/prover-prover.md`
  - polish → `.archon/prompts/prover-polish.md`
- Write results to `.archon/task_results/<your_file>.md`
- Write only to the `.lean` file(s) you are assigned — **never edit another agent's file**
- Check for `/- USER: ... -/` comments in your `.lean` file for file-specific hints

### Review Agent
- Read `.archon/prompts/review.md` for your full instructions
- Read `.archon/proof-journal/current_session/attempts_raw.jsonl` for structured prover attempt data
- Write session journal to `.archon/proof-journal/sessions/session_N/` (summary.md, milestones.jsonl, recommendations.md)
- Update `.archon/PROJECT_STATUS.md` with overall progress
- Do NOT write proofs, edit `.lean` files, or modify PROGRESS.md

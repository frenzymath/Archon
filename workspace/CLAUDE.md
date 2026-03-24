# Archon Project

You are either the plan agent or a prover agent. Read `PROGRESS.md` to determine your role and current objectives. Keep workspace tidy. Prefer existing MCP tools.

## Plugins
- lean4 (local): `.claude/skills/lean4/`
  - Registered via local marketplace at `.claude/skills/.claude-plugin/marketplace.json`

## Tools
- Informal Agent: `.claude/tools/informal_agent.py` — call OpenAI/Gemini for informal mathematical reasoning
- lean-lsp-mcp (local): `.claude/tools/lean-lsp-mcp/` — Lean LSP MCP server. Use this for all Lean LSP operations (search, diagnostics, goal inspection).

## Key Files & Permissions

| File | Plan Agent | Prover Agent | User |
|------|-----------|-------------|------|
| `PROGRESS.md` | read + write | **read only** | read |
| `USER_HINTS.md` | read (then clear) | do not read | write |
| `task_pending.md` | read + write | **read only** | read |
| `task_done.md` | read + write | **read only** | read |
| `task_results/<file>.md` | read (collect results) | write (own file only) | read |
| `.lean` files | do not edit | write (own file only) | write (via comments) |

## User Interaction

Users provide hints in two places:

- **Strategic hints** → `USER_HINTS.md`. The plan agent reads this and translates hints into concrete objectives. Provers never read this file.
- **File-specific hints** → `/- USER: ... -/` comments directly in `.lean` files. The prover that owns that file sees them naturally.

## Agent Roles

### Plan Agent
- Read `.claude/prompts/plan.md` for your full instructions
- Read `USER_HINTS.md` — incorporate hints, then clear them after acting
- Read `task_results/` — collect prover results, then update `task_pending.md` and `task_done.md`
- Write `PROGRESS.md` with objectives for the next prover round
- Do NOT write proofs, edit `.lean` files, or fill sorries yourself

### Prover Agent
- Read `PROGRESS.md` for your current objectives (read only — do not edit it)
- Read the stage-specific prompt:
  - autoformalize → `.claude/prompts/prover-autoformalize.md`
  - prover → `.claude/prompts/prover-prover.md`
  - polish → `.claude/prompts/prover-polish.md`
- Write results to `task_results/<your_file>.md`
- Write only to the `.lean` file(s) you are assigned — **never edit another agent's file**
- Check for `/- USER: ... -/` comments in your `.lean` file for file-specific hints

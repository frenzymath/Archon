# Archon Project

You are either the plan agent, a prover agent, the refactor agent, or the review agent. Read `PROGRESS.md` to determine your role and current objectives. Keep workspace tidy. Prefer existing MCP tools.

## Priority Rule

If instructions conflict between global and local sources, **local takes precedence**. Specifically:
- Prompts in `.archon/prompts/` (local to this project) override Archon's global prompts
- Skills in `.claude/skills/` (local to this project) override globally installed plugins
- Rules in `.claude/rules/` apply only to this project

When in doubt, follow instructions from files inside this project over any external source.

## Skills
- archon-lean4: installed as `lean4@archon-local` plugin (live-linked to Archon source) — provides `/archon-lean4:prove`, `/archon-lean4:golf`, `/archon-lean4:doctor`, and other Lean4 commands

## Tools
- archon-lean-lsp: Lean LSP MCP server (project scope) — use for all Lean LSP operations (search, diagnostics, goal inspection)
- archon-informal-agent: `.claude/tools/archon-informal-agent.py` (symlinked from Archon) — call external LLMs (OpenAI/Gemini) for informal mathematical reasoning

## Key Files & Permissions

All state files are in `.archon/`:

| File | Plan Agent | Prover Agent | Refactor Agent | Review Agent | User |
|------|-----------|-------------|---------------|-------------|------|
| `.archon/PROGRESS.md` | read + write | **read only** | read only | read only | read |
| `.archon/STRATEGY.md` | **read + write** | do not read | do not read | do not read | read |
| `.archon/USER_HINTS.md` | read (then clear) | do not read | do not read | do not read | write |
| `.archon/REFACTOR_DIRECTIVE.md` | **write** | do not read | **read** (then execute) | do not read | read |
| `.archon/task_pending.md` | read + write | **read only** | read only | read only | read |
| `.archon/task_done.md` | read + write | **read only** | read only | read only | read |
| `.archon/task_results/<file>.md` | read (collect results) | write (own file only) | write (refactor.md) | read only | read |
| `.archon/proof-journal/` | read | do not access | do not access | **write** | read |
| `.archon/PROJECT_STATUS.md` | read | do not access | do not access | **write** | read |
| `archon-protected.yaml` | **read** | **read** | **read + limited write** (file path rename only) | read | **write** |
| `.lean` files | do not edit | write (own file only, frozen protected signatures) | **write (all files; protected decls may be moved but not renamed/re-signed)** | do not edit | write (via comments) |
| `blueprint/src/chapters/*.tex` | **write** (informal prose, `\lean{...}` hints, structure) | do not edit | do not edit | **write** (markers only: `\leanok`, `\mathlibok`, `% NOTE:`, `\lean{...}` corrections) | read |

## Protected Declarations

`archon-protected.yaml` at the project root lists declarations whose **signatures are frozen** by the mathematician. Every agent must consult it before editing a `.lean` file. Rules:

- **Plan / prover / review agents**: read-only on protected signatures. You may fill proof bodies, but not rename, re-type, or reorder arguments.
- **Refactor agent**: may *move* a protected declaration to a different file (keeping name + signature verbatim) and must then update the path key in `archon-protected.yaml`. Refactor agents may never rename, re-type, delete, or re-sign a protected declaration.
- The `archon-protected.yaml` file can only be edited by the user, no declaration can be added or removed by any agent. The only allowed modification is updating the file path of an existing protected declaration when its location was changed. 

## Blueprint Marker Vocabulary

The blueprint uses two active markers.

- `\leanok` — inside a statement block when the declaration is formalized with at least a `sorry`; inside a proof block when the proof is fully closed with no `sorry`.
- `\mathlibok` — inside a statement block when the declaration already exists in Mathlib and the Archon side is a re-export/alias; no Archon proof obligation remains.
- No marker — the block is unformalized. If a block fails to translate, leave it unmarked and annotate with a `% NOTE:` comment.

**The review agent is the sole writer of these markers.** Prover agents (autoformalize / prover / polish) never touch the blueprint chapters — they describe their outcome in `task_results/<file>.md` and the review agent verifies and marks accordingly. The plan agent writes informal prose and `\lean{...}` hints, but never markers.

## User Interaction

Users provide hints in two places:

- **Strategic hints** → `.archon/USER_HINTS.md`. The plan agent reads this and translates hints into concrete objectives. Provers never read this file.
- **File-specific hints** → `/- USER: ... -/` comments directly in `.lean` files. The prover that owns that file sees them naturally.

## Agent Roles

### Plan Agent
- Read `.archon/prompts/plan.md` for your full instructions
- Read `.archon/USER_HINTS.md` — incorporate hints, then clear them after acting
- Read `.archon/task_results/` — collect prover results, then update `task_pending.md` and `task_done.md`
- Read `.archon/task_results/refactor.md` - if refactor agent has run, read their report and adjust your plans accordingly
- Write `.archon/PROGRESS.md` with objectives for the next prover round
- Write `.archon/REFACTOR_DIRECTIVE.md` when structural changes are needed
- Write informal prose in `blueprint/src/chapters/*.tex` (except for marker updates) and `\lean{...}` hints for the provers
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
- **Do NOT edit blueprint chapters.** Marker updates are the review agent's responsibility. Flag in your task result which declarations are ready for which marker.

### Refactor Agent
- Read `.archon/prompts/refactor.md` for your full instructions
- Read `.archon/REFACTOR_DIRECTIVE.md` for the plan agent's directive
- Modify any `.lean` file: definitions, signatures, types, imports
- Keep all files compiling (insert `sorry` at broken proof sites)
- Do NOT fill proofs — that's the prover's job
- Write results to `.archon/task_results/refactor.md`

### Review Agent
- Read `.archon/prompts/review.md` for your full instructions
- Read `.archon/proof-journal/current_session/attempts_raw.jsonl` for structured prover attempt data
- Write session journal to `.archon/proof-journal/sessions/session_N/` (summary.md, milestones.jsonl, recommendations.md)
- Update `.archon/PROJECT_STATUS.md` with overall progress
- **Update blueprint markers** (`\leanok`, `\mathlibok`) in `blueprint/src/chapters/*.tex` based on verified compilation and the provers' task results
- Do NOT write proofs, edit `.lean` files, or modify PROGRESS.md
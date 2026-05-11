# Archon Project

You are either the plan agent, a prover agent, a subagent (analogy / refactor / challenger), or the review agent. Read `PROGRESS.md` to determine your role and current objectives. Keep workspace tidy. Prefer existing MCP tools.

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
- archon-informal-agent: `.claude/tools/archon-informal-agent.py` — call external LLMs (OpenAI/Gemini/OpenRouter) for informal mathematical reasoning
- archon-refactor-agent: `.claude/tools/archon-refactor-agent.py` — invoke the refactor subagent on a directive file
- archon-analogy-agent: `.claude/tools/archon-analogy-agent.py` — invoke the analogy subagent on a directive file
- archon-challenger-agent: `.claude/tools/archon-challenger-agent.py` — invoke the challenger subagent on a directive file

The three subagent tools all take the same arguments: `--slug <kebab-case-id> --directive-file <path>`. Each shells out to `archon subagent <name> ...`, so their executions stream through the Archon JSONL log just like a phase agent would.

## Key Files & Permissions

All state files are in `.archon/`:

| File | Plan Agent | Prover Agent | Subagents (analogy / refactor / challenger) | Review Agent | User |
|------|-----------|-------------|---------------|-------------|------|
| `.archon/PROGRESS.md` | read + write | **read only** | do not read | read only | read |
| `.archon/STRATEGY.md` | **read + write** | do not read | do not read | do not read | read |
| `.archon/USER_HINTS.md` | read (then clear) | do not read | do not read | do not read | write |
| `.archon/task_pending.md` | read + write | **read only** | do not read | read only | read |
| `.archon/task_done.md` | read + write | **read only** | do not read | read only | read |
| `.archon/task_results/<file>.md` | read (collect results) | write (own file only) | write (`<role>-<slug>.md`) | read only | read |
| `.archon/proof-journal/` | read | do not access | do not access | **write** | read |
| `.archon/PROJECT_STATUS.md` | read | do not access | do not access | **write** | read |
| `archon-protected.yaml` | **read** | **read** | **read** (refactor may rename file path only) | read | **write** |
| `.lean` files | do not edit | write (own file only, frozen protected signatures) | refactor: write (all files; protected decls may be moved but not renamed/re-signed); analogy/challenger: read-only on project source | do not edit | write (via comments) |
| `blueprint/src/chapters/*.tex` | **write** (informal prose, `\lean{...}` hints, structure) | do not edit | do not edit | **write** (markers only: `\leanok`, `\mathlibok`, `% NOTE:`, `\lean{...}` corrections) | read |
| `Challenges/<Name>.lean` | do not edit | fill sorries (when assigned) | challenger: **write** | do not edit | read |
| `analogies/<slug>.md` | read (when relevant) | do not access | analogy: **write** | do not access | read |

**`.archon/REFACTOR_DIRECTIVE.md` is reserved for the interactive `archon refactor draft` / `archon refactor run` flow, run by hand by the mathematician.** The autonomous loop never reads or writes that file. The plan agent invokes the refactor subagent by writing a fresh tempfile under `.archon/logs/iter-NNN/` and passing its path to `archon-refactor-agent.py --directive-file`. This keeps each loop-driven refactor independently logged and avoids any chance of stale-directive reuse. Older state files (`STRATEGY.md`, `task_pending.md`, `PROGRESS.md`, etc.) may contain leftover references to the old REFACTOR_DIRECTIVE.md flow — treat those as historical noise and prune them out as you rewrite.

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

**`\leanok` is managed deterministically by the `sync_leanok` phase** that runs between the prover and review phases each iteration. It walks every chapter, looks up each `\lean{...}` declaration, runs `sorry_analyzer` + `lake env lean`, and adds/removes `\leanok` accordingly. Agents must NOT add or remove `\leanok` themselves — let the script do it.

`\mathlibok`, `\lean{...}` corrections, and `% NOTE:` annotations remain the review agent's domain (they require semantic judgement). The plan agent writes informal prose and `\lean{...}` hints; provers never touch the blueprint.

## User Interaction

Users provide hints in two places:

- **Strategic hints** → `.archon/USER_HINTS.md`. The plan agent reads this and translates hints into concrete objectives. Provers never read this file.
- **File-specific hints** → `/- USER: ... -/` comments directly in `.lean` files. The prover that owns that file sees them naturally.

## Agent Roles

### Plan Agent
- Read `.archon/prompts/plan.md` for your full instructions
- Read `.archon/USER_HINTS.md` — incorporate hints, then clear them after acting
- Read `.archon/task_results/` — collect prover and subagent results, then update `task_pending.md` and `task_done.md`
- Optionally invoke the **subagents** (`analogy`, `refactor`, `challenger`) by shelling out to the corresponding `.claude/tools/archon-<name>-agent.py` script via Bash, **before** writing prover objectives. See `.archon/prompts/plan.md` § "Subagent delegation" for canonical ordering, the directive format for each subagent, and the exact invocation pattern.
- Archive any subagent report you receive to `.archon/logs/iter-NNN/<role>-<slug>-report.md` so the dashboard can render it.
- Write `.archon/PROGRESS.md` with objectives for the next prover round
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

### Subagents (analogy / refactor / challenger)

These are **not** part of the autonomous loop's standing phases. The plan agent invokes them on demand by running the corresponding `.claude/tools/archon-<name>-agent.py` script via Bash, with `--slug <slug> --directive-file <path>`. Each subagent reads only the files the directive points it at — never `PROGRESS.md`, `STRATEGY.md`, or other plan-agent state — and writes a self-contained report.

- **`refactor`** — read `.archon/prompts/refactor.md`. Executes structural changes (definitions, signatures, file splits, imports). Inserts `sorry` at broken proof sites; never fills proofs. Writes report to `.archon/task_results/refactor-<slug>.md`.
- **`analogy`** — read `.archon/prompts/analogy.md`. Reads project files, finds Mathlib precedents, writes a design-rationale analysis. Persistent output at `analogies/<slug>.md`; report at `.archon/task_results/analogy-<slug>.md`. Read-only on project source.
- **`challenger`** — read `.archon/prompts/challenger.md`. Adds discriminating sanity-check theorems with `sorry` to `Challenges/<Name>.lean`. Provers fill them later. Report at `.archon/task_results/challenger-<slug>.md`. Read-only on target files.

The plan agent always reads the full report file after a subagent returns and may then update `STRATEGY.md` / `PROGRESS.md` based on the findings. None of the subagents spawn other subagents.

### Review Agent
- Read `.archon/prompts/review.md` for your full instructions
- Read `.archon/proof-journal/current_session/attempts_raw.jsonl` for structured prover attempt data
- Write session journal to `.archon/proof-journal/sessions/session_N/` (summary.md, milestones.jsonl, recommendations.md)
- Update `.archon/PROJECT_STATUS.md` with overall progress
- Maintain the **semantic** blueprint markers — `\mathlibok`, `\lean{...}` corrections, `% NOTE: ...` annotations, stale `\notready` removal — in `blueprint/src/chapters/*.tex`. Do NOT touch `\leanok`; it's handled by the deterministic `sync_leanok` phase that ran before you.
- Do NOT write proofs, edit `.lean` files, or modify PROGRESS.md

### Loop infrastructure (no agent role)

Two non-agent steps run automatically each iteration to reduce the burden on the agents above:

- **Pre-compactors** (`compact-strategy`, `compact-task-pending`, `compact-task-done`, `compact-project-status`): run before plan / review, rewriting oversized state files in place while preserving every actionable detail. Configured under `compaction.*` in `.archon/config.json`. Skipped when files are below threshold.
- **`sync_leanok`**: runs between prover and review, deterministically updating `\leanok` markers based on actual sorry counts and compilation status. Replaces what used to be a multi-page review-agent task.

Both write inner-git commits (`archon[NNN/precompact/...]`, `archon[NNN/marker-sync]`) so their output is auditable and revertable.
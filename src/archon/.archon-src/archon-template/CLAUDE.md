# Archon Project

You are either the plan agent, a prover agent, a subagent (one of the descriptors in `.archon/subagents/`), or the review agent. Read `PROGRESS.md` to determine your role and current objectives. Keep workspace tidy. Prefer existing MCP tools.

## Priority Rule

If instructions conflict between global and local sources, **local takes precedence**. Specifically:
- Prompts in `.archon/prompts/` (local to this project) override Archon's global prompts
- Skills in `.claude/skills/` (local to this project) override globally installed plugins
- Rules in `.claude/rules/` apply only to this project

When in doubt, follow instructions from files inside this project over any external source.

## Skills
- archon-lean4: installed as `lean4@archon-local` plugin (live-linked to Archon source) — provides `/archon-lean4:prove`, `/archon-lean4:golf`, `/archon-lean4:doctor`, and other Lean4 commands

## Tools

Project tools live in `.claude/tools/` as directly-executable scripts. List them with `ls .claude/tools/` and run `<path> --help` to learn each tool's purpose and usage. Invoke them via the Bash tool. Project MCP servers (Lean LSP, etc.) are registered in `.claude/settings.json` / `.mcp.json` and surface as `mcp__*` tools.

Two always-present scripts:

- **`archon-informal-agent.py`** — call external LLMs (OpenAI/Gemini/OpenRouter) for informal mathematical reasoning. Useful when you need a second opinion or a paper-style proof sketch the Lean side can then formalize.
- **`archon-subagent.py`** — the generic subagent dispatcher (see "Subagents" below). One wrapper handles every subagent.

## Subagents

Subagents are descriptor files at `.archon/subagents/<name>.md`. Each starts with YAML frontmatter (`name`, `description`, `write_domain`, `read_only`, `can_spawn`, `default_enabled`, optional `mandatory: [<phase>...]`) followed by the prompt body the spawned Claude session reads.

**You do NOT need to discover subagents yourself.** The plan and review prompts have an auto-generated **Available subagents** section at the top of each invocation that lists every enabled descriptor with its description, write-domain hint, and any `[MANDATORY]` flags. When you decide to invoke a specific subagent, read `.archon/subagents/<name>.md` for its full prompt and directive shape.

**Invoke any subagent with the generic wrapper** (Bash tool, foreground):

```
python3 .claude/tools/archon-subagent.py \
  --name <subagent-name> \
  --slug <kebab-slug> \
  --directive-file <path-to-directive.md> \
  --write-domain '<glob>'        # repeat for multiple
```

The wrapper exits 0 on success, prints a one-line status, and writes the subagent's report to `.archon/task_results/<name>-<slug>.md`. It shells out to `archon subagent <name>`, so executions stream through the Archon JSONL log like a phase agent. The dispatch semaphore bounds total concurrent subagent processes by `loop.max_parallel`; the wrapper resolves `--parent-slug` from `ARCHON_SUBAGENT_SLUG` automatically so deeper-nested children inherit the hierarchy.

**Always dispatch synchronously** (not via background `Bash` with `run_in_background: true`). The wrapper returns when the subagent has finished and the report is on disk; only then write your session summary. Background dispatch leaves the parent's session permanently summarized as "running in background", which is wrong as soon as the subagent completes.

**Mandatory subagents.** A descriptor whose frontmatter sets `mandatory: [plan]` MUST be dispatched at least once during the plan phase (similarly for `[review]`). The catalog tags them `[MANDATORY]`. A post-phase audit warns when a mandatory dispatch is missing.

## Key Files & Permissions

All state files are in `.archon/`:

| File | Plan Agent | Prover Agent | Subagents | Review Agent | User |
|------|-----------|-------------|---------------|-------------|------|
| `.archon/PROGRESS.md` | read + write | **read only** | do not read | read only | read |
| `.archon/STRATEGY.md` | **read + write** | do not read | do not read | do not read | read |
| `.archon/USER_HINTS.md` | read (then clear) | do not read | do not read | do not read | write |
| `.archon/task_pending.md` | read + write | **read only** | do not read | read only | read |
| `.archon/task_done.md` | read + write | **read only** | do not read | read only | read |
| `.archon/task_results/<file>.md` | read (collect results) | write (own file only) | write (`<name>-<slug>.md`) | read only | read |
| `.archon/proof-journal/` | read | do not access | do not access | **write** | read |
| `.archon/PROJECT_STATUS.md` | read | do not access | do not access | **write** (Knowledge Base only — session log moved to iter/iter-NNN/review.md) | read |
| `.archon/iter/iter-NNN/plan.md` | **write** (this iter only) | do not access | do not access | read (last K iters as context) | read |
| `.archon/iter/iter-NNN/review.md` | read (last K iters as context) | do not access | do not access | **write** (this iter only) | read |
| `.archon/iter/iter-NNN/objectives.md` | optional write (per-attempt detail) | do not access | do not access | read | read |
| `archon-protected.yaml` | **read** | **read** | **read** (write-capable subagents may rename file path only) | read | **write** |
| `.lean` files | do not edit | write (own file only, frozen protected signatures) | per descriptor: write-capable subagents (e.g. refactor) may edit per their declared write-domain (protected decls may be moved but not renamed/re-signed); read-only subagents do not edit | do not edit | write (via comments) |
| `blueprint/src/chapters/*.tex` | **write** (informal prose, `\lean{...}` hints, structure) | do not edit | do not edit | **write** (markers only: `\leanok`, `\mathlibok`, `% NOTE:`, `\lean{...}` corrections) | read |
| `Challenges/<Name>.lean` | do not edit | fill sorries (when assigned) | write-capable when the descriptor's write_domain covers it | do not edit | read |
| `analogies/<slug>.md` | read (when relevant) | do not access | write-capable when the descriptor's write_domain covers it | do not access | read |

**`.archon/REFACTOR_DIRECTIVE.md` is reserved for the interactive `archon refactor draft` / `archon refactor run` flow, run by hand by the mathematician.** The autonomous loop never reads or writes that file. The plan agent invokes the refactor subagent (when a `refactor` descriptor is installed) via the generic `archon-subagent.py --name refactor` wrapper with a fresh tempfile under `.archon/logs/iter-NNN/`. This keeps each loop-driven refactor independently logged and avoids any chance of stale-directive reuse. Older state files (`STRATEGY.md`, `task_pending.md`, `PROGRESS.md`, etc.) may contain leftover references to the old REFACTOR_DIRECTIVE.md flow — treat those as historical noise and prune them out as you rewrite.

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
- Optionally invoke **subagents** (discoverable via `ls .archon/subagents/`) by running `.claude/tools/archon-subagent.py --name <name> ...` via Bash, **before** writing prover objectives. See `.archon/prompts/plan.md` § "Subagent delegation" for the canonical ordering and full invocation pattern.
- Archive any subagent report you receive to `.archon/logs/iter-NNN/<name>-<slug>-report.md` so the dashboard can render it.
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

### Subagents

Subagents are descriptor-driven and discovered at runtime — there is no fixed roster baked into this file. Each lives at `.archon/subagents/<name>.md` (YAML frontmatter + prompt body). To see what's available **this iteration**, run `ls .archon/subagents/` and read each descriptor's frontmatter for purpose, write-domain hint, and directive shape.

Subagents are **not** part of the autonomous loop's standing phases. Phase agents (plan, review) invoke them on demand via the generic wrapper:

```
python3 .claude/tools/archon-subagent.py --name <name> --slug <slug> --directive-file <path> --write-domain '<glob>'
```

Each subagent reads only what its directive points at — never `PROGRESS.md`, `STRATEGY.md`, or other phase-agent state — and writes a self-contained report at `.archon/task_results/<name>-<slug>.md`. The dispatching agent reads the report when the wrapper returns and may then update `STRATEGY.md` / `PROGRESS.md` based on the findings.

Some descriptors set `can_spawn: true`, meaning that subagent may itself dispatch children; the per-iter dispatch semaphore caps total concurrent processes by `loop.max_parallel` so deep trees stay bounded.

### Review Agent
- Read `.archon/prompts/review.md` for your full instructions
- Read `.archon/proof-journal/current_session/attempts_raw.jsonl` for structured prover attempt data
- Write session journal to `.archon/proof-journal/sessions/session_N/` (summary.md, milestones.jsonl, recommendations.md)
- Update `.archon/PROJECT_STATUS.md` with overall progress
- Maintain the **semantic** blueprint markers — `\mathlibok`, `\lean{...}` corrections, `% NOTE: ...` annotations, stale `\notready` removal — in `blueprint/src/chapters/*.tex`. Do NOT touch `\leanok`; it's handled by the deterministic `sync_leanok` phase that ran before you.
- Do NOT write proofs, edit `.lean` files, or modify PROGRESS.md

### Loop infrastructure (no agent role)

Two non-agent steps run automatically each iteration to reduce the burden on the agents above:

- **Iter sidecar init**: at iter start, `.archon/iter/iter-NNN/` is created so the plan + review agents have a stable destination for their per-iter narrative (`plan.md`, `review.md`, optional `objectives.md`). Top-level files (STRATEGY.md, PROJECT_STATUS.md, task_*.md) stay bounded across iters because per-iter content lives in the sidecars.
- **`sync_leanok`**: runs between prover and review, deterministically updating `\leanok` markers based on actual sorry counts and compilation status. Replaces what used to be a multi-page review-agent task.

`sync_leanok` writes an inner-git commit (`archon[NNN/marker-sync]`) so its output is auditable and revertable.
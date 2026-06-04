# Archon Project

You are one of: the plan agent, a prover agent, a subagent (per descriptor in `.archon/subagents/`), or the review agent. Read `PROGRESS.md` to determine your role and current objectives. Keep workspace tidy. Prefer existing MCP tools.

## Priority Rule

When instructions conflict between global and local sources, **local takes precedence**:

- Prompts in `.archon/prompts/` override Archon's global prompts.
- Skills in `.claude/skills/` override globally installed plugins.
- Rules in `.claude/rules/` apply only to this project.

## Skills

- **archon-lean4** — installed as `lean4@archon-local` plugin (live-linked to Archon source). Provides `/archon-lean4:prove`, `/archon-lean4:golf`, `/archon-lean4:doctor`, etc.

## Tools

Project tools live in `.claude/tools/` as directly-executable scripts. List with `ls .claude/tools/`; run `<path> --help`. MCP servers (Lean LSP, etc.) are registered in `.claude/settings.json` / `.mcp.json` and surface as `mcp__*` tools.

Two always-present scripts:

- **`archon-informal-agent.py`** — call external LLMs for an informal proof-style sketch when you're stuck on an approach and want a second opinion. Supports DeepSeek, Kimi (OpenAI-compatible via `--provider kimi`), Kimi (Anthropic-compatible via `--provider kimi-anthropic`), OpenRouter, OpenAI, and Gemini; defaults to `--provider auto` which picks the best available key automatically. **Requires at least one key in env** (`DEEPSEEK_API_KEY` / `MOONSHOT_API_KEY` / `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`) — check via `env | grep -E "DEEPSEEK|MOONSHOT|OPENROUTER|OPENAI|GEMINI"` BEFORE planning around its use; if no key is set, fall back to alternatives below. Both `kimi` and `kimi-anthropic` use the same `MOONSHOT_API_KEY`; the latter speaks Moonshot's Anthropic-compatible Messages endpoint and supports `--think`. The output is LLM-generated from training data, NOT source-derived: this tool is **not** a literature retriever. For actual literature lookup (cross-check a claim against Hartshorne, fetch an arXiv paper, summarize a Stacks tag) consult the auto-injected subagent catalog — when a literature/reference-fetching subagent is enabled it will appear there — or use the `WebSearch` / `WebFetch` tools directly.
- **`archon-subagent.py`** — the generic subagent dispatcher (one wrapper handles every subagent — see "Subagents" below).

## Subagents

Subagents are descriptor files at `.archon/subagents/<name>.md` (YAML frontmatter — `name`, `description`, `write_domain`, `read_only`, `can_spawn`, `default_enabled`, optional `mandatory: [<phase>...]`, optional `dispatcher_notes` — followed by the prompt body the spawned Claude reads).

**You do NOT need to discover subagents.** The plan and review prompts auto-inject an **Available subagents** section at the top of each invocation listing every enabled descriptor with description, write-domain, MANDATORY flag, and `dispatcher_notes`. When you decide to invoke a subagent, read its full prompt at `.archon/subagents/<name>.md`.

**Invoke** via the generic wrapper (Bash tool, foreground):

```
python3 .claude/tools/archon-subagent.py \
  --name <subagent-name> \
  --slug <kebab-slug> \
  --directive-file <path-to-directive.md> \
  --write-domain '<glob>'        # repeat for multiple
```

The wrapper exits 0 on success, prints a one-line status, writes the report to `.archon/task_results/<name>-<slug>.md`, and the Archon CLI auto-archives a copy to `logs/iter-NNN/<name>-<slug>-report.md` for the dashboard. The dispatch semaphore caps concurrent subagent processes at `loop.max_parallel`; the wrapper resolves `--parent-slug` from `ARCHON_SUBAGENT_SLUG` so nested children inherit the hierarchy.

**Treat each dispatch as blocking — wait for its report before acting on it.** Don't *deliberately* set `run_in_background: true`. But note the wrapper is genuinely synchronous (it `subprocess.run`s the `archon subagent` CLI and only returns once the child finishes and its report is on disk), so the harness may *auto-background* a long-running dispatch and hand you a task ID immediately — that's expected and fine. When it does, await that task / poll for the report file at `.archon/task_results/<name>-<slug>.md` (the wrapper's one-line status and exit 0 confirm completion) before you read or act on the result. The thing to avoid is firing a dispatch and then continuing to plan as if it had already returned; whether the call blocked inline or was auto-backgrounded, the rule is the same: do not consume a subagent's output until its report exists.

**Highly-recommended subagents.** A descriptor whose frontmatter sets `mandatory: [plan]` is treated as **highly recommended** for the plan phase (similarly `[review]` for the review phase). The catalog tags them `[HIGHLY RECOMMENDED]`. Dispatch each one unless you have a concrete reason to skip — when skipping, record a one-line rationale under a `## Subagent skips` section in `iter/iter-NNN/<phase>.md` (e.g. `- strategy-critic: STRATEGY.md SHA unchanged from prior iter and last verdict was SOUND`). A post-phase audit checks both signals: it silences when the subagent dispatched OR a skip rationale was recorded, and warns only when neither happened. Each subagent's `dispatcher_notes` enumerates its specific skip conditions — read them before deciding. **Subagents ship disabled by default** (`default_enabled: false`), so the rule only fires for subagents the user has explicitly listed under `subagents.enabled` in `.archon/config.json`; on a fresh project the catalog is empty and no recommended dispatch is required. (The frontmatter field is still named `mandatory: [...]` for backward compat with existing configs and descriptors — its semantic meaning is "highly recommended", not "must dispatch".)

## Key Files & Permissions

All Archon state files are in `.archon/`. The table below covers **phase agents** (plan, prover, review) and the **user**. Subagent permissions are NOT a fixed row — they are descriptor-driven (`write_domain` glob in the subagent's frontmatter, enforced at dispatch time). See "Subagent permissions" after the table.

| File | Plan | Prover | Review | User |
|------|------|--------|--------|------|
| `PROGRESS.md` | read + write | read only | read only | read |
| `STRATEGY.md` | read + write | — | — | read |
| `USER_HINTS.md` | **loop-managed** (captured + cleared by the CLI; injected into the plan prompt) | — | — | write |
| `task_pending.md` | read + write | read only | read only | read |
| `task_done.md` | read + write | read only | read only | read |
| `task_results/<file>.md` | read (collect) | write (own file) | read only | read |
| `proof-journal/` | read | — | **write** | read |
| `PROJECT_STATUS.md` | read | — | **write** (Knowledge Base only — session log lives in `iter/iter-NNN/review.md`) | read |
| `iter/iter-NNN/plan.md` | **write** (this iter only) | — | read (recent window injected) | read |
| `iter/iter-NNN/review.md` | read (recent window injected) | — | **write** (this iter only) | read |
| `iter/iter-NNN/objectives.md` | optional write | — | read | read |
| `archon-protected.yaml` | read | read | read | **write** |
| `.lean` files | — | write (own file; frozen protected signatures) | — | write (via comments) |
| `blueprint/src/chapters/*.tex` | **write** (informal prose, `\lean{...}` hints) | read | **write** (semantic markers: `\mathlibok`, `% NOTE:`, `\lean{...}` corrections — NOT `\leanok`) | read |
| `analogies/<slug>.md` | read (when relevant) | — | — | read |
| `TO_USER.md` | **write** (shared notice board) | **write** (shared notice board) | **write** (shared notice board) | read |

### Subagent permissions

Each subagent ships YAML frontmatter:

- `write_domain: "<glob>"` — file pattern the subagent may write. Empty for purely read-only subagents (the dispatching call typically passes `--write-domain 'task_results/**'` so the subagent can still write its own report).
- `read_only: true | false` — when `true`, the subagent may only write its `task_results/<name>-<slug>.md`.
- `can_spawn: true | false` — when `true`, the subagent may dispatch children whose `write_domain` is a subset of its own.

The dispatcher enforces `write_domain` at the filesystem level: any child whose declared domain is not a subset of the parent's is rejected before the child agent starts. Reads are restricted only by the descriptor's prompt body (e.g. fresh-context critics that are told not to read PROGRESS.md / STRATEGY.md).

**`.archon/REFACTOR_DIRECTIVE.md`** is reserved for the interactive `archon refactor draft` / `archon refactor run` flow run by hand by the mathematician. The autonomous loop never reads or writes that file. To invoke a refactor-style subagent inside the loop, write the directive to a fresh tempfile under `.archon/logs/iter-NNN/<name>-<slug>-directive.md` and dispatch via the generic wrapper. Older state files (`STRATEGY.md`, `task_pending.md`, `PROGRESS.md`) may contain leftover references to the old REFACTOR_DIRECTIVE.md flow — treat those as historical noise.

## Protected Declarations

`archon-protected.yaml` at the project root lists declarations whose **signatures are frozen** by the mathematician. Rules:

- **Plan / prover / review** agents: read-only on protected signatures. May fill proof bodies; may not rename, re-type, or reorder arguments.
- **Write-capable structural subagents** (per their `write_domain`): may *move* a protected declaration to a different file (name + signature preserved verbatim) and must then update the path key in `archon-protected.yaml`. They may NEVER rename / re-type / delete / re-sign a protected declaration.
- The YAML file itself is user-only. No agent adds or removes a declaration. The only allowed agent edit is updating an existing declaration's file path.

## Blueprint Marker Vocabulary

Two semantic markers:

- `\leanok` — statement block: declaration is formalized (at least a `sorry` present). Proof block: proof closed, no `sorry`.
- `\mathlibok` — statement block: declaration is backed by Mathlib (re-export / alias); no Archon proof obligation remains.

No marker means the block is unformalized. If a block fails to translate, leave it unmarked and annotate with a `% NOTE:` comment.

**`\leanok` is managed deterministically by the `sync_leanok` phase** that runs between prover and review each iteration. It walks every chapter, looks up each `\lean{...}` declaration, runs `sorry_analyzer` + `lake env lean`, and adds/removes `\leanok` accordingly. Agents must NOT add or remove `\leanok` themselves.

`\mathlibok`, `\lean{...}` corrections, and `% NOTE:` annotations are the review agent's domain (they require semantic judgement). The plan agent writes informal prose and `\lean{...}` hints; provers never touch the blueprint.

## User Interaction

The user provides hints in two places:

- **Strategic hints** → `.archon/USER_HINTS.md`. The Archon loop captures this file's content before each plan phase, injects it into the plan agent's prompt, and resets the file to its bundled template (an HTML-comment format guide + zero bullets) after the plan phase succeeds. The HTML-comment preamble is stripped before injection so a template-only file renders as "no hints" to the planner. The plan agent does NOT read or clear that file itself. Provers never see these hints.
- **File-specific hints** → `/- USER: ... -/` comments directly in `.lean` files. The prover that owns that file sees them naturally.

Archon talks back to the user through **`TO_USER.md`** — a single, *persistent* notice board surfaced as a UI banner. It is **shared and writable by every agent** (plan, prover, review). It is no longer cleared at the start of each review phase; content survives across iters until an agent prunes it. The discipline every writer follows:

- **Concise**: the whole file stays ≤ 2–3 short bullets. It is a banner, not a log.
- **Relevant now**: before adding anything, read the current content and **delete any bullet that is no longer true at this point** (the blocker was resolved, the decision was overtaken, the credential got set). Never let it accumulate stale notices.
- **Notice board, never a question queue**: the loop is autonomous and may run unattended for many iters. Never write "awaiting your decision", an options menu, a "where to reply", or "default to X if no reply" — anything implying the loop is paused for a human. State decisions as *made* and blockers as *worked-around-as-far-as-possible*; the user steers asynchronously via `USER_HINTS.md`.

## Agent Roles

### Plan Agent

- Read `.archon/prompts/plan.md` for full instructions.
- User hints, blueprint-doctor findings, recent iter sidecars, the subagent catalog, and the references summary are all pre-injected into your invocation prompt. You do not "go read" those files.
- Read `task_results/` to collect prover and subagent results, then update `task_pending.md` / `task_done.md`.
- Optionally invoke subagents (via the auto-injected catalog) before writing prover objectives. Subagent reports are auto-archived to `logs/iter-NNN/` by the Archon CLI.
- Write `PROGRESS.md` with objectives for the next prover round.
- Write informal prose in `blueprint/src/chapters/*.tex`. Do NOT touch markers — `\leanok` is owned by the deterministic sync; `\mathlibok` / `% NOTE:` by the review agent.
- Do NOT write proofs, edit `.lean` files, or fill sorries yourself.

### Prover Agent

- Read `PROGRESS.md` for your objectives (read only).
- Your **active mode** is injected directly into your invocation prompt — read it carefully, it defines your goal, workflow, and constraints for this session.
- When your mode specifies `read_blueprint: true`, you are permitted to **read** your assigned blueprint chapter (`blueprint/src/chapters/<your_slug>.tex`) for the informal proof sketch. You may never write to blueprint chapters.
- Write results to `task_results/<your_file>.md`.
- Write only to the `.lean` file(s) you are assigned — never edit another agent's file.
- Check for `/- USER: ... -/` comments in your file for file-specific hints.
- Do NOT edit blueprint chapters. Flag in your task result which declarations are ready for which marker; the review agent applies them.

### Subagents

Subagents are descriptor-driven and discovered at runtime. The catalog is auto-injected into the plan and review prompts each phase (see "Subagents" above for the wrapper invocation). Each subagent reads only what its directive points at — never `PROGRESS.md` / `STRATEGY.md` / phase-agent state — and writes a self-contained report at `task_results/<name>-<slug>.md`. Descriptors with `can_spawn: true` may dispatch children (subject to the global `loop.max_parallel` cap).

### Review Agent

- Read `.archon/prompts/review.md` for full instructions.
- Read `proof-journal/current_session/attempts_raw.jsonl` for structured prover attempt data.
- Write the session journal to `proof-journal/sessions/session_N/` (`summary.md`, `milestones.jsonl`, `recommendations.md`).
- Update `PROJECT_STATUS.md` (Knowledge Base only — session narrative goes to `iter/iter-NNN/review.md`).
- Maintain the semantic blueprint markers — `\mathlibok`, `\lean{...}` corrections, `% NOTE:` annotations, stale `\notready` removal. Do NOT touch `\leanok`.
- Do NOT write proofs, edit `.lean` files, or modify `PROGRESS.md`.

### Loop infrastructure (no agent role)

These run automatically between phases:

- **Iter sidecar init** — creates `.archon/iter/iter-NNN/` at iter start so plan + review have a stable destination for per-iter narrative. Top-level files (STRATEGY.md, PROJECT_STATUS.md, task_*.md) stay bounded.
- **`sync_leanok`** — runs between prover and review, deterministically updating `\leanok` markers based on actual sorry counts and compilation status.
- **`blueprint-doctor`** — runs between prover and review, deterministically detecting orphan chapters, broken `\ref` / `\uses` / `\proves`, and new `axiom` declarations. Findings are written to `logs/iter-NNN/blueprint-doctor.{md,json}` and inlined into the next plan agent's prompt.

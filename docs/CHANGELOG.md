# Changelog

All notable changes to Archon are documented here.

## [Unreleased]

### Added

- **Harness router (opt-in, off by default).** Route any responsibility
  (plan / prover / review / subagent / lane) to a named *harness* via
  `harnesses.<name>` + `loop.harness` / `loop.roles.<role>`. A `build_runner`
  factory behind a new `AgentRunner` protocol (implemented by `ClaudeAgent`)
  replaces direct `ClaudeAgent` construction; resolvers mirror
  `resolve_subagent_model`'s precedence. **No harness config ⇒ behavior
  unchanged** (`build_runner` returns exactly `ClaudeAgent(model, role)`).
- **Codex runner (opt-in).** `runner: "codex"` routes a role (typically
  `prover`) to `codex exec`, sharing `ClaudeAgent`'s subprocess supervision via
  an extracted `supervise_streamed_run`. Gateway creds in `.archon/.env` (API
  key only in the child env, never argv); optional lean-lsp MCP
  (`mcp: "lean-lsp"`) and codex prompt variant (`prompt_variant: "codex"`).
- **Codex dashboard / token parity.** Codex's `exec --json` stream is normalized
  by a peer `_CODEX_STREAM_PARSER` (the claude parser is renamed
  `_CLAUDE_STREAM_PARSER`) into the same `session_meta` / `text` / `tool_call` /
  `tool_result` / `session_end` schema claude-code emits, so the dashboard renders
  codex runs and the cost/token aggregators count codex tokens. `session_end`
  records the token breakdown (fresh `input_tokens` = input − cached, plus
  cache-read / output / reasoning) and omits `total_cost_usd` — codex bills no
  per-token price on a native login. The dashboard's "turns" column reports
  codex's **iteration count** (completed items / work steps), since one `codex
  exec` is a single turn (always 1, meaningless) — this lines up with claude's
  round-trip "turns".
- **Harness selection at `archon init`.** A fresh `archon init` now asks which
  engine runs the loop's roles — Claude Code + Opus (default), Codex + GPT-5.5
  (native `~/.codex` login), or Mixed (per `plan` / `prover` / `review`; the
  recommended mix routes only the prover to codex) — and writes the choice as
  `loop.harness` / `loop.roles`. Deterministic prompt fired only on a brand-new
  `config.json` (a keep/merge re-init stays silent); `archon init --harness
  {claude-code,codex-gpt,mixed}` presets it and skips the menu. The default config
  now ships an **inert** `harnesses` block (a `codex-gpt` descriptor, native login
  by default; gateway noted in a comment) — never consulted unless a
  `loop.harness` / `loop.roles` key references it, so the zero-config
  `build_runner` short-circuit is preserved. The choice governs only the loop's
  roles; init's own semantic pass stays on interactive Claude Code.

### Known limitations

- Codex: no per-token USD cost on a native login (tokens are recorded; the cost
  column stays 0); `run_interactive` unsupported
  (interactive / init / merge stay claude-code); multilane lane axis is
  claude-code-only (a non-claude lane harness is rejected). Codex is more
  adversarial — gate its proofs behind a kernel-level checker. `gemini` runner
  unimplemented (raises).

## [0.2.0] — 2026-05

This release adds **multi-lane parallel proving**, a dedicated **refactor agent**,
**inner-git versioning** of agent work, the `archon discuss` / `branch` / `version`
commands, a project-level frozen-signature surface (`archon-protected.yaml`),
an **opt-in subagent system** (blueprint review, strategy critique, Mathlib
design advice, refactor, and more), a **`--resume`** flag for interrupted
runs, a **blueprint-doctor** phase that catches blueprint structural drift,
and a **post-plan validation step** that auto-corrects common `PROGRESS.md`
heading drift. The codebase has also been reorganised: large command modules
now live as packages (`archon.commands.loop`, `archon.commands.init`, …) so
each phase, step, or runner is a small focused class.

**Default single-agent behavior is preserved** — subagents and multilane both
ship disabled and the loop runs the same shape as v0.1.0 unless you opt in.

Upgrading from v0.1.0? See [MIGRATION.md](MIGRATION.md#7-upgrading-from-v010-to-v020).

### Added

- **Multi-lane proving**: parallel prover lanes that run different LLM
  providers (Anthropic, Moonshot/Kimi, DeepSeek) on the same Lean files in
  isolated worktrees under `.archon/lanes/<lane>/`. The first lane to finish a
  file cleanly wins; other lanes get a 10-minute grace period and are then
  cancelled. A per-file merge agent picks the best proof per declaration across
  the lanes that did finish. See
  [MULTILANE.md](https://github.com/frenzymath/Archon/blob/main/src/archon/.archon-src/archon-template/MULTILANE.md)
  for setup. Multilane is opt-in via `.archon/config.json`; the default is a
  single Anthropic lane.
- **Refactor agent** + `archon refactor`: structural changes (wrong
  definitions, signature changes, file splits) are handled by a dedicated
  refactor agent that may edit any `.lean` file (subject to
  `archon-protected.yaml`). In the autonomous loop the plan agent dispatches
  the `refactor` subagent directly via the Agent tool, passing the directive
  inline — nothing is staged in a file. For hands-on use, the interactive
  `archon refactor draft` command interviews you and writes
  `.archon/REFACTOR_DIRECTIVE.md`, and `archon refactor run` then executes
  it.
- **`archon-protected.yaml`** at the project root: declares signatures that
  are frozen by the mathematician. No agent may rename or re-sign listed
  declarations; the refactor agent may move them between files.
- **Inner-git versioning** at `.archon/git-dir/`: every agent phase commits
  its work as `archon[NNN/phase]: ...` so the dashboard's git tree shows the
  per-phase history independently of the project's outer git. The new
  `archon branch` command forks a branch from any historical agent commit so
  you can reset bad runs without losing the rest of the work.
- **`archon discuss`**: launches Claude Code interactively in the project
  with full Archon context loaded. Useful for debugging stuck proofs or
  brainstorming strategy without starting a full loop iteration.
- **`archon version`**: prints the CLI version and, inside a project, the
  project version stamped by `archon init` into `.archon/VERSION`.
- **Dashboard improvements**:
  - Live diff fallback: when the current iteration is mid-flight and no
    snapshot or git commit exists yet, the diff view now reads the file from
    the live working tree instead of showing an empty baseline.
  - Multilane integration: `/api/logs` walks the lane subtree so per-lane
    prover logs (`<file>__<lane>.jsonl`) appear alongside single-lane logs.
  - Smarter branch ordering and stale-prompt warnings in the proof graph.
  - Blueprint chapter rendering reads `\newcommand` /
    `\DeclareMathOperator` from `blueprint/src/macros/*.tex` and passes them
    to KaTeX.
- **Dependency-graph script** bundled with the lean4 skill
  (`$LEAN4_SCRIPTS/dependency_graph.py`): emits a JSON / DOT / summary view of
  every `.lean` import + every blueprint `\lean{…}` / `\uses{…}` /
  `\proves{…}` / `\leanok` / `\notready` marker. The plan prompt now points
  agents at it instead of asking them to reconstruct the dependency map by
  hand.
- **Per-project config**: `.archon/config.json` (loop and multilane settings,
  versioned with the project) and `.archon/.env` (informal-agent and
  multilane provider keys, gitignored).
- **Iteration-number canonicalization**: every agent prompt now carries
  `Archon iteration: NNN` matching the `logs/iter-NNN/` and
  `archon[NNN/phase]` commit numbering, so plan / refactor / review agents no
  longer drift into their own ad-hoc counters in `STRATEGY.md` or
  `proof-journal/.../recommendations.md`.
- **Tooling foundation** under `archon.commands.tooling.*`: blueprint helpers,
  lake/mathlib detection, `InnerGit` wrapper, `ProjectLayout`, env-loader,
  per-project config, `protect` (yaml reader), version stamping. Reused
  across init, loop, refactor, doctor, and discuss.
- **Modular code layout**: `loop.py`, `init.py`, `setup.py`, `dashboard.py`
  are now packages (`commands/loop/`, `commands/init/`, …) where each phase
  / step / runner is a small class in its own file. The largest
  per-file footprint dropped from ~2400 lines (loop.py) to under 500.
- **Subagents** (opt-in). Nine descriptor-driven subagents shipped under
  the bundled `subagents/` directory. Each is a `.md` file with YAML
  frontmatter (`name`, `description`, `write_domain`, `read_only`,
  `can_spawn`, `default_enabled`, optional `mandatory: [<phase>]`) plus a
  prompt body. The plan / review prompts auto-inject an "Available
  subagents" catalog at the top of every invocation; the dispatching agent
  reads each descriptor's body before composing its directive.

  | Subagent | Phase | Role |
  |----------|-------|------|
  | `blueprint-reviewer` | plan | Whole-blueprint audit with per-chapter completeness/correctness checklists and a hard prover-dispatch gate. |
  | `blueprint-writer` | plan | Updates one chapter to reflect strategy changes; may spawn `reference-retriever`. |
  | `strategy-critic` | plan | Fresh-context critic of `STRATEGY.md` (no iter history) — challenges sunk-cost reasoning. |
  | `progress-critic` | plan | Convergence detector that flags CHURNING / STUCK / UNCLEAR routes from the last K iters' signals. |
  | `mathlib-analogist` | plan | Locates Mathlib idioms for new infrastructure decisions; catches parallel-API patterns before they harden. |
  | `reference-retriever` | plan | Fetches papers / books / online math and registers them under `references/`. |
  | `refactor` | plan | Executes structural Lean changes under plan-agent direction; inserts `sorry` at broken proof sites. |
  | `lean-auditor` | review | Whole-project read-only Lean audit, with no strategy bias. |
  | `lean-vs-blueprint-checker` | review | Per-file bidirectional verifier (Lean ↔ blueprint). |

  All ship with `default_enabled: false`. Enable one or more by listing
  names under `subagents.enabled` in `.archon/config.json`. The shipped
  config has an `_available` list of every subagent name; copy any of
  them into `enabled` to turn it on. `mandatory: [<phase>]` enforcement
  fires only for subagents the user has enabled — on a fresh project the
  catalog is empty and the rule does nothing.
- **`--resume`** for `archon loop`. When a previous loop crashed
  mid-iteration, `archon loop --resume` picks up at the interrupted phase.
  The phase is auto-detected from the prior iter's `meta.json`, and
  per-phase session ids are now captured so Claude Code re-attaches to the
  right session.
- **Blueprint-doctor phase**: runs each iteration between the prover and
  review phases (after the `\leanok` sync) and reports orphan chapters,
  broken `\ref{}` / `\uses{}` references, malformed (empty) annotations,
  stray `axiom` declarations, and `% archon:covers` integrity problems. It
  writes `.archon/logs/iter-NNN/blueprint-doctor.{md,json}`; the same iter's
  review agent reads it and the next iter's plan agent sees the findings
  inline under `## Blueprint doctor — live structural findings`. No separate
  command needed.
- **Axiom-sweep phase** (opt-in via `loop.axiom_sweep` in
  `.archon/config.json`): runs between the blueprint-doctor and review
  phases to catch `sorryAx` laundering — declarations that compile with no
  `sorry` warning yet still depend on `sorryAx` through a clean-compiling
  delegate, which the warning-based sorry count misses. Writes
  `.archon/logs/iter-NNN/axiom-sweep.{md,json}`; never blocks the loop.
- **Plan-validate post-plan step**: catches common `PROGRESS.md` heading
  drift (`## Strategy` → `## Current Objectives`), auto-fixes it, and
  warns the planner instead of silently wasting a prover round.
- **Dashboard polish** (additional, on top of the v0.1.0 dashboard):
  - Each iter group's logs sort chronologically by the first-event
    timestamp of each file — plan / its subagents / refactor / provers /
    review / its subagents now appear in execution order instead of
    alphabetical.
  - When a subagent stream is selected, its final report markdown is
    inlined at the top of the panel (no separate sidebar row for the
    report).
  - The "Summary" panel reads the last assistant text emitted, so
    intermediate "Waiting for subagents…" announcements no longer pin the
    summary box on a stale message.
  - Subagent rows in the sidebar drop the redundant `iter<N>` slug suffix
    (the iter is already in the group header).
  - Log entries laid out as a strict three-column grid (timestamp · event
    label · content) with a hairline row separator instead of the
    previous wall-of-text feel.
  - Overview and Journal views expand to the full content-area width.
  - Connection-error banner points at `archon dashboard <project>`.

### Changed

- **`max_parallel` default lowered from 8 to 4.** Closer to the safe
  upper bound for most users on a single workstation. Existing
  `.archon/config.json` files keep their explicit value; only fresh
  projects pick up `4`. Restore the v0.1.0 default with `--max-parallel 8`
  or by editing `config.json`.
- A `STRATEGY.md` is managed by the plan agent to capture the long-term strategy.

### Fixed

- `--resume` correctly maps the interrupted phase from `meta.json`
  instead of always restarting at plan.
- Prover bash-as-MCP confusion at cold-LSP startup, with explicit warm-up.
- Stale `meta_temp` files and partially-written `meta.json` no longer
  trip the resume path.
- Automatic re-scan when sorries are added back after project is marked COMPLETE
- `mathlib-unavailable-theorems.md` was not accurate, it is now version-stamped and has been updated. 
- `PROGRESS.md` parsing is more resilient to heading drift and formatting changes.

## [0.1.0] — 2026-04

It replaces the earlier shell-script checkout workflow with an installable `archon` CLI, adds an
auto-launching dashboard and graph visualization, and makes re-initializing an already-initialized project safe and interactive.

Upgrading an existing project? See [MIGRATION.md](MIGRATION.md).

### Added

- **`archon` CLI** with commands `init`, `loop`, `dashboard`, `doctor`,
  `prove`, `setup`, and `update`. Replaces `archon-loop.sh`, `init.sh`,
  `review.sh`, and related shell scripts.
- **One-line installer** at
  `https://raw.githubusercontent.com/frenzymath/Archon/refs/heads/main/install.sh`,
  runnable with `curl ... | bash`.
- **`archon update`** command to update the installed CLI without cloning
  the repository manually.
- **Interactive re-init flow**: when a project is already initialized,
  `archon init` offers `keep` / `merge` / `overwrite` / `abort`. The
  `merge` mode launches Claude Code to reconcile prompts and `CLAUDE.md`
  file-by-file.
- **Legacy-layout detection**: older projects that used symlinked prompts
  are detected and migrated gracefully instead of erroring.
- **Auto-launching web dashboard**: `archon loop` starts the dashboard in
  the background on a free port in 8080–8099 and prints the URL. Disable
  with `--no-dashboard`; open a browser automatically with `--open`. The
  dashboard persists after the loop finishes so results can be reviewed.
- **Graph view** in the dashboard UI: interactive proof dependency graph.

### Known limitations

- The bundled informal agent remains a single-call demonstration. Our
  internal richer implementation is not yet ready for open-source release.
- Single-problem benchmarks (e.g. competition problems) are not a target;
  Archon is optimized for multi-file, project-level formalization.

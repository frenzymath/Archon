# Changelog

All notable changes to Archon are documented here.

## [0.3.3] — 2026-08

A patch release: incremental dashboard, workflow, and safety improvements over
0.3.2. The core proving loop is unchanged, and every 0.3.1/0.3.2 fix carries
forward.

### Added

- **Workspace-only safe mode** ([#40](https://github.com/frenzymath/Archon/issues/40)).
  `archon loop --safe` keeps sandboxed commands automatic while confining
  writes to the active project through Claude Code's native filesystem sandbox
  or Codex `workspace-write`. Reads and network access remain available;
  operations outside the workspace fail instead of prompting or falling back
  to unsandboxed execution. The equivalent persistent setting is
  `loop.safe: true`.
- **Scope & roadmap.** A Scope Home dashboard view with interactive
  project-status checklists, and an `archon scope roadmap` agent that launches
  an interactive session and condenses the plan into milestones rather than an
  exhaustive list.
- **Static dashboard export** (`archon dashboard --static-build`). Builds a
  self-contained static copy of the dashboard suitable for GitHub Pages, so a
  project's DAG/blueprint/logs can be published without a running server.
- **Token & USD cost tracking.** Per-operation and turn-level token and dollar
  accounting, surfaced in the UI log viewer.
- **Multi-project dashboard.** A project switcher and a Meta DAG *union* view
  across peer projects (building on `.archon/peers.yaml`), so several projects
  can be monitored — and their DAGs merged — from one dashboard.
- **Layered DAG layout** (#31). A "Layered · by depth" option in the DAG view
  alongside the force-directed default: nodes are ranked by longest dependency
  chain and stacked top-down, so the prerequisite structure reads clearly.

### Fixed

- **Dashboard server silently exited on native Windows.** CLI entry-point
  detection now compares normalized native paths instead of a file URL with a
  backslash path. [#43](https://github.com/frenzymath/Archon/issues/43).

## [0.3.2] — 2026-07

A patch release: two bug fixes reported against v0.3.1, one contributed
enhancement, and a version-badge correction. No configuration changes.

### Added

- **Dashboard behind a path-prefix reverse proxy.** The dashboard now works
  when mounted under a URL sub-path (jupyter-server-proxy, code-server
  `/proxy/<port>/`, nginx/k8s ingress sub-paths), not just at the origin root.
  Every change is a no-op at the root, so existing localhost/root deployments
  are unaffected. Contributed by [@surenny](https://github.com/surenny) in
  [#29](https://github.com/frenzymath/Archon/pull/29).

### Fixed

- **`sync_leanok` stripped valid `\leanok` markers every round.** The decl
  index didn't exclude `.archon`, so full-source history snapshots poisoned it
  and a snapshot path could win over the real declaration — the marker was then
  stripped (reproducibly ~48/round).
  [#32](https://github.com/frenzymath/Archon/issues/32).
- **`archon loop` crashed with `FileNotFoundError: 'claude'` mid-run.** The
  Claude Code CLI auto-updates by swapping its launcher on disk; agent spawns
  now retry through the swap. [#30](https://github.com/frenzymath/Archon/issues/30).
- **Dashboard version badge stuck at 0.3.0.** The UI manifests were never
  bumped for 0.3.1; all manifests now track the package version.

## [0.3.1] — 2026-06

A hotfix release: three bugs reported against v0.3.0, fixed without pulling in
the in-progress feature work on the dev branch.

### Fixed

- **`archon init` (merge mode) crashed with `UnboundLocalError: project_path`.**
- **Re-init left `PROGRESS.md` stuck at `init`** on projects that already had
  Lean content, so `archon loop` / `archon dag` refused to run; re-init now
  reconciles the stage from the detected declarations.
- **`sync_leanok` corrupted inline `\leanok` math.** The line regex swallowed
  the rest of the line, so `\leanok \(x = y\)` lost its opening `\(` and crashed
  plastex; it is now anchored to a `\leanok`-alone line.

## [0.3.0] — 2026-06

Two headlines. First, a **modular engine system**: every role and subagent can
run on the built-in Claude Code engine or on **OpenAI Codex**, selected via named
**harnesses**, and the Claude engine itself can be launched through several
**backends** (including the `claude-p` workaround for Anthropic's headless
`claude -p` rate limits). Second, **DAG-grounded blueprints**: the new
`archon dag` loop writes a coherent LeanBlueprint dependency graph, queried
deterministically through [LeanDag](https://github.com/AxelDlv00/LeanDAG) instead
of the LLM's fuzzy internal picture. Most of v0.3.0 is opt-in — the default
single-lane Claude Code loop runs the same shape as before.

Upgrading from v0.2.0? Run `archon update` (then `archon init` in each project).
See [MIGRATION.md §8](MIGRATION.md#8-upgrading-to-v030).

### Added

- **`archon dag` — blueprint-writing DAG loop.** A dedicated loop that builds a
  coherent informal LeanBlueprint graph (definitions, theorems, chapter
  structure, `\uses{...}` links, coverage annotations) before/around the main
  proving loop, so the planner and provers share grounded context. Backed by the
  [leandag](https://github.com/AxelDlv00/LeanDAG) API, which exposes the project's
  real dependency DAG and a character-count effort estimate used to order the
  proving queue and break down high-effort theorems.
- **Harnesses (modular engines).** Route any role (`loop.harness` /
  `loop.roles.<role>`) or subagent (`subagents.<name>.harness`) to a named engine
  descriptor under `harnesses`. A built-in `codex` harness runs **OpenAI Codex**
  (`codex exec`) with first-class logging parity (its `--json` stream is
  normalised into Archon's JSONL for the dashboard), per-invocation lean-lsp MCP
  wiring, and a Codex-specific prompt variant. Define your own from the
  `_my_harness_example` template. See [CONFIGURATION.md](CONFIGURATION.md).
  The harness router and Codex runner build on prior work by
  [@surenny](https://github.com/surenny) in
  [#25](https://github.com/frenzymath/Archon/pull/25).
- **Claude backends** (`--claude-backend` / `loop.claude_backend`): `default`
  (plain `claude -p`), `vscode` / `desktop` (entrypoint attribution), `claude-p`
  (drives the interactive TUI headlessly via the
  [claude-p](https://github.com/AxelDlv00/claude-p) fork — handy with a Claude
  subscription under Anthropic's Agent SDK plan policy), and `interactive`
  (human-driven foreground; serial). The backend propagates to subagents.
- **Prover modes.** Per-objective `[prover-mode: <name>]` tags select a prover
  playbook (`formalize`, `prove`, `fine-grained`, `polish`, `golf`,
  `mathlib-build`); each stage has a default mode. Replaces the static
  `prompts/prover-*.md`.
- **`archon extract` / `archon merge`.** DAG-driven extraction of a subproject (a
  dependency cone) and merging of two projects keeping the best shared proofs;
  declaration-level carve with a parent-regression gate.
- **Dashboard — DAG and Blueprint views.** An interactive dependency-graph view
  (status-coloured nodes, node inspector, git-history scrubber) and a typeset
  Blueprint view (chapters, `\leanok` / `\mathlibok` tags, source citations),
  interlinked with the Diffs view.
- **`archon blueprint-doctor`** lint (orphan chapters, broken/undefined
  `\ref` / `\uses` / macros, literal-REF placeholders, interleaved math
  delimiters, bare labels, axioms) — also run automatically before the plan phase.
- **Planner signals**: a loop-managed `AUTO_NOTES.md` feedback channel (separate
  from the user-authored `USER_HINTS.md`) and Lean↔blueprint coverage-debt
  injection.
- `archon-protected.yaml` v2: blueprint (`.tex`) protection alongside Lean,
  protection levels (signature/statement vs. all), `fnmatch` glob patterns, and a
  deterministic whole-file gate. `subagents.enabled` also accepts `"*"` to enable
  every installed subagent.
- **Per-file `/- USER: ... -/` Lean hints.** Inline comments in a `.lean` file are
  read by the prover as persistent, file-specific guidance; `archon discuss` can
  add them for you.
- **New CLI commands**: `archon dag`, `archon extract`, `archon merge`,
  `archon blueprint-doctor`, and `archon log` (inner-git commit graph). `archon
  migrate` groups one-off migrations for legacy projects.

### Changed

- **`.archon/CLAUDE.md` → `.archon/AGENTS.md`** — the cross-tool role doc loaded
  by both Claude Code and Codex; prompts are now harness-neutral.
  `archon init` / `update` performs the rename.
- **`sync_leanok`** compile-checks via `lake build <module>` (dependencies built,
  no spurious missing-import failures), run sequentially; timeout configurable
  (`loop.sync_leanok_timeout_sec`); optional `#print axioms` sweep
  (`loop.axiom_sweep`).
- The plan phase no longer exits on a COMPLETE plan while sorries remain — it
  resets to the prover stage and keeps going.
- Subagent dispatch is engine-agnostic (`python -m archon` entrypoint;
  PATH-independent CLI / codex / uv handles for the Codex login-shell sandbox).
- `config.json` surfaces the `harnesses` block near the top, with shorter help
  text and concrete examples; re-init refreshes help text and adds new keys
  without touching your values.
- Internal/agent CLI commands (`dag-query`, `subagent`, …) are hidden from
  `archon -h` (still runnable with `--help`).
- Hardened `PROGRESS.md` stage detection (tolerates annotations after the stage token).

### Fixed

- Dashboard binds IPv4 `0.0.0.0` under WSL2 so `localhost:PORT` is reachable from
  the Windows browser (was IPv6-only — `ERR_CONNECTION_REFUSED`).
- Hardcoded #!/bin/bash shebangs were replaced with `#!/usr/bin/env bash` for better cross-platform compatibility.

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
  [MULTILANE.md](MULTILANE.md) for setup. Multilane is opt-in via `.archon/config.json`; the default is a
  single Anthropic lane.
- **Refactor agent** + `archon refactor`: structural changes (wrong
  definitions, signature changes, file splits) are handled by a dedicated
  refactor agent that may edit any `.lean` file (subject to
  `archon-protected.yaml`). In the autonomous loop the plan agent dispatches the
  `refactor` subagent directly, passing the directive inline — nothing is staged
  in a file. For hands-on use, `archon refactor draft` interviews you and writes
  `.archon/REFACTOR_DIRECTIVE.md`, and `archon refactor run` then executes it.
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
- **Blueprint-doctor phase**: runs at the top of each iteration and
  reports orphan files, broken `\uses{}`, missing `\lean{...}` blocks, and
  other blueprint structural drift directly into the plan agent's prompt
  under `## Blueprint doctor — live structural findings`. No separate
  command needed.
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
  `merge` mode launches Claude Code to reconcile prompts and `AGENTS.md`
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

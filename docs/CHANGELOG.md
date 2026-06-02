# Changelog

All notable changes to Archon are documented here.

## [Unreleased]

### Added

- **Codex runner (Phase 2, opt-in)**: the harness router can now route a
  responsibility — in practice the `prover` — to OpenAI **Codex**
  (`codex exec`, e.g. gpt-5.5) instead of Claude Code. Add a
  `harnesses.<name>` block with `runner: "codex"` and point a role at it via
  `loop.roles.prover`; gateway creds go in `.archon/.env`. The new
  `CodexAgent` implements the same `AgentRunner` protocol as `ClaudeAgent`
  and **reuses the exact same subprocess supervision** (cancel-event,
  idle-timeout watchdog + retry, ordered teardown) via a shared
  `supervise_streamed_run` helper — so a hung codex provider auto-restarts
  just like a hung Kimi/DeepSeek lane does. The `codex exec` invocation
  mirrors the FormalQualBench bash runner flag-for-flag (`--json
  --skip-git-repo-check --ignore-user-config -m <model> -c
  model_reasoning_effort=… --sandbox <mode> --ephemeral` + a `-c`-injected
  custom gateway provider built from the descriptor's `base_url_env` /
  `key_env`); the API key is passed via `CODEX_GATEWAY_API_KEY` in the child
  env, never on the command line. The resolved `HarnessDescriptor` (a frozen,
  picklable dataclass extended with `effort` / `sandbox` / `prompt_variant` /
  `mcp` / `base_url_env` / `key_env` / `wire_api`) is now threaded — as the
  descriptor itself, not a bare name — into the prover process pool, subagents,
  and lanes, so a pool worker rebuilds a fully-configured codex runner with no
  config re-read. **Default behavior is unchanged**: an unconfigured project
  still short-circuits to exactly `ClaudeAgent(model=…, role=…)`.

  Codex now supports two opt-in capabilities the FormalQualBench harness
  already had:

  - **`archon-lean-lsp` MCP** — set `harnesses.<name>.mcp: "lean-lsp"` and the
    codex runner appends per-invocation `-c mcp_servers.archon-lean-lsp.*`
    overrides (the same server / `data_path("tools/lean-lsp-mcp")` dir the
    claude-code path registers at init, plus codex-specific `required=true` and
    `tool_timeout_sec=600`), with `LEAN_PROJECT_PATH` pointed at the lake root
    the prover runs in. Codex has no `--mcp-config` flag, so this is entirely
    self-contained in the runner's argv (it does not touch the init-time
    `claude mcp add` model). Fails closed if the bundled MCP dir can't be
    resolved or the bundle name is unknown. Unset ⇒ no MCP, identical to before.
  - **`prompt_variant`** — set `harnesses.<name>.prompt_variant: "codex"` and
    the runner appends a bundled codex-specific prompt tail (resolved
    local-overrides-bundled: `.archon/prompts/variants/codex.md` wins over the
    shipped copy, mirroring how Archon resolves its other prompts). The shipped
    `codex.md` tells the model to use codex-native tools (`apply_patch` /
    `exec_command` / `read_file`), to prefer the Lean LSP MCP tools for
    diagnostics when present, and reinforces faithfulness (no statement
    rewrites, no axioms/`sorry`, no metaprogramming to game the checker). A
    missing variant file warns and proceeds; unset ⇒ prompt unchanged.

  **Remaining limitations** (see [MIGRATION.md](MIGRATION.md)): the codex
  `--json` stream is logged **raw**, so the dashboard does not parse codex
  cost/session; no true session resume (a passed resume runs fresh with a
  warning); `run_interactive` is unsupported (interactive sites stay on
  claude-code). Because codex is more adversarial, gate codex proofs behind the
  comparator before trusting them. The `gemini` runner remains unimplemented and
  raises a clear error.

- **Harness router (Phase 1, opt-in seam)**: a "responsibility → harness"
  routing layer that lets plan / prover / review / each subagent / each lane
  be pointed at a named *harness* (an engine). This phase introduces only the
  decision point — a `build_runner(...)` factory behind a new `AgentRunner`
  protocol that `ClaudeAgent` now implements, plus per-role
  (`resolve_role_harness`) and per-subagent (`resolve_subagent_harness`)
  resolvers that mirror the existing `resolve_subagent_model` precedence
  (`loop.roles.<role>` / `subagents.<name>.harness` > `loop.harness` >
  built-in). Config is additive: an optional `harnesses.<name>` descriptor
  table and `loop.harness` / `loop.roles` keys. The only registered runner is
  still `claude-code`; an unknown harness raises a clear error rather than
  silently downgrading. **Default behavior is unchanged**: with no new config
  keys present, `build_runner` short-circuits to exactly
  `ClaudeAgent(model=…, role=…)`, so the default single-Anthropic path never
  touches the new resolution code. `SubagentDescriptor` gains an optional
  `harness:` frontmatter field and `LaneConfig` gains a forward-compat
  `harness` field (parsed but not yet rewired into lane dispatch). Non-Anthropic
  (codex / gemini) runners arrive in a later phase.

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

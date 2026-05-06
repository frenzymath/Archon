# Changelog

All notable changes to Archon are documented here.

## [0.2.0] — 2026-05

This release adds **multi-lane parallel proving**, a dedicated **refactor agent**,
**inner-git versioning** of agent work, the `archon discuss` / `branch` / `version`
commands, and a project-level frozen-signature surface (`archon-protected.yaml`).
The codebase has also been reorganised: large command modules now live as
packages (`archon.commands.loop`, `archon.commands.init`, …) so each phase, step,
or runner is a small focused class.

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
- **Refactor agent** + `archon refactor`: when the plan agent identifies
  structural issues (wrong definitions, signature changes, file splits), it
  writes a directive into `.archon/REFACTOR_DIRECTIVE.md`. The next loop
  iteration picks it up and runs a refactor agent that may edit any `.lean`
  file (subject to `archon-protected.yaml`). The plan agent then runs a
  post-refactor verification pass.
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

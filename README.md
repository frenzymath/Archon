# Archon

![Version](https://img.shields.io/badge/version-0.2.0-blue)
[![License](https://img.shields.io/badge/Apache-2.0-green)](./LICENSE)

> **Archon v0.2.0.** This version adds **multi-lane parallel proving** (Anthropic + Moonshot + DeepSeek side by side), a **refactor agent** driven by the plan agent, **inner-git versioning** of agent work, and a frozen-signature surface (`archon-protected.yaml`). The CLI gains `archon refactor`, `archon discuss`, `archon branch`, and `archon version`.
>
> **Upgrading from v0.1.0?** See [section 7 of MIGRATION.md](docs/MIGRATION.md#7-upgrading-from-v010-to-v020). **Upgrading from a pre-CLI checkout?** Start at section 1.
> 
> Full release notes: [CHANGELOG.md](docs/CHANGELOG.md).

Archon is an agentic system that autonomously formalizes research-level mathematics in Lean 4. A **plan agent** provides strategic guidance while **prover agents** write and verify proofs — separating analysis from execution to avoid context explosion. The system handles repository-scale formalization through three phases: scaffolding, proving, and polish. Built on Claude Code and Claude Opus 4.6, with a modified fork of [lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp) and [lean4-skills](https://github.com/cameronfreer/lean4-skills). Archon originated from orchestrating Claude Code with OpenClaw — see [Standard vs. orchestrator-scheduled mode](#standard-vs-orchestrator-scheduled-mode). See also our [blog](https://frenzymath.com/blog/archon-firstproof/) and [announcement](https://frenzymath.com/news/archon-firstproof/).

Archon is designed and optimized for **project-level formalization** — multi-file repositories with interdependent theorems, not isolated competition problems. As such, single-problem benchmarks are not a specific optimization target. For model choice, **Opus 4.6 is strongly recommended**; Sonnet also works well but is less capable. Other models have not been tested — weaker models may struggle with the complex skills and prompt structures, in which case Archon's system design could hurt performance rather than help it.

**Security note:** `archon loop` runs Claude Code with `--dangerously-skip-permissions --permission-mode bypassPermissions`, meaning the model can execute arbitrary shell commands, read/write any file the process can access, and make network requests — all without asking for confirmation. This is necessary for unattended operation but carries real risk: a misbehaving model could delete files, overwrite code, or run unintended commands. **While Opus 4.6 NEVER caused harm across all of our experiments,** the following measures can further reduce exposure:

- **Commit and push your project before running Archon, so any unintended changes can be easily reverted.**
- Run Archon under a **dedicated, low-privilege user** that only has access to the project directory
- Run inside a **Docker container** or VM with no access to sensitive data or credentials
- Avoid running as root or with access to production systems
- Review `.archon/proof-journal/` after each run to audit what the agents did

## Table of Contents

- [Install](#install)
- [Usage](#usage)
  - [1. Initialize a project](#1-initialize-a-project)
  - [2. Start the automated loop](#2-start-the-automated-loop)
    - [The plan agent's expanded role](#the-plan-agents-expanded-role)
    - [Per-project config: `.archon/config.json` and `.archon/.env`](#per-project-config-archonconfigjson-and-archonenv)
    - [Multi-lane proving (optional)](#multi-lane-proving-optional)
    - [Frozen signatures: `archon-protected.yaml`](#frozen-signatures-archon-protectedyaml)
  - [Guiding agents](#guiding-agents)
  - [Monitoring progress](#monitoring-progress)
  - [Starting the dashboard manually](#starting-the-dashboard-manually)
  - [Existing lean4-skills and lean-lsp MCP installations](#existing-lean4-skills-and-lean-lsp-mcp-installations)
  - [CLI options for `archon loop`](#cli-options-for-archon-loop)
- [Supplying informal material](#supplying-informal-material)
- [Standard vs. orchestrator-scheduled mode](#standard-vs-orchestrator-scheduled-mode)
  - [How to use orchestrator-scheduled mode](#how-to-use-orchestrator-scheduled-mode)
  - [What changes compared to the standard loop](#what-changes-compared-to-the-standard-loop)
  - [Why orchestrator-scheduled mode is more effective](#why-orchestrator-scheduled-mode-is-more-effective)

## Install

> **Note:** `archon loop` runs Claude Code with `--dangerously-skip-permissions`, which Claude Code refuses when running as root on Linux. Two workarounds:
> 1. **Use a non-root account** (RECOMMENDED) — e.g. create one with `adduser` — so you are not running with excessive root privileges.
> 2. **Set `export IS_SANDBOX=1`** so Claude Code is allowed to start with this high-risk option.

To install the CLI tools and system dependencies, run the following command in your terminal:
> Note: It is recommended, but not required, to run inside a Python virtual environment like `conda`. Most recent versions of Python should work, for instance `conda create -n archon python=3.11` works well. 

```bash
curl -sSL https://raw.githubusercontent.com/frenzymath/Archon/refs/heads/main/install.sh | bash
```

If prefer manual installation, [`install.sh`](./install.sh) fetches the repository, runs `pip install .`, and executes `archon setup` to install system-level dependencies (uv, Claude Code, ...) and verify your Lean toolchain. *The installation process might be slow the first time.*

You should now be able to verify the installation and be guided on its usage with:

```bash
archon -h
```

To update an existing install later:

```bash
archon update
```

`archon setup` also checks for API keys used by the informal agent (`OPENAI_API_KEY`, `GEMINI_API_KEY`, or `OPENROUTER_API_KEY`) — at least one is recommended but not required. The API keys can also be set in `.archon/.env` at the project level. 

> The bundled informal agent is a simplified demonstration: a single API call
> to an external model for proof sketches. Our internal implementation is more
> involved but not yet ready for open-sourcing. In practice the one-shot
> approach does not show an obvious performance drop, likely because Claude
> Code performs its own verification and refinement on the returned sketches.

### CLI overview

| Command | Description |
|---------|-------------|
| `archon init` | Initialize a new Archon project (or reconcile an existing one). |
| `archon loop` | Run the automated plan → (refactor) → prove → review loop. |
| `archon dashboard` | Start the web monitoring interface (auto-launched by `loop` by default). |
| `archon doctor` | Verify the full Archon setup and health. |
| `archon prove` | Directly prove an inline statement. |
| `archon refactor` | Run only the refactor agent against the current `REFACTOR_DIRECTIVE.md`. |
| `archon discuss` | Open Claude Code interactively in the project with full Archon context — for debugging or brainstorming without firing the loop. |
| `archon branch` | Create a branch in the inner git (`.archon/git-dir/`) from any historical agent commit. |
| `archon version` | Show the Archon CLI version and, inside a project, the project version. |
| `archon setup` | Install required system dependencies. |
| `archon update` | Update Archon to the latest published version. |

Run `archon --help` or `archon <command> --help` for details.

## Usage

### 1. Initialize a project

The project path must point to the directory containing your `lakefile.lean` or `lakefile.toml` — this is what defines a Lean project. Otherwise, it can contain informal content (papers, blueprints, notes) that Archon will use to initialize the project structure and write the first objectives.

To initialize:

```bash
archon init /path/to/your-lean-project
```

If no path is given, `init` prompts you for a project name and creates it.

`archon init` does the following inside your project:
- Creates `.archon/` with runtime state files and a **copy** of Archon's prompts
  (previous versions symlinked — see [MIGRATION.md](docs/MIGRATION.md))
- Installs Archon's lean4 skills as the `lean4@archon-local` plugin at project scope
- Copies the informal agent into `.claude/tools/archon-informal-agent.py`
- Installs Archon's lean-lsp MCP server as `archon-lean-lsp` at project scope
- Detects and disables any conflicting global `lean4-skills` / `lean-lsp` MCP
  (see [Existing lean4-skills and lean-lsp MCP installations](#existing-lean4-skills-and-lean-lsp-mcp-installations))
- Launches Claude Code interactively to detect project state, set up
  lakefile/Mathlib if needed, and write initial objectives

If the project has already been initialized, it might use a different version of Archon or you might have modified the prompts/skills manually, `init` offers four choices:

- **keep** — leave your existing setup alone; refresh MCP/plugin registrations only
- **merge** (recommended) — launch Claude Code in a focused diff session and reconcile each prompt / `CLAUDE.md` file interactively
- **overwrite** — replace all Archon files with the bundled versions (discards local edits)
- **abort** — cancel without changes

Init automatically runs `/archon-lean4:doctor` at the end to verify the full setup (Lean environment, MCP, skills, state files). See [MIGRATION.md](docs/MIGRATION.md) for details on upgrading an older project.

### 2. Start the automated loop

```bash
archon loop /path/to/your-lean-project
```

The loop alternates plan and prover agents through stages, with an optional refactor pass between plan and prove:

| Stage | What happens |
|-------|-------------|
| `autoformalize` | Scaffolding — translate informal math into Lean declarations with `sorry` |
| `prover` | Proving — fill `sorry` placeholders with verified proofs |
| `polish` | Verification and polish — golf, refactor, extract reusable lemmas |

Each iteration runs `plan → (refactor if requested) → prover(s) → review → finalize`. The refactor phase is conditional: it fires only when the plan agent writes a directive to `.archon/REFACTOR_DIRECTIVE.md`, runs at most once per iteration, and is followed by a verification pass. Every phase commits its output to an inner git at `.archon/git-dir/` as `archon[NNN/phase]: …`, so the dashboard's git tree shows the per-phase history independently of your project's outer git. Use `archon branch` to fork a branch from any historical agent commit if a run goes sideways.

#### The plan agent's expanded role

In v0.2.0 the **plan agent is the strategic centre of the loop**, not just a per-iteration scheduler. Its responsibilities now include:

- **Long-arc strategy** — maintains `.archon/STRATEGY.md`, the living plan from the project's current state to its end-state. Each iteration is grounded in this arc instead of optimising locally; only the plan agent reads or writes this file.
- **Blueprint authorship** — writes the informal proof sketches in `blueprint/src/chapters/<slug>.tex` (one chapter per Lean file) and registers `\lean{…}` hints. The prover reads its assigned chapter as the source of truth for the math; the review agent later updates `\leanok` / `\mathlibok` markers based on verified compilation.
- **Refactor triggering** — when current objectives can't be reached without structural changes (wrong definitions, missing infrastructure, signature drift), the plan agent writes `.archon/REFACTOR_DIRECTIVE.md`. The next loop body runs the refactor agent against that directive; the plan agent then verifies the result before assigning the next prover round.
- **Independent verification** — never trusts prover self-reports. After each prover round it independently checks sorry counts, compilation, and axiom usage before promoting results into `task_done.md`.
- **Dependency-graph-aware scheduling** — calls the bundled `dependency_graph.py` script every iteration so objective ordering reflects the real `.lean` import graph and blueprint `\uses{…}` edges, instead of being reconstructed by hand.

By default, `archon loop` **also launches the web dashboard** (see [Web Dashboard](#monitoring-progress)) in the background on a free port in the range 8080–8099 and prints the URL. The dashboard keeps running after the loop finishes so you can review results; stop it with Ctrl-C or by closing the terminal. Disable it with `--no-dashboard`, or open a browser automatically with `--open`.

#### Per-project config: `.archon/config.json` and `.archon/.env`

`archon init` creates two files for per-project configuration. Both are written once and then yours to edit:

- **`.archon/config.json`** — loop and multilane settings (max iterations, parallelism, multilane lane list, base ref, …). It is committed to the inner git so it travels with your project. The file ships self-documenting with a `multilane._examples` block; copy entries from there into `multilane.lanes` to enable a provider.
- **`.archon/.env`** — API keys for the informal agent (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`) and for multilane providers (`MOONSHOT_API_KEY`, `DEEPSEEK_API_KEY`, …). **Gitignored** — never committed. You can also still set these in your shell environment; the `.env` file just provides a stable per-project home.

Most users won't need to touch either file unless they're enabling multi-lane or wiring up a different informal-agent provider.

#### Multi-lane proving (optional)

By default `archon loop` runs a single Anthropic lane, and the rest of this README assumes that. v0.2.0 adds **multi-lane** proving: parallel prover lanes that run different LLM providers (Anthropic, Moonshot/Kimi, DeepSeek) on the same Lean files in isolated worktrees under `.archon/lanes/<lane>/`. The first lane to finish a file cleanly wins; other lanes get a 10-minute grace period, are then cancelled, and a per-file merge agent picks the best proof per declaration across whichever lanes did finish. To enable it, edit `.archon/config.json` (`multilane.enabled: true` plus a `lanes` list) and put provider keys in `.archon/.env`. See [MULTILANE.md](src/archon/.archon-src/archon-template/MULTILANE.md) for the full setup.

#### Frozen signatures: `archon-protected.yaml`

Add an `archon-protected.yaml` at the project root to declare signatures that the mathematician owns. Listed declarations cannot be renamed or re-signed by any agent — the refactor agent may only move them between files (and update the file path key). The file is committed to your project git so the whole team shares the protected surface. `archon init` writes an empty template; fill it in when you are ready.

**NOTE:** The prover agent is instructed to push formalization as far as possible, so the first few runs typically take **several hours** as it clears all low-hanging fruits. Once only genuinely difficult sorrys remain, each iteration becomes much shorter. To confirm the agent is running, watch the dashboard or tail `.archon/logs/iter-*/provers/*.jsonl`; the agent also writes Lean files while running, which you can see directly.

The loop exits automatically when the stage reaches `COMPLETE`. You can run `archon loop` on multiple projects in parallel from separate terminals — each project's state is independent.

### Guiding agents

Archon runs fully autonomously, but guiding it with your expertise will speed it up, align it with your preferred proof style, and help it overcome mathematical and Lean challenges.

There are three ways to influence Archon's behavior. Each serves a different purpose:

| Mechanism | When to use | Lifetime | Who reads it |
|-----------|-------------|----------|-------------|
| **USER_HINTS.md** | Mid-run course corrections | One-shot — cleared after each plan cycle | Plan agent |
| **/- USER: ... -/ comments** | File-specific proof guidance | Persistent — stays in the `.lean` file | Prover agent |
| **Prompts and skills** | Change how agents think and operate | Permanent — applies every iteration | All agents |

**USER_HINTS.md** — for things that change between iterations. Examples: "prioritize theorem X next", "stop trying approach Y, it's a dead end". The plan agent reads this once, acts on it, and clears the file. Don't put permanent instructions here — they'll be lost.

**/- USER: ... -/ comments** — for proof-level guidance tied to a specific `.lean` file. Examples: "try using Finset.sum_comm here", "this sorry depends on the helper lemma above". These persist in the source file and are visible to whichever prover agent owns that file.

**Prompts and skills** — for changing how agents behave across all iterations. Edit prompts when you want to change the plan agent's strategy, the prover's proof style, or the review agent's analysis. Create or extend skills for reusable workflows in specific situations. For a deeper treatment — including which changes are short-lived vs. permanent, how skills and prompts differ, the recommended order of adjustments, and how to evolve them as you encounter recurring issues — see [Section 5 (Skills and Prompts) in ORCHESTRATOR_GUIDE.md](ORCHESTRATOR_GUIDE.md#5-skills-and-prompts).

Archon has two layers — local overrides global:

| Layer | Location | Scope |
|-------|----------|-------|
| **Global** | Bundled inside the installed `archon` package | All projects (template source) |
| **Local** | `<project>/.archon/prompts/*.md` | One project only |

Starting with v0.1.0, local prompts are **copies** of the bundled templates rather than symlinks. This means:

- You can edit `<project>/.archon/prompts/*.md` freely without affecting other projects.
- Template updates do **not** automatically propagate. To pull in newer versions of the bundled prompts, run `archon init` again in that project and choose **merge** (recommended) or **overwrite**.

If you're coming from a version that used symlinks, see [MIGRATION.md](docs/MIGRATION.md) for the one-time migration flow.

### Customizing skills

Archon ships with a modified fork of [lean4-skills](https://github.com/cameronfreer/lean4-skills), installed as `lean4@archon-local` (providing `/archon-lean4:prove`, `/archon-lean4:doctor`, etc.). Skills are sourced from the installed `archon` package and registered with Claude Code as a local plugin marketplace.

**Modifying global skills**: Edit files under the installed package's
`skills/lean4/` directory (the path might look like `/site-packages/archon/skills/lean4/`). `archon init` re-registers the marketplace at the correct path on each run, so your edits take effect after re-init.

**Adding new global skills**: Create a new directory under the bundled
`skills/<your-skill-name>/` with a `SKILL.md` or `.claude-plugin/plugin.json` inside, and add it to `skills/.claude-plugin/marketplace.json`. Run `archon init` again on your project to pick up the new skill.

**We encourage you to customize.** If you notice the prover repeatedly making the same mistakes, or a proof strategy that consistently works for your project, codify it — add a skill or adjust a prompt. Archon improves as its skills and prompts accumulate lessons from your specific formalization work.

**Adding local skills**: Place them in `<project>/.claude/skills/<your-skill-name>/SKILL.md`. They are discovered by Claude Code automatically and won't conflict with Archon's `/archon-lean4:*` commands. No re-init needed.

### Monitoring progress

To check how the formalization is going, the easiest starting point is the **dashboard** (auto-launched by `archon loop` — visit the URL printed in the terminal, e.g. `http://localhost:8080`). It shows iteration progress, parallel prover status, a file-centric Diffs view backed by recorded code snapshots, agent logs with live streaming, and proof journal milestones.

<p align="center">
<img src="docs/dashboard-logs.jpg" alt="Archon Dashboard — Logs view" width="800">
</p>

The **Logs** view groups logs by iteration with phase timing (plan → prover → review) and per-prover completion status.

The **Journal** view tracks proof milestones across sessions — see which theorems were solved, blocked, or retried, with condensed reasoning traces that let you follow how the agents approached each proof.

<p align="center">
<img src="docs/dashboard-journal.jpg" alt="Archon Dashboard — Journal view" width="800">
</p>

The **Graph** view renders the proof dependency graph interactively so you
can see which theorems block which, and the **Diffs** view replays
per-iteration code snapshots — including a live fallback that reads the
working tree when an iteration is mid-flight and no snapshot has been
captured yet (v0.2.0). When multi-lane is enabled, lane-specific logs and
the per-file merge agent's output show up alongside the single-lane view.

You can also inspect state files directly:

- **`.archon/logs/iter-<N>/**/*.jsonl`** — running log of agent activity. The latest iteration's files tell you whether agents are still working.
- **`.archon/PROJECT_STATUS.md`** — overall progress: total sorries, what's solved, what's blocked, and reusable proof patterns.
- **`.archon/proof-journal/sessions/session_N/summary.md`** — detailed record of a specific iteration: what was attempted, what succeeded, what failed, and why.

These are updated automatically by the review agent after each iteration.

### Starting the dashboard manually

If you disabled the auto-launched dashboard, or want to look at a project after the loop has finished and the terminal is gone:

```bash
archon dashboard /path/to/your-lean-project
```

### Existing lean4-skills and lean-lsp MCP installations

If you already have `lean4-skills` or `lean-lsp` MCP installed globally, `archon init` detects them and disables them **for this project only** — so only Archon's modified versions are active. Your global installations are untouched and continue working in all other projects.

To restore the originals in an Archon project:
```bash
cd /path/to/your-project
claude plugin enable lean4-skills --scope project     # re-enable standard skills
claude mcp add lean-lsp -s project -- uvx lean-lsp-mcp  # re-enable standard MCP
```

### CLI options for `archon loop`

| Flag | Description |
|------|-------------|
| `--max-iterations N` / `-m N` | Max plan → (refactor) → prover → review cycles (default: 10). Exits early if stage reaches `COMPLETE`. |
| `--max-parallel N` | Max concurrent provers in parallel mode (default: 8). |
| `--stage STAGE` / `-s STAGE` | Force a stage (`autoformalize`, `prover`, `polish`) instead of reading from PROGRESS.md. |
| `--serial` | One prover at a time instead of parallel (one per file). |
| `--verbose-logs` | Save raw Claude stream events to `.raw.jsonl` for debugging. |
| `--no-review` | Skip review phase. Saves time/cost; plan agent still works without it. |
| `--no-refactor` | Skip the refactor phase even if the plan agent wrote a directive. |
| `--no-finalize` | Skip end-of-iteration commit / lake build / blueprint web rebuild. |
| `--from PHASE` | Resume the first iteration at `plan` / `refactor` / `prover` / `review` instead of restarting it. Subsequent iterations run the full sequence. |
| `--no-dashboard` | Do not auto-start the web dashboard. |
| `--open` | Open the dashboard in a browser as soon as it starts. |
| `--dry-run` | Print prompts without launching Claude. |

Multi-lane proving is configured exclusively via `.archon/config.json` (no CLI flag) — see [MULTILANE.md](src/archon/.archon-src/archon-template/MULTILANE.md).

## Supplying informal material

Formalization quality improves materially when the agents have access to the original informal mathematics. Supply as much source material as you can — place files in the repository root or a clearly documented top-level folder (e.g. `references/`):

1. **Papers and manuscripts** — the primary text being formalized (PDF, LaTeX source, or both). This is the single most important input after the Lean project itself.
2. **Blueprints** — if you have a [LeanBlueprint](https://github.com/PatrickMassot/leanblueprint) or similar dependency graph, include it. Blueprints give the agents a clear picture of the logical structure and what depends on what.
3. **Key definitions and lemma references** — for important definitions or lemmas, note where they first appear (e.g. "Definition 3.2 in [Author, Year]" or "Lemma 2 of arXiv:XXXX.XXXXX"). If the main paper cites important theorems whose proofs appear elsewhere, include those papers too — either add them yourself or ask Claude Code to fetch them. This helps the agents choose correct formalizations and find existing Mathlib content instead of reinventing it.

Even rough or incomplete material is valuable — partial references are far better than none. The more context the agents have, the better they can disambiguate notation, pick appropriate Mathlib abstractions, and produce proofs that match the mathematical intent.

## Standard vs. orchestrator-scheduled mode

`archon loop` is the **standard mode** — a fixed plan→prover→review loop that runs unattended. It is sufficient for most formalization tasks.

In our experiments, replacing the fixed loop with an **orchestrator-scheduled mode** — where an outer orchestrator like OpenClaw drives Claude Code directly — yielded stronger results. Instead of following a rigid pipeline, the orchestrator can freely choose when to plan, prove, or review based on the current state, and can supervise the model continuously to prevent premature termination.

### How to use orchestrator-scheduled mode

Ensure your orchestrator has access to the project directory, and ask it to read README.md for an overview of the project.

We provide [`ORCHESTRATOR_GUIDE.md`](src/archon/ORCHESTRATOR_GUIDE.md) as a companion guide for your orchestrator. It was authored by our own OpenClaw based on its accumulated experience orchestrating Claude Code across multiple formalization projects. The guide covers how to read Archon's state files, decide which stage to run next, compose prompts from `.archon/prompts/`, and invoke `claude -p` — including prompt composition, adaptive scheduling logic, failure recovery, and operational rules learned from production use.

### What changes compared to the standard loop

In standard mode, `archon loop` enforces a fixed cycle: plan→prover→review, repeated up to `--max-iterations`. The orchestrator-scheduled mode differs in several ways:

- **Environment management** — the orchestrator handles setup and debugging: installing dependencies, resolving Mathlib cache issues, verifying that skills and MCP work correctly. These tasks often require back-and-forth troubleshooting that a fixed script cannot do.
- **Flexible phase ordering** — the orchestrator decides when to plan, prove, or review based on what it observes, rather than following a fixed sequence. It might skip planning when the current objectives are still valid.
- **Real-time intervention** — the orchestrator can step in the moment the model is stuck. It detects surrender patterns (e.g., "Mathlib lacks infrastructure") and pushes the prover back in with refined hints or alternative strategies.
- **Richer cross-session context** — the orchestrator has its own memory. It can retain whatever state matters for adaptive routing — failure histories, proof patterns, mathematical context — accumulating richer context over time than a script that only persists a few markdown artifacts between iterations.

### Why orchestrator-scheduled mode is more effective

**Flexibility** — the orchestrator decides when to plan, prove, or review based on current state rather than following a fixed sequence, making it adaptable to a wider range of formalization tasks.

**Stability** — a supervisor layer catches errors that a fixed loop cannot: crashed sessions, malformed state files, stuck provers, or plan agents that set unreasonable objectives. The orchestrator acts as a safety net that keeps the process running correctly over hours or days without manual intervention.

**Evolvability** - by design, orchestrators like OpenClaw can author and refine skills and prompts over time. The global/local skill and prompt slots are designed not only for human experts but also for orchestrators: they can analyze failure modes and update skills or prompts accordingly (with your permission), making the system progressively more powerful.
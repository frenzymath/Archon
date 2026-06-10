# Archon

![Version](https://img.shields.io/badge/version-0.3.0-blue)
[![License](https://img.shields.io/badge/Apache-2.0-green)](./LICENSE)
[![claude-p](https://img.shields.io/badge/driver-claude--p-8A2BE2?logo=github)](https://github.com/AxelDlv00/claude-p)
[![leandag](https://img.shields.io/badge/graph-leandag-1f6feb?logo=github)](https://github.com/AxelDlv00/LeanDAG)

> **Archon v0.3.0.** Adds more harnesses (Currently : Codex, Claude Code). Adds solutions to [Anthropics' new rate limits on `claude -p`](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan) (Codex, Headless Claude TUI, etc). Archon grounds its work in a DAG using [LeanDag](https://github.com/AxelDlv00/LeanDAG), a custom API to query the Lean blueprint graph. The UI is more powerful (blueprints are rendered in Archon's dashboard, the DAG is navigable, etc). The provers can be launched with different modes (fine-grained, mathlib-build, etc). Subprojects can be extracted from the DAG with `archon extract` (to work on them separately), and merged back with `archon merge`. 

> **Archon v0.2.0.** Adds **multi-lane parallel proving** (Anthropic + Moonshot + DeepSeek side by side), **inner-git versioning** of agent work, a frozen-signature surface (`archon-protected.yaml`), an **opt-in subagent system** (blueprint review, strategy critique, Mathlib design advice, and more), etc.

> **Upgrading from v0.2.0?** Just run `archon update` and `archon init` in existing projects. 
>
> **Upgrading from v0.1.0 or before?** See [MIGRATION.md](docs/MIGRATION.md).
>
> Full release notes: [CHANGELOG.md](docs/CHANGELOG.md).

Archon is an agentic system that autonomously formalizes research-level mathematics in Lean 4. A **plan agent** provides strategic guidance while **prover agents** write and verify proofs — separating analysis from execution to avoid context explosion. The system handles repository-scale formalization through three phases: scaffolding, proving, and polish. By default, built on Claude Code and Claude Opus 4.8, with a modified fork of [lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp) and [lean4-skills](https://github.com/cameronfreer/lean4-skills). Archon originated from orchestrating Claude Code with OpenClaw. See also our [blog](https://frenzymath.com/blog/archon-firstproof/) and [announcement](https://frenzymath.com/news/archon-firstproof/).

Archon is designed and optimized for **project-level formalization** — multi-file repositories with interdependent theorems, not isolated competition problems. As such, single-problem benchmarks are not a specific optimization target. For model choice, **Opus 4.8 is strongly recommended**; Sonnet also works well but is less capable. Other models have not been tested — weaker models may struggle with the complex skills and prompt structures, in which case Archon's system design could hurt performance rather than help it. Since **v0.3.0**, codex is also available, gpt-5.5 can be a good alternative to Opus 4.8. Other models can be used through Claude Code harness (e.g., Deepseek and Kimi by using their Anthropic-compatible APIs, or any model compatible with OpenRouter since the later handles the API translation). 

## Table of Contents

- [Install](#install)
- [Usage](#usage)
  - [1. Initialize a project](#1-initialize-a-project)
  - [2. Configure your preferences in `config.json`](#2-configure-your-preferences-in-configjson)
  - [3. Write blueprints to constitute a consistent DAG](#3-write-blueprints-to-constitute-a-consistent-dag)
  - [4. Start the automated loop](#4-start-the-automated-loop)
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

> **Security note:** `archon loop` runs Claude Code with `--dangerously-skip-permissions`, meaning the model can execute arbitrary shell commands, read/write any file the process can access, and make network requests, which Claude Code refuses when running as root on Linux. In our experiment, Opus or Sonnet never caused harm, but since there is still a risk, we recommend one of the following workarounds: 
> 1. **Use a dedicated non-root user** (RECOMMENDED) — e.g. create one with `adduser` — so you are not running with excessive root privileges.
> 2. **Set `export IS_SANDBOX=1`** so Claude Code is allowed to start with this high-risk option.
> 3. **Run inside a Docker container** or VM with no access to sensitive data or credentials

> Note: It is recommended, but not required, to run inside a Python virtual environment (e.g., with `python=3.11`).

To install the CLI tools and system dependencies, run the following command in your terminal:

```bash
curl -sSL https://raw.githubusercontent.com/frenzymath/Archon/refs/heads/main/install.sh | bash
```

You can also install manually if you prefer. [`install.sh`](./install.sh) fetches the repository, runs `pip install .`, and executes `archon setup` to install system-level dependencies (uv, Claude Code, ...) and verify your Lean toolchain. *The installation process might be slow the first time.*

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
| `archon init` | Initialize a new Archon project. |
| `archon dag` | Elaborate a full informal blueprint (dependency graph) for the project. |
| `archon blueprint-doctor` | Lint blueprint structure, references, macros, axioms, and coverage annotations. |
| `archon extract` | Extract a dependency-cone subproject from an Archon project. |
| `archon merge` | Merge two Archon projects via the DAG, keeping the best shared proofs. |
| `archon loop` | Run the automated plan → prove → review loop. |
| `archon doctor` | Verify the full Archon setup. |
| `archon dashboard` | Start the web monitoring interface. |
| `archon prove` | Launch a proof loop for a given statement. |
| `archon setup` | Install system-level dependencies. |
| `archon update` | Update Archon to the latest version. |
| `archon discuss` | Start an interactive discussion about the project. |
| `archon branch` | Switch to an existing inner-git branch, or fork a new one. |
| `archon log` | Show the inner-git commit graph. |
| `archon version` | Show the Archon CLI version and, inside a project, the project version. |
| `archon refactor` | Draft or run a refactor directive. |
| `archon migrate` | Run one-off migrations for legacy Archon projects. |

Run `archon -h` or `archon <command> -h` for details.

## Usage

### 1. Initialize a project

To initialize:

```bash
archon init /path/to/your-lean-project
```

Here `/path/to/your-lean-project` can either:
- Be empty, in which case Archon creates a new project directory and will ask you what you want to formalize. 
- Contain an existing Lean project, in which case Archon will use it as the basis for formalization.
- Contain informal material (e.g. description of the problem, papers, blueprints, ...) in which case Archon will create the Lean project structure inside it and use the informal material to write the first objectives.

`archon init` does the following inside your project:
- Creates `.archon/` with runtime state files and a **copy** of Archon's prompts, copies the python scripts that agents can use
- Installs Archon's lean4 skills as the `lean4@archon-local` plugin at project scope
- Installs Archon's lean-lsp MCP server as `archon-lean-lsp` at project scope. Detects and disables any conflicting global `lean4-skills` / `lean-lsp` MCP
- Configures `leanblueprint`, `git`, `lake`, and installs the lastest version of mathlib if there are none yet
- Launches Claude Code interactively to detect project state and write initial objectives

If the project has already been initialized, `init` should be re-run after updating Archon to pull in the latest bundled prompts and skills, it will preserve your local edits and config, and propose you to keep, merge or overwrite prompts with the new versions.

### 2. Configure your preferences

#### `.archon/config.json` and `.archon/.env`

Since `v0.2.0`, `.archon/config.json` lets you store project preferences so you do not have to repeat the same CLI options every time. It is the recommended place to configure harnesses and models, Claude launch backends, enabled subagents, multi-lane proving, and loop defaults.

See [CONFIGURATION.md](docs/CONFIGURATION.md) for the full reference. As a quick overview, a typical `.archon/config.json` looks like this:

```jsonc
{
  "loop": {
    "max_iterations": 2,
    "parallel": true,
    "max_parallel": 4,

    // Use another launch backend than plain `claude -p`, whose billing became limited (e.g. "claude-p", "vscode", "desktop", "interactive").
    "claude_backend": "claude-p",

    // Default engine for every role and subagent
    "harness": "codex", // Claude Code is default 
    "model": "gpt-5.5",

    // Override a specific role (plan / prover / review).
    "roles": { "plan": "claude-code", "prover": "codex" }
  },

  // Opt into focused helper agents. List names, or use "*" to enable every
  // installed subagent (recommended for best results).
  "subagents": {
    "enabled": ["strategy-critic", "blueprint-reviewer", "lean-auditor"]
    // "enabled": "*"   // ← shortcut: turn them all on
  }
}
```

#### `./archon-protected.yaml`

Depending on the project, you might want to avoid Archon overwriting some of your contributions. Therefore `archon-protected.yaml` lets you declare files or declarations that Archon should not modify (e.g. not touch the body, not touch the signature, ...) whether in the blueprints or in the Lean files.

Since `v0.3.0` the file supports three kinds of rule, all optional — you mix and match what your project needs. Patterns are `fnmatch` globs (`*`, `?`, `[...]`), so a single rule can cover many files or declarations at once:

```yaml
# 1. Protect Lean declarations, per file.
lean:
  MyProject/Core.lean:
    - some_lemma                 # freeze the SIGNATURE
    - name: key_definition
      protect: all               # freeze the WHOLE declaration
    - name: MyProject.Internal.* 

# 2. Protect blueprint (.tex) content.
blueprint:
  - file: blueprint/src/chapters/Intro_*.tex  # whole chapter(s) read-only
  - label: thm:main              # freeze the STATEMENT, the proof may still change
  - label: lem:key*
    protect: all                 # freeze STATEMENT and proof

# 3. Protect arbitrary files by path glob (notes, scripts, data, ...).
files:
  - references/*
  - notes/*.md
```

### 3. Write blueprints to constitute a consistent DAG

Since `v0.3.0`, `archon dag` launches a blueprint-writing loop. This loop can be ran before the main loop or in the middle. It uses [`LeanDag`](https://github.com/AxelDlv00/LeanDAG) to avoid only relying on the LLM's internal representation of the dependencies. 

```bash
archon dag /path/to/your-lean-project
```

This step is optional but recommended, we recommend running it at least once before the main loop, so that the blueprint is in place before the agents start writing Lean code. 

### 4. Start the automated loop

The main work of Archon is here, `archon loop` alternates plan/prover/review agents through iterations, with optional subagents (see [Subagents (optional but highly recommended)](#subagents-optional)), to formalize the project in Lean. 

```bash
archon loop /path/to/your-lean-project
```

The loop's high-lever flow is `autoformalize` → `prover` → `polish` → `COMPLETE`, the longest phase is `prover`:

| Stage | What happens |
|-------|-------------|
| `autoformalize` | Scaffolding — translate informal math into Lean declarations with `sorry` |
| `prover` | Proving — fill `sorry` placeholders with verified proofs |
| `polish` | Verification and polish — golf, refactor, extract reusable lemmas |

Every phase commits its output to an inner git at `.archon/git-dir/` as `archon[NNN/phase]: …`, so the dashboard's git tree shows the per-phase history independently of your project's outer git. Use `archon branch` to fork a branch from any historical agent commit if a run goes sideways. Using `.archon/git-dir/` allows you to work with your own `.git` without interference with Archon's versioning of its work.

By default, `archon loop` **also launches the web dashboard** (see [Web Dashboard](#monitoring-progress)) in the background on a free port in the range 8080–8099 and prints the URL.

#### Subagents (optional but highly recommended)

For backward compatibility purposes, **we chose to disable subagents by default**, everything will work without them. Nevertheless, our experiments show that they significantly improve performance and reliability of Archon.

What are these subagents? The planner and reviewer can dispatch them in parallel, they have a focused scope and a fresh context, instead of being overwhelmed by the full project context and hundreds lines of intructions (like the plan and review agents). Some of these subagents, whose work is mechanical, can run with Sonnet instead of Opus (by editing `config.json`) without performance drop.

Another advantage of subagents is that you can easily add yours, specific to your project, all it requires is writing a `.md` file with YAML frontmatter in the `subagents/` directory, and then listing its name in `config.json`.

You can include all of our subagents with:

```json
"loop" : {
  "subagents": { "enabled": "*" }
  // Or 
  "subagents": { "enabled": "enabled": [
      "blueprint-reviewer",
      "strategy-critic",
      "progress-critic",
      "lean-auditor",
      "lean-vs-blueprint-checker",
      "mathlib-analogist",
      "refactor",
      "blueprint-writer",
      "reference-retriever",
      "blueprint-clean",
      "lean-scaffolder",
      "strategy-auditor",
      "dag-walker",
      "effort-breaker"
  ] },
  "strategy-critic": "opus",
  "refactor": "sonnet",
  // ... 
}
```

#### Multi-lane proving (optional)

By default `archon loop` runs a single Anthropic lane. v0.2.0 adds **multi-lane** proving: parallel prover lanes that run different LLM providers (Anthropic, Moonshot/Kimi, DeepSeek) on the same Lean files in isolated worktrees under `.archon/lanes/<lane>/`. The first lane to finish a file cleanly wins; other lanes get a 10-minute grace period, are then cancelled, and a per-file merge agent picks the best proof per declaration across whichever lanes did finish. To enable it, edit `.archon/config.json` (`multilane.enabled: true` plus a `lanes` list) and put provider keys in `.archon/.env`. See [MULTILANE.md](src/archon/.archon-src/archon-template/MULTILANE.md) for the full setup.

### The human's role? 

Archon is designed to run fully autonomously, it is able to find alternative proof strategies when it gets stuck, and it has good abilities to self-critique and find its own mistakes. However, guiding it with your expertise will speed it up, align it with your preferred proof style, and help it overcome mathematical and Lean challenges.

First, there are several means to understand Archon's work:
- `archon discuss` launches an interactive terminal discussion with Archon, so that you can directly communicate with it. Moreover, Archon will propose you to add guidance for the next iteration using `USER_HINTS.md`. 
-  The dasboard allows you to monitor the agents' work, summaries are made at the end of each step. Archon can also flag information to user using `TO_USER.md` which is shown in a red banner in the dashboard (e.g. environment setup issue, bug in the code, asking for reference that it cannot retrieve, critical change in the strategy, etc). 


There are three ways to influence Archon's behavior. Each serves a different purpose:

| Mechanism | When to use | Lifetime | Who reads it |
|-----------|-------------|----------|-------------|
| **USER_HINTS.md** | This is the easiest way to provide guidance | You can choose between short-lived and permanent hints | Plan agent |
| **/- USER: ... -/ comments** | File-specific proof guidance | Persistent — stays in the `.lean` file | Prover agent |
| **Prompts, skills, custom subagents** | Change how agents think and operate | Permanent — applies every iteration | All agents |

### Customizing skills

Archon ships with a modified fork of [lean4-skills](https://github.com/cameronfreer/lean4-skills), installed as `lean4@archon-local` (providing `/archon-lean4:prove`, `/archon-lean4:doctor`, etc.). Skills are sourced from the installed `archon` package and registered with Claude Code as a local plugin marketplace.

**Modifying global skills**: Edit files under the installed package's
`skills/lean4/` directory (the path might look like `/site-packages/archon/skills/lean4/`). `archon init` re-registers the marketplace at the correct path on each run, so your edits take effect after re-init.

**Adding new global skills**: Create a new directory under the bundled
`skills/<your-skill-name>/` with a `SKILL.md` or `.claude-plugin/plugin.json` inside, and add it to `skills/.claude-plugin/marketplace.json`. Run `archon init` again on your project to pick up the new skill.

**We encourage you to customize.** If you notice the prover repeatedly making the same mistakes, or a proof strategy that consistently works for your project, codify it — add a skill or adjust a prompt. Archon improves as its skills and prompts accumulate lessons from your specific formalization work.

**Adding local skills**: Place them in `<project>/.claude/skills/<your-skill-name>/SKILL.md`. They are discovered by Claude Code automatically and won't conflict with Archon's `/archon-lean4:*` commands. No re-init needed.

### Monitoring progress

To check how the formalization is going, the easiest starting point is the **dashboard** (auto-launched by `archon loop` — visit the URL printed in the terminal, e.g. `http://localhost:8080`). It shows iteration progress, parallel prover status, a file-centric Diffs view backed by recorded code snapshots, agent logs with live streaming, a DAG view, blueprints reader, and proof journal milestones.

<p align="center">
<img src="docs/dashboard-logs.png" alt="Archon Dashboard — Logs view" width="800">
</p>

The **Logs** view groups logs by iteration with phase timing (plan → prover → review) and per-prover completion status. The subagents' logs can also be consulted. All of the logs are live-streamed. 

The **DAG** view renders the blueprint dependency graph interactively — nodes are colour-coded by status (proved / Mathlib-backed / ∞-effort), and clicking one shows its Lean name, status, and shortcuts to focus its cone or open it in the Blueprint / Diffs views. A git-history scrubber along the bottom replays the graph at any past iteration.

<p align="center">
<img src="docs/dashboard-DAG.png" alt="Archon Dashboard — DAG (blueprint dependency graph) view" width="800">
</p>

The **Blueprint** view renders the informal blueprint as a typeset document — chapters, definitions/theorems tagged with their `\leanok` / `\mathlibok` status, source citations, and per-declaration links into the graph and diffs.

<p align="center">
<img src="docs/dashboard-blueprints.png" alt="Archon Dashboard — Blueprint view" width="800">
</p>

The **Journal** view tracks proof milestones across sessions (which theorems were solved, blocked, or retried, with condensed reasoning traces), and the **Diffs** view replays per-iteration code snapshots — including a live fallback that reads the working tree when an iteration is mid-flight. When multi-lane is enabled, lane-specific logs and the per-file merge agent's output show up alongside the single-lane view.

You can also inspect state files directly:

- **`.archon/logs/iter-<N>/**/*.jsonl`** — running log of agent activity. The latest iteration's files tell you whether agents are still working.
- **`.archon/PROJECT_STATUS.md`** — overall progress: total sorries, what's solved, what's blocked, and reusable proof patterns.
- **`.archon/proof-journal/sessions/session_N/summary.md`** — detailed record of a specific iteration: what was attempted, what succeeded, what failed, and why.

These are updated automatically by the review agent after each iteration.

#### Starting the dashboard manually

If you disabled the auto-launched dashboard, or want to look at a project after the loop has finished and the terminal is gone:

```bash
archon dashboard /path/to/your-lean-project -p <port> 
```

#### Lean blueprint 

The planner is now responsible for maintaining blueprints (using [leanblueprint](https://github.com/PatrickMassot/leanblueprint) that is installed and configured when `archon setup` and `archon init` are run). You can read [Terence Tao's blog post](https://terrytao.wordpress.com/2023/11/18/formalizing-the-proof-of-pfr-in-lean4-using-blueprint-a-short-tour/) to understand how blueprints work and why they are helpful. 

In pratice, this means that Archon writes informal `tex` files before writing the corresponding `lean` files, in order to guide its formalization. You can run `leanblueprint serve` in the project directory to launch a server that renders the blueprints in HTML.

#### LeanDag 

We developed [LeanDag](https://github.com/AxelDlv00/LeanDAG) in a seperate repository, as it might be useful outside of Archon. It is a very simple API to query the DAG structure of a Lean project. 

Why added it to Archon? We noticed that LLMs tend to simplify dependencies in their internal representation of the project, having a deterministic structure to query and rely on grounds Archon's work in the real DAG. This is also helpful to have a clear picture of the difficulty of each component of the project, for this, it gives an estimation of the effort by counting the number of characters in the informal proofs, recursively summing the effort of the dependencies when they are not proven yet. While this estimation is rough, it is only used to sort the proving queue, and potentially breaking down high-effort theorems into smaller lemmas.  

### Existing lean4-skills and lean-lsp MCP installations

If you already have `lean4-skills` or `lean-lsp` MCP installed globally, `archon init` detects them and disables them **for this project only** — so only Archon's modified versions are active. Your global installations are untouched and continue working in all other projects.

To restore the originals in an Archon project:
```bash
cd /path/to/your-project
claude plugin enable lean4-skills --scope project     # re-enable standard skills
claude mcp add lean-lsp -s project -- uvx lean-lsp-mcp  # re-enable standard MCP
```

## Supplying informal material

Formalization quality improves materially when the agents have access to the original informal mathematics. The current trajectory of Archon is grounding more and more of its work in reference material (v0.2.0 introduced the retriever agent, v0.3.0 introduced the blueprint-writing DAG loop that forces Archon to specify the source and quoting it in the blueprints). 

Archon is already able to retrieve reference, if it doesn't, consider adding a permanent hint in `USER_HINTS.md` to ask it to do so and rely heavily on them. However, there is a limitation, it cannot retrieve papers behind paywalls, or textbooks that are not free access. 

There are several ways for you to supply such material to Archon:
1. **Papers and manuscripts** — You can provide PDF or LaTeX files in `/references`. 
2. **Blueprints** — Archon natively works with blueprints, you can edit them or add your own chapters, archon will use them (don't forget to freeze the parts you don't want it to change with `archon-protected.yaml`).
3. **Informal notes** — You can also provide notes in the format you want (e.g. markdown files), for instance in a `/notes` directory or in `/references`, don't forget to freeze them if you don't want Archon to edit them, and add a hint in `USER_HINTS.md` to make Archon aware of them and use them.
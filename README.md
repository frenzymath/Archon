# Archon

Archon is an agentic system that autonomously formalizes research-level mathematics in Lean 4. A **plan agent** provides strategic guidance while **prover agents** write and verify proofs — separating analysis from execution to avoid context explosion. The system handles repository-scale formalization through three phases: scaffolding, proving, and polish. Built on Claude Code and Claude Opus 4.6, with a modified fork of [lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp) and [lean4-skills](https://github.com/cameronfreer/lean4-skills). Archon originated from orchestrating Claude Code with OpenClaw — see [Why orchestrating Claude Code works](#why-orchestrating-claude-code-works). See also our [blog](https://frenzymath.com/blog/archon-firstproof/) and [announcement](https://frenzymath.com/news/archon-firstproof/).

Archon is designed and optimized for **project-level formalization** — multi-file repositories with interdependent theorems, not isolated competition problems. As such, single-problem benchmarks are not a specific optimization target. For model choice, **Opus 4.6 is strongly recommended**; Sonnet also works well but is less capable. Other models have not been tested — weaker models may struggle with the complex skills and prompt structures, in which case Archon's system design could hurt performance rather than help it.                                                          

**Security note:** `archon-loop.sh` runs Claude Code with `--dangerously-skip-permissions --permission-mode bypassPermissions`, meaning the model can execute arbitrary shell commands, read/write any file the process can access, and make network requests — all without asking for confirmation. This is necessary for unattended operation but carries real risk: a misbehaving model could delete files, overwrite code, or run unintended commands. **While Opus 4.6 NEVER caused harm across all of our experiments,** the following measures can further reduce exposure:

- **Commit and push your project before running Archon, so any unintended changes can be easily reverted.**
- Run Archon under a **dedicated, low-privilege user** that only has access to the project directory
- Run inside a **Docker container** or VM with no access to sensitive data or credentials
- Avoid running as root or with access to production systems
- Review `.archon/proof-journal/` after each run to audit what the agents did

## Setup

Prerequisites: git, Python 3.10+, curl, elan (Lean toolchain).

```bash
cd /path/where/you/want/Archon
git clone <repo-url>
cd Archon
./setup.sh
```

`setup.sh` installs system-level dependencies (uv, tmux, Claude Code) and verifies your Lean toolchain. It also checks for API keys needed by the informal agent (`OPENAI_API_KEY`, `GEMINI_API_KEY`, or `OPENROUTER_API_KEY`) — at least one is recommended but not required.

Note: the bundled informal agent is a simplified demonstration — it makes a single API call to an external model for proof sketches. Our internal implementation is more involved but not yet ready for open-sourcing. In practice, the one-shot approach does not show an obvious performance drop, likely because Claude Code performs its own verification and refinement on the returned sketches.

## Usage

All commands below assume you are inside the Archon directory:

```bash
cd /path/to/Archon
```

### 1. Initialize a project

**Option A — Initialize an existing project**:
```bash
./init.sh /path/to/your-lean-project
```

**Option B — Create a new project in Archon's workspace**:
```bash
./init.sh workspace/my-project
```

If no path is given, `init.sh` prompts you for a project name and creates it under `workspace/`.

`init.sh` does the following inside your project:
- Creates `.archon/` with runtime state files and symlinked prompts
- Symlinks Archon's lean4 skills into `.claude/skills/archon-lean4`
- Symlinks the informal agent into `.claude/tools/archon-informal-agent.py`
- Installs Archon's lean-lsp MCP server as `archon-lean-lsp` at project scope
- Detects and disables any conflicting global lean4-skills and lean-lsp MCP (see [Existing lean4-skills and lean-lsp MCP installations](#existing-lean4-skills-and-lean-lsp-mcp-installations))
- Launches Claude Code interactively to detect project state, set up lakefile/Mathlib if needed, and write initial objectives

Init automatically runs `/archon-lean4:doctor` at the end to verify the full setup (Lean environment, MCP, skills, state files).

### 2. Start the automated loop

```bash
./archon-loop.sh /path/to/your-lean-project
```

The loop alternates plan and prover agents through stages:

| Stage | What happens |
|-------|-------------|
| `autoformalize` | Scaffolding — translate informal math into Lean declarations with `sorry` |
| `prover` | Proving — fill `sorry` placeholders with verified proofs |
| `polish` | Verification and polish — golf, refactor, extract reusable lemmas |

**NOTE:** The prover agent is instructed to push formalization as far as possible, so the first few runs typically take **several hours** as it clears all low-hanging fruits. Once only genuinely difficult sorrys remain, each iteration becomes much shorter. To confirm the agent is running, check the latest log in `.archon/logs/archon-<timestamp>.jsonl` in your project directory; the agent also writes Lean files when running, which you can see directly.

The loop exits automatically when the stage reaches `COMPLETE`. You can run `archon-loop.sh` on multiple projects in parallel from separate terminals — each project's state is independent.

### Guiding agents

Archon runs fully autonomously, but guiding it with your expertise will speed it up, align it with your preferred proof style, and help it overcome mathematical and Lean challenges.

There are three ways to influence Archon's behavior. Each serves a different purpose:

| Mechanism | When to use | Lifetime | Who reads it |
|-----------|-------------|----------|-------------|
| **USER_HINTS.md** | Mid-run course corrections | One-shot — cleared after each plan cycle | Plan agent |
| **/- USER: ... -/ comments** | File-specific proof guidance | Persistent — stays in the `.lean` file | Prover agent |
| **Prompts** | Change how agents think and operate | Permanent — applies every iteration | All agents |

**USER_HINTS.md** — for things that change between iterations. Examples: "prioritize theorem X next", "stop trying approach Y, it's a dead end". The plan agent reads this once, acts on it, and clears the file. Don't put permanent instructions here — they'll be lost.

**/- USER: ... -/ comments** — for proof-level guidance tied to a specific `.lean` file. Examples: "try using Finset.sum_comm here", "this sorry depends on the helper lemma above". These persist in the source file and are visible to whichever prover agent owns that file.

**Prompts** — for changing how agents behave across all iterations. Edit prompts when you want to change the plan agent's strategy, the prover's proof style, or the review agent's analysis. Archon has two layers — local overrides global:

| Layer | Location | Scope |
|-------|----------|-------|
| **Global** | `Archon/.archon-src/prompts/*.md` | All projects |
| **Local** | `<project>/.archon/prompts/*.md` | One project only |

By default, local prompts are symlinks to the global ones — so edits to the global prompt are picked up automatically by every project on the next iteration. To override a prompt for one project, replace the symlink with a copy and edit it. Note that once you do this, future updates to the global prompt will no longer propagate to that project — you are responsible for keeping the local copy up to date.

### Customizing skills

Archon ships with a modified fork of [lean4-skills](https://github.com/cameronfreer/lean4-skills), installed as `archon-lean4` (providing `/archon-lean4:prove`, `/archon-lean4:doctor`, etc.). Skills follow a global-vs-local layering:

| Layer | Location | What it provides |
|-------|----------|-----------------|
| **Global** | `Archon/.archon-src/skills/*/` | Skills symlinked into every project on init |
| **Local** | `<project>/.claude/skills/<name>/` | Project-specific skills you create |

**Modifying global skills**: You can edit files directly under `Archon/.archon-src/skills/lean4/`. Since projects symlink to this directory, changes take effect on the next Claude Code session in any project. Be aware that this affects all projects.

**Adding new global skills**: Create a new directory under `Archon/.archon-src/skills/<your-skill-name>/` with a `SKILL.md` or `.claude-plugin/plugin.json` inside. Run `./init.sh` again on your project to pick up the new skill — init symlinks all directories under `.archon-src/skills/` automatically.

**Modifying local (project-only) skills**: To customize a global skill for one project without affecting others, replace the symlink with a local copy. As with prompts, once you replace the symlink, future updates to the global skill will no longer propagate to this project.

**Adding local skills**: Place them in `<project>/.claude/skills/<your-skill-name>/SKILL.md`. They are discovered by Claude Code automatically and won't conflict with Archon's `/archon-lean4:*` commands. No re-init needed.

### Monitoring progress

To check how the formalization is going, look at these files in your project:

- **`.archon/logs/archon-<timestamp>.jsonl`** — running log of agent activity. Check the latest timestamp to monitor whether agents are still working.
- **`.archon/PROJECT_STATUS.md`** — overall progress: total sorries, what's solved, what's blocked, and reusable proof patterns. This is the best starting point.
- **`.archon/proof-journal/sessions/session_N/summary.md`** — detailed record of a specific iteration: what was attempted, what succeeded, what failed, and why.

These are updated automatically by the review agent after each iteration. If the loop has finished with `--no-review` and you want to generate a review manually, run `./review.sh /path/to/your-project`.

### Existing lean4-skills and lean-lsp MCP installations

If you already have `lean4-skills` or `lean-lsp` MCP installed globally, `init.sh` detects them and disables them **for this project only** — so only Archon's modified versions are active. Your global installations are untouched and continue working in all other projects.

To restore the originals in an Archon project:
```bash
cd /path/to/your-project
claude plugin enable lean4-skills --scope project     # re-enable standard skills
claude mcp add lean-lsp -s project -- uvx lean-lsp-mcp  # re-enable standard MCP
```

### CLI options

| Flag | Description |
|------|-------------|
| `--max-iterations N` | Max plan→prover→review cycles (default: 10). Exits early if stage reaches `COMPLETE`. |
| `--stage STAGE` | Force a stage (`autoformalize`, `prover`, `polish`) instead of reading from PROGRESS.md. |
| `--serial` | One prover at a time instead of parallel (one per file). |
| `--verbose-logs` | Save raw Claude stream events to `.raw.jsonl` for debugging. |
| `--no-review` | Skip review phase. Saves time/cost; plan agent still works without it. |
| `--dry-run` | Print prompts without launching Claude. |

## Standard vs. orchestrator-scheduled mode

`archon-loop.sh` is the **standard mode** — a fixed plan→prover→review loop that runs unattended. It is sufficient for most formalization tasks.

In our experiments, replacing the fixed loop with an **orchestrator-scheduled mode** — where an outer orchestrator like OpenClaw drives Claude Code directly — yielded stronger results. Instead of following a rigid pipeline, the orchestrator can freely choose when to plan, prove, or review based on the current state, and can supervise the model continuously to prevent premature termination.

### How to use orchestrator-scheduled mode

Ensure your orchestrator has access to the project directory, and ask it to read README.md for an overview of the project.

We provide [`ORCHESTRATOR_GUIDE.md`](ORCHESTRATOR_GUIDE.md) as a companion guide for your orchestrator. It was authored by our own OpenClaw based on its accumulated experience orchestrating Claude Code across multiple formalization projects. The guide covers how to read Archon's state files, decide which stage to run next, compose prompts from `.archon/prompts/`, and invoke `claude -p` — including prompt composition, adaptive scheduling logic, failure recovery, and operational rules learned from production use.

### What changes compared to the standard loop

In standard mode, `archon-loop.sh` enforces a fixed cycle: plan→prover→review, repeated up to `--max-iterations`. The orchestrator-scheduled mode differs in several ways:

- **Environment management** — the orchestrator handles setup and debugging: installing dependencies, resolving Mathlib cache issues, verifying that skills and MCP work correctly. These tasks often require back-and-forth troubleshooting that a fixed script cannot do.
- **Flexible phase ordering** — the orchestrator decides when to plan, prove, or review based on what it observes, rather than following a fixed sequence. It might skip planning when the current objectives are still valid.
- **Real-time intervention** — the orchestrator can step in the moment the model is stuck. It detects surrender patterns (e.g., "Mathlib lacks infrastructure") and pushes the prover back in with refined hints or alternative strategies.
- **Richer cross-session context** — the orchestrator has its own memory. It can retain whatever state matters for adaptive routing — failure histories, proof patterns, mathematical context — accumulating richer context over time than a script that only persists a few markdown artifacts between iterations.

### Why orchestrator-scheduled mode is more effective

**Flexibility** — the orchestrator decides when to plan, prove, or review based on current state rather than following a fixed sequence, making it adaptable to a wider range of formalization tasks.

**Evolvability** - by design, orchestrators like OpenClaw can author and refine skills and prompts over time. The global/local skill and prompt slots are designed not only for human experts but also for orchestrators: they can analyze failure modes and update skills or prompts accordingly (with your permission), making the system progressively more powerful.

**Stability** — a supervisor layer catches errors that a fixed loop cannot: crashed sessions, malformed state files, stuck provers, or plan agents that set unreasonable objectives. The orchestrator acts as a safety net that keeps the process running correctly over hours or days without manual intervention.

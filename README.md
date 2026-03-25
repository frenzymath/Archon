# Archon

Archon is an agentic system that autonomously formalizes research-level mathematics in Lean 4. A **plan agent** provides strategic guidance while **prover agents** write and verify proofs — separating analysis from execution to avoid context explosion. The system handles repository-scale formalization through three phases: scaffolding, proving, and polish. Built on Claude Code and Claude Opus 4.6, with a modified fork of [lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp) and [lean4-skills](https://github.com/cameronfreer/lean4-skills). Archon originated from orchestrating Claude Code with OpenClaw — see [Why orchestrating Claude Code works](#why-orchestrating-claude-code-works). See also our [blog](https://frenzymath.com/blog/archon-firstproof/) and [announcement](https://frenzymath.com/news/archon-firstproof/).

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

The loop exits automatically when the stage reaches `COMPLETE`. You can run `archon-loop.sh` on multiple projects in parallel from separate terminals — each project's state is independent.

### Guiding agents

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

## Why orchestrating Claude Code works

Archon's `archon-loop.sh` is a distillation of a workflow we originally built using an outer orchestrator (such as OpenClaw) to drive Claude Code. Understanding that origin explains why the architecture looks the way it does — and why you might want to return to the full orchestrator-driven setup for harder problems.

### The original workflow

If you already have OpenClaw or a similar terminal orchestrator, the end-to-end flow is straightforward:

1. **Environment setup** — OpenClaw can help set up and debug the various environments needed for formalization: installing dependencies, configuring Lean toolchains, resolving Mathlib cache issues, and verifying that skills and MCP are working correctly. These are tasks that often require back-and-forth troubleshooting — an orchestrator handles them naturally.

2. **Drive Claude Code directly** — With skills and MCP correctly installed in the project, give the orchestrator enough context about the formalization goal and let it invoke Claude Code sessions. Set up cron jobs or polling loops so the orchestrator continuously supervises Claude Code's work.

3. **Supervise persistence** — This is the critical part. Claude Code, left to its own devices, tends to give up early. For many theorems it will claim that Mathlib lacks the necessary infrastructure, or that the proof would be too long, and stop pushing forward. For research-grade formalization this is unacceptable — the interesting results live precisely in the territory where the model's first instinct is to quit. An outer orchestrator can detect these surrender patterns and push the prover back in with refined hints, decomposed subgoals, or alternative proof strategies.

4. **Multi-window intelligence** — If the orchestrator has a second Claude Code session available, it can use that instance to gather information, search Mathlib, read papers, and organize context — effectively serving as a research assistant for its own planning. The plan agent in `archon-loop.sh` is a simplified version of this pattern.

### From orchestrator to script

We took that manual orchestrator-driven workflow and condensed it into `archon-loop.sh`: the plan/prover alternation, the parallel agent dispatch, the cross-iteration memory, and the stage-driven progression all come from observing what an effective outer orchestrator actually does when supervising Claude Code over many hours.

The script is sufficient for most formalization tasks. But the orchestrator-driven approach remains strictly more powerful, because a live orchestrator can:

- Intervene in real time when the model is stuck, rather than waiting for the next plan cycle
- Maintain richer context across sessions than markdown state files allow
- Adapt its supervision strategy on the fly based on what it observes

### Why this pairing works

The deepest advantage of pairing an orchestrator with Claude Code — rather than writing a custom proving system from scratch — is **transparency**. Everything happens in a terminal session that a human can read, interrupt, and redirect. When something goes wrong, you can talk directly to the model to diagnose and fix the issue, rather than debugging opaque internal state on the machine. This makes the system dramatically easier to debug, easier to oversee, and easier to trust with ambitious formalization targets.

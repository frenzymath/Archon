# Archon

Archon is an agentic system that autonomously formalizes research-level mathematics in Lean 4. A **plan agent** provides strategic guidance while **prover agents** write and verify proofs — separating analysis from execution to avoid context explosion. The system handles repository-scale formalization through three phases: scaffolding, proving, and polish. Built on Claude Code and Claude Opus 4.6, with a modified fork of [lean-lsp-mcp](https://github.com/oOo0oOo/lean-lsp-mcp) and [lean4-skills](https://github.com/cameronfreer/lean4-skills). Archon originated from orchestrating Claude Code with OpenClaw — see [Why orchestrating Claude Code works](#why-orchestrating-claude-code-works). See also our [first proof writeup](https://frenzymath.com/blog/archon-firstproof/) and [announcement](https://frenzymath.com/news/archon-firstproof/).

## Setup

Prerequisites: git, Python 3.10+, curl, elan (Lean toolchain).

```bash
git clone <repo-url> ~/Archon
cd ~/Archon
./setup.sh
```

`setup.sh` installs system-level dependencies (uv, tmux, Claude Code) and verifies your Lean toolchain. Run it once — it does not touch your projects.

## Usage

### 1. Initialize a project

**Option A — Use an existing project in-place** (recommended):
```bash
~/Archon/init.sh /path/to/your-lean-project
```

**Option B — Use Archon's built-in workspace**:
```bash
mkdir -p ~/Archon/workspace/my-project
~/Archon/init.sh ~/Archon/workspace/my-project
```

If no path is given, `init.sh` defaults to the current directory and prints a clear message.

`init.sh` does the following inside your project:
- Creates `.archon/` with runtime state files and symlinked prompts
- Symlinks Archon's lean4 skills into `.claude/skills/lean4`
- Configures lean-lsp MCP server at project scope
- Detects and disables any conflicting global lean4-skills (see [Existing lean4-skills installations](#existing-lean4-skills-installations))
- Launches Claude Code interactively to detect project state, set up lakefile/Mathlib if needed, and write initial objectives

After init, verify:
```bash
cd /path/to/your-lean-project
claude
# Inside Claude Code:
/lean4:doctor
```

### 2. Start the automated loop

From the project directory:
```bash
cd /path/to/your-lean-project
~/Archon/archon-loop.sh
```

Or specify the path explicitly:
```bash
~/Archon/archon-loop.sh /path/to/your-lean-project
```

The loop alternates plan and prover agents through stages:

| Stage | What happens |
|-------|-------------|
| `autoformalize` | Scaffolding — translate informal math into Lean declarations with `sorry` |
| `prover` | Proving — fill `sorry` placeholders with verified proofs |
| `polish` | Verification and polish — golf, refactor, extract reusable lemmas |

The loop exits automatically when the stage reaches `COMPLETE`.

### 3. Multiple projects

```bash
# Initialize multiple projects
~/Archon/init.sh ~/repos/project-A
~/Archon/init.sh ~/repos/project-B

# Run in parallel from separate terminals:
~/Archon/archon-loop.sh ~/repos/project-A
~/Archon/archon-loop.sh ~/repos/project-B
```

Each project gets `.archon/` (runtime state) and `.claude/skills/lean4` (symlink to Archon skills).

### Guiding agents while the loop runs

No need to stop the loop — provide hints in two places:

- **`<project>/.archon/USER_HINTS.md`** — strategic guidance for the plan agent
- **`/- USER: ... -/` comments in `.lean` files** — file-specific hints for the prover

### Monitoring

```bash
# Structured log
tail -f /path/to/project/.archon/logs/archon-*.jsonl

# Check prover results
watch -n10 'ls -lt /path/to/project/.archon/task_results/'
```

### Existing lean4-skills installations

If you already have the standard `lean4-skills` plugin installed globally, `init.sh` will automatically detect it and **disable it for this project only**. This prevents Claude Code from seeing two conflicting skill sets (Archon's modified version and the standard one) and not knowing which to use.

Your global installation is **not removed** — it continues to work in all other projects.

To re-enable the standard lean4-skills in an Archon project:
```bash
cd /path/to/your-project
claude plugin enable lean4-skills --scope project
```

To check which plugins are active, run `/plugin` inside Claude Code and check the Installed tab.

### CLI options

| Flag | Description |
|------|-------------|
| `--max-iterations N` | Max loop iterations (default: 10) |
| `--stage STAGE` | Override the current stage |
| `--serial` | Use a single prover instead of parallel agents |
| `--verbose-logs` | Also save raw Claude stream events to `.raw.jsonl` |
| `--dry-run` | Print prompts without launching Claude |

## Why orchestrating Claude Code works

Archon's `archon-loop.sh` is a distillation of a workflow we originally ran by hand using an outer orchestrator (such as OpenClaw) to drive Claude Code. Understanding that origin explains why the architecture looks the way it does — and why you might want to return to the full orchestrator-driven setup for harder problems.

### The original workflow

If you already have OpenClaw or a similar terminal orchestrator, the end-to-end flow is straightforward:

1. **Bootstrap** — OpenClaw can clone and install Archon, then run `init.sh` against your target project. Whether you start with a configured Lean 4 repository or only natural-language material (a paper, lecture notes, a textbook chapter), Archon's init stage handles the rest.

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

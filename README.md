# Archon

Archon is an agentic system that autonomously formalizes research-level mathematics in Lean 4. A **plan agent** provides strategic guidance while **prover agents** write and verify proofs — separating analysis from execution to avoid context explosion. The system handles repository-scale formalization through three phases: scaffolding, proving, and polish. Built on Claude Code and Claude Opus 4.6, with LeanSearch via MCP and 40+ bundled skill references encoding authentic mathematical practice.

## Setup

Prerequisites: git, Python 3.10+, curl, elan (Lean toolchain).

```bash
git clone <repo-url> ~/Archon
cd ~/Archon
./setup.sh
```

Install once. Works with any number of Lean projects.

## Usage

### 1. Initialize a project

**Option A — Use an existing project in-place** (recommended for active repos):
```bash
cd ~/Archon
./init.sh /path/to/your-lean-project
```
This adds a `.archon/` folder (runtime state) and symlinks Archon skills into `.claude/skills/` inside your project. No project files are copied or moved.

**Option B — Create a project in Archon's workspace**:
```bash
cd ~/Archon
./init.sh workspace/my-project
```

If no path is given, `init.sh` defaults to the current directory and prints a clear message about what it's doing.

Init installs per-project MCP + skills and guides you through setup:
- Detects existing Lean project state
- Sets up lakefile and Mathlib if needed
- Counts sorries, writes initial objectives

After init, verify:
```bash
cd /path/to/your-lean-project   # or workspace/my-project
claude
# Inside Claude Code:
/lean4:doctor
```

### 2. Start the automated loop

```bash
cd ~/Archon
./archon-loop.sh /path/to/your-lean-project
```

Or from the project directory (no path needed):
```bash
cd /path/to/your-lean-project
~/Archon/archon-loop.sh
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
# External projects — no copying needed
./init.sh ~/repos/project-A
./init.sh ~/repos/project-B

# Or workspace projects
./init.sh workspace/project-C

# Run in parallel from separate terminals:
./archon-loop.sh ~/repos/project-A
./archon-loop.sh ~/repos/project-B
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
| `--max-iterations N` | Max loop iterations (default: 50) |
| `--stage STAGE` | Override the current stage |
| `--serial` | Use a single prover instead of parallel agents |
| `--verbose-logs` | Also save raw Claude stream events to `.raw.jsonl` |
| `--dry-run` | Print prompts without launching Claude |

## Key Features

### Dual-agent architecture with strategic oversight

The plan agent doesn't just dispatch tasks. When the prover encounters obstacles, the plan agent employs three intervention strategies: **detailed informal support** (generating step-by-step natural-language guidance), **decomposition** (breaking complex proofs into smaller, independently provable sub-lemmas), and **informal re-routing** (proposing alternative proof strategies when the standard approach lacks necessary library infrastructure).

### Parallel prover agents

Prover iterations automatically detect which `.lean` files contain `sorry` and spawn one prover agent per file using Claude Code agent teams. Each agent has exclusive ownership of its file — no conflicts, no merge issues. Use `--serial` to fall back to a single prover.

### Pre-generated informal proofs

Archon pre-generates complete informal proofs before attempting formalization, eliminating wasted computation from repeated re-derivation during proving cycles. The plan agent enriches blueprints using external model consultation and web search for published papers.

### Task tracking organized by theorem

`task_pending.md` is organized by file and theorem, not by time. Each theorem accumulates its attempt history: what was tried, what failed, what dead ends to avoid. This persistent memory across fresh-context iterations prevents the system from rediscovering the same dead ends.

### Expert knowledge through bundled skills (40+ guides)

All Lean 4 skill references ship with the project — no external dependencies. Includes tactic patterns, domain-specific proof strategies, Mathlib integration guides, proof golfing patterns, compilation error fixes, preferred Mathlib idioms, and an unavailable theorems index covering 52 mathematical domains.

### Local lean-lsp-mcp server

The Lean LSP MCP server ships as a local fork with adjusted rate limits, ensuring agents can search Mathlib effectively without hitting throttling during intensive proof sessions.

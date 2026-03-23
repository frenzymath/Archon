# Archon

Archon is an agentic system that autonomously formalizes research-level mathematics in Lean 4. A **plan agent** provides strategic guidance while **prover agents** write and verify proofs — separating analysis from execution to avoid context explosion. The system handles repository-scale formalization through three phases: scaffolding, proving, and polish. Built on Claude Code and Claude Opus 4.6, with LeanSearch via MCP and 40+ bundled skill references encoding authentic mathematical practice.

## Setup

Prerequisites: git, Python 3.10+, curl.

```bash
git clone <repo-url>
cd Archon-preopen
./setup.sh
```

`setup.sh` will:
1. Verify system prerequisites (git, Python, pip, curl)
2. Install `uv` (Python package manager) and the local lean-lsp-mcp server
3. Install `tmux` (required for parallel prover agents)
4. Install Claude Code (or detect existing installation)
5. Register the lean-lsp-mcp MCP server at project scope
6. Register the bundled lean4 skills plugin via local marketplace

After setup, verify and register plugins:
```bash
cd Archon
claude
# Inside Claude Code:
/plugin marketplace add .claude/skills
/plugin install lean4@archon-local
/lean4:doctor
```

If `setup.sh` registered the plugins automatically, the first two commands will confirm they're already installed. If not, they complete the registration.

## Usage

### Starting a new project

```bash
cd Archon
./archon-loop.sh
```

On first run, `PROGRESS.md` starts at stage `init`. The script launches Claude Code **interactively** so you can:
- Provide informal proofs, blueprints, or problem statements
- Point to an existing Lean project
- Configure Lean and Mathlib versions

Claude detects the project state and advances to the next stage. Then re-run the script to start the automated loop.

### Automated loop

```bash
./archon-loop.sh              # parallel provers (default)
./archon-loop.sh --serial     # single prover per iteration
./archon-loop.sh --dry-run    # print prompts without running
```

The loop alternates plan and prover agents through four stages:

| Stage | What happens |
|-------|-------------|
| `init` | Interactive setup (runs once, then exits) |
| `autoformalize` | Scaffolding — translate informal math into Lean declarations with `sorry` |
| `prover` | Proving — fill `sorry` placeholders with verified proofs |
| `polish` | Verification and polish — golf, refactor, extract reusable lemmas |

The loop exits automatically when the stage reaches `COMPLETE`.

### Guiding agents while the loop runs

No need to stop the loop — provide hints in two places:

- **`USER_HINTS.md`** — strategic guidance for the plan agent (e.g., "the measure_union approach is a dead end, try sigma-additivity instead"). The plan agent reads this every iteration and translates your hints into concrete prover objectives.
- **`/- USER: ... -/` comments in `.lean` files** — file-specific hints for the prover that owns that file (e.g., "try Stacks 0A31 for this lemma").

### Monitoring

```bash
# Main dialogue log (plan agent + monitor)
tail -f Archon/logs/archon-*.readable.log

# Watch which .lean files provers are modifying
watch -n5 'ls -lt proetale/**/*.lean 2>/dev/null | head -20'

# Check prover results as they finish
watch -n10 'ls -lt Archon/task_results/'
```

### CLI options

| Flag | Description |
|------|-------------|
| `--max-iterations N` | Max loop iterations (default: 50) |
| `--stage STAGE` | Override the current stage |
| `--serial` | Use a single prover instead of parallel agents |
| `--verbose-logs` | Generate raw JSON logs alongside readable logs |
| `--dry-run` | Print prompts without launching Claude |

## Key Features

### Dual-agent architecture with strategic oversight

The plan agent doesn't just dispatch tasks. When the prover encounters obstacles, the plan agent employs three intervention strategies: **detailed informal support** (generating step-by-step natural-language guidance), **decomposition** (breaking complex proofs into smaller, independently provable sub-lemmas), and **informal re-routing** (proposing alternative proof strategies when the standard approach lacks necessary library infrastructure). It recognizes common failure patterns — premature abandonment, wrong constructions, skipped web searches — and responds with targeted corrections, tracking dead ends so provers never re-explore failed approaches.

### Parallel prover agents

Prover iterations automatically detect which `.lean` files contain `sorry` and spawn one prover agent per file using Claude Code agent teams. Each agent has exclusive ownership of its file — no conflicts, no merge issues. The plan agent coordinates and commits the combined work. Use `--serial` to fall back to a single prover.

### Pre-generated informal proofs

Archon pre-generates complete informal proofs before attempting formalization, eliminating wasted computation from repeated re-derivation during proving cycles. The plan agent enriches blueprints using external model consultation and web search for published papers, ensuring the prover always has rich mathematical context rather than working blind.

### Task tracking organized by theorem

`task_pending.md` is organized by file and theorem, not by time. Each theorem accumulates its attempt history: what was tried, what failed, what dead ends to avoid, what Mathlib lemmas were found. An index at the top lets agents jump to the right section. `task_done.md` archives completed theorems with the strategy that worked. This persistent memory across fresh-context iterations prevents the system from rediscovering the same dead ends.

### Expert knowledge through bundled skills (40+ guides)

All Lean 4 skill references ship with the project — no external dependencies. This encodes tacit expertise reflecting authentic mathematical practice: tactic patterns, domain-specific proof strategies (measure theory, algebra, topology), Mathlib integration guides, proof golfing patterns, compilation error fixes, and more. Two notable additions:

- **Preferred Mathlib idioms** — which abstractions to choose (filters over epsilon-delta, bundled morphisms, Finset vs Set, Finite vs Fintype)
- **Unavailable theorems index** — 52 mathematical domains with classical theorems that should not be used as default dependencies due to missing or immature Mathlib infrastructure. This proactively prevents wasted effort on approaches that will fail due to infrastructure gaps.

### Local lean-lsp-mcp server

The Lean LSP MCP server ships as a local fork with adjusted rate limits, ensuring agents can search Mathlib effectively without hitting throttling during intensive proof sessions. The entire tool suite — LeanSearch, Lean LSP diagnostics, goal inspection — runs locally with no external service dependencies.

### Inviolable proof integrity rules

Working proofs are never modified. Theorem statements are never changed. Mathlib version is never touched. Every edit is verified before and after. If compilation breaks, the change is reverted immediately. These rules are absolute — no optimization or convenience overrides them.

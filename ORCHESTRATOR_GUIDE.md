# Orchestrator Guide — Driving Archon with OpenClaw

This document teaches an orchestrator (such as OpenClaw) how to drive Archon's Claude Code workflow. After reading this file, the orchestrator should be able to flexibly schedule plan, prover, and review stages without relying on the fixed `archon-loop.sh` pipeline.

## Core Principle

The orchestrator does **not** start interactive Claude Code sessions and improvise. Instead, it assembles prompts from Archon's framework and invokes `claude -p` with those prompts. The orchestrator decides **what stage to run next** and **which prompt to compose**, then drives the route.

---

## 1. How to Invoke Claude Code

### Command Template

```bash
cd <project-directory>
claude -p "<prompt>" \
    --dangerously-skip-permissions --permission-mode bypassPermissions \
    --model claude-opus-4-6
```

**Critical rules:**
- Always `cd` into the project directory first — Claude Code must see `.claude/skills/` and `.archon/` in its working directory
- Never add `--verbose` — it disables the TUI entirely
- Never add `--resume` unless explicitly recovering a crashed session
- The prompt is a single string; compose it by reading Archon's prompt files and injecting project-specific context

### What Each Invocation Produces

Each `claude -p` call is a self-contained session. It starts, executes the prompt, and exits. The orchestrator reads the state files afterward to decide the next step.

---

## 2. Available Stages and Prompts

Archon provides these prompt files in `<project>/.archon/prompts/` (symlinked from `.archon-src/prompts/`):

| Stage | Prompt File | Agent Role |
|-------|-------------|------------|
| Plan | `plan.md` | Read results, set objectives, prepare informal content |
| Prover (autoformalize) | `prover-autoformalize.md` | Scaffold Lean declarations from informal math |
| Prover (prover) | `prover-prover.md` | Fill `sorry` placeholders with proofs |
| Prover (polish) | `prover-polish.md` | Golf, refactor, extract reusable lemmas |
| Review | `review.md` | Analyze prover log, write proof journal |

### Composing a Prompt

Read the relevant prompt file and prepend context. Example for the plan agent:

```
You are the plan agent for project '<name>'. Current stage: prover.
Project directory: /path/to/project
Project state directory: /path/to/project/.archon
Read .archon/CLAUDE.md for your role, then read .archon/prompts/plan.md and .archon/PROGRESS.md.
All state files (PROGRESS.md, task_pending.md, task_done.md, USER_HINTS.md, task_results/) are in .archon/.
The .lean files are in /path/to/project/.
```

The orchestrator constructs this string, reads the prompt file content if needed, and passes the assembled prompt to `claude -p`.

---

## 3. State Files the Orchestrator Reads

All state is in `<project>/.archon/`:

| File | Written By | What It Contains |
|------|-----------|-----------------|
| `PROGRESS.md` | Plan agent | Current stage, objectives, summary |
| `task_pending.md` | Plan agent | Per-theorem attempt history, dead ends |
| `task_done.md` | Plan agent | Resolved theorems |
| `task_results/<file>.md` | Prover agent(s) | Raw prover output per file |
| `USER_HINTS.md` | User / Orchestrator | Strategic guidance for plan agent |
| `PROJECT_STATUS.md` | Review agent | Cumulative progress, blockers, patterns |
| `proof-journal/sessions/session_N/` | Review agent | Per-iteration journal |

### Reading the Current Stage

```bash
awk '/^## Current Stage/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit}' .archon/PROGRESS.md
```

Returns: `init`, `autoformalize`, `prover`, `polish`, or `COMPLETE`.

### Checking Sorry Count

```bash
find <project> -name '*.lean' -not -path '*/.lake/*' -not -path '*/lake-packages/*' \
    | xargs grep -c '\bsorry\b' 2>/dev/null | grep -v ':0$' | awk -F: '{s+=$2} END {print s}'
```

---

## 4. Decision Logic — What to Run Next

The orchestrator replaces `archon-loop.sh`'s fixed cycle with adaptive scheduling:

### Standard Sequence (baseline)

```
Plan → Prover → Review → Plan → Prover → Review → ...
```

### Adaptive Decisions

The orchestrator should read state files and decide:

| Condition | Action |
|-----------|--------|
| First run / no PROGRESS.md | Run Plan to initialize objectives |
| `task_results/` has new files | Run Plan to collect results before next prover round |
| PROGRESS.md has clear objectives, no new results | Run Prover |
| Prover just finished (log exists, no review yet) | Run Review, then run Plan |
| Sorry count unchanged after prover | Run Plan with `USER_HINTS.md` guidance to try different approach |
| Sorry count is 0 | Run Plan to verify and advance stage (prover → polish) |
| Stage is `COMPLETE` | Stop |
| Multiple prover rounds with no progress | Write hints to `USER_HINTS.md`, run Plan |
| Review shows all remaining targets are blocked | Escalate to user |

### Writing Hints

The orchestrator can write to `USER_HINTS.md` to steer the plan agent:

```bash
cat > .archon/USER_HINTS.md << 'EOF'
Prioritize theorem X — it blocks three other theorems.
Stop trying approach Y on file Z, it's a dead end (tried 3 times).
The key insight for lemma W is: use Finset.sum_comm then induction on n.
EOF
```

The plan agent reads this, incorporates it, and clears the file.

---

## 5. Running the Review Stage

Review requires a log file from the prover run. The orchestrator should:

1. **Extract attempt data** (deterministic, no LLM):
```bash
python3 <archon>/scripts/extract-attempts.py <log-file> .archon/proof-journal/current_session/attempts_raw.jsonl
```

2. **Run the review agent**:
```bash
claude -p "You are the review agent for project '<name>'. Current stage: <stage>.
Project directory: <path>
Project state directory: <path>/.archon
Read .archon/CLAUDE.md for your role, then read .archon/prompts/review.md.
Session number: <N>.
Pre-processed attempt data: .archon/proof-journal/current_session/attempts_raw.jsonl (READ THIS FIRST).
Prover log: <log-file>
Write your output to: .archon/proof-journal/sessions/session_<N>/" \
    --dangerously-skip-permissions --permission-mode bypassPermissions
```

3. **Validate output**:
```bash
python3 <archon>/scripts/validate-review.py .archon/proof-journal/sessions/session_<N> .archon/proof-journal/current_session/attempts_raw.jsonl
```

---

## 6. Failure Patterns and Recovery

### Claude Code Gives Up Too Early

**Pattern**: Prover reports "Mathlib lacks infrastructure" or "proof would be too long" and stops.

**Response**: Write specific guidance to `USER_HINTS.md`:
```
Do not accept "Mathlib lacks X" as a reason to leave sorry.
For theorem Y: prove the missing lemma yourself, or find an alternative approach.
Use Web Search to find the paper proof if needed.
```

Then run Plan, which will incorporate these hints into the next prover's objectives.

### Claude Code Doesn't Use Web Search

**Pattern**: When blueprint references a paper theorem, Claude Code searches Mathlib, finds nothing, and gives up — without searching the web for the paper.

**Response**: This is already addressed in the prover prompt (`prover-prover.md` section 5.4), but can be reinforced via `USER_HINTS.md`.

### Session Produces No Output

**Pattern**: `claude -p` exits but `task_results/` is empty.

**Response**: Check the log file for errors. Common causes:
- API authentication failure → check `ANTHROPIC_API_KEY` / proxy config
- MCP server not running → run `/archon-lean4:doctor` to diagnose
- Context exhaustion → normal, just run again (Claude Code auto-handles compaction)

### API Stream Hangs

**Pattern**: `claude -p` process runs but produces no output for extended periods.

**Response**: The orchestrator can set a process-level timeout:
```bash
timeout 4h claude -p "<prompt>" --dangerously-skip-permissions ...
```

If killed by timeout, partial results in `task_results/` may still be usable. Run Review on the partial log.

---

## 7. Parallel Prover Scheduling

The orchestrator can run multiple provers in parallel by assigning each a different file:

```bash
# Find files with sorry
SORRY_FILES=$(find <project> -name '*.lean' -not -path '*/.lake/*' | xargs grep -l '\bsorry\b' | sort)

# Launch one prover per file in parallel
for file in $SORRY_FILES; do
    rel=$(python3 -c "import os; print(os.path.relpath('$file', '<project>'))")
    claude -p "You are a prover agent for project '<name>'. Current stage: prover.
...
Your assigned file: $rel
You own ONLY this file. Do NOT edit any other .lean file.
Write your results to .archon/task_results/${rel}.md when done." \
        --dangerously-skip-permissions --permission-mode bypassPermissions &
done
wait
```

Each prover writes to its own `task_results/<file>.md`. The orchestrator then runs Plan to collect all results.

---

## 8. Monitoring and Heartbeat

The orchestrator should implement a heartbeat loop:

### What to Check

| Check | How | Frequency |
|-------|-----|-----------|
| Process alive | `ps aux \| grep "claude -p"` | Every 5 min |
| Sorry count changing | `grep -r sorry *.lean` before/after | After each prover |
| Log growing | `wc -l <log-file>` | Every 10 min |
| Results written | `ls .archon/task_results/` | After prover finishes |

### What NOT to Do

- Do not restart Claude Code because context is running low — it handles compaction internally
- Do not send multiple messages to a running `claude -p` — it's non-interactive, single-prompt
- Do not restart after a single observation of no progress — allow at least 90 minutes before concluding a session is stuck
- Do not run `lake build` — use MCP diagnostics or `lake env lean <file>` for compilation checks

---

## 9. Logging

Each `claude -p` call can produce structured logs:

```bash
claude -p "<prompt>" \
    --dangerously-skip-permissions --permission-mode bypassPermissions \
    --verbose --output-format stream-json \
    2>/dev/null | tee <log-file>
```

The log file can then be fed to `extract-attempts.py` for the review stage.

---

## 10. Complete Example: One Iteration

```bash
PROJECT=/path/to/lean-project
ARCHON=/path/to/Archon
STATE=$PROJECT/.archon
STAGE=$(awk '/^## Current Stage/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit}' $STATE/PROGRESS.md)
LOG=$STATE/logs/archon-$(date +%Y%m%d-%H%M%S).jsonl

# 1. Plan
cd $PROJECT
claude -p "You are the plan agent for project '$(basename $PROJECT)'. Current stage: $STAGE.
Project directory: $PROJECT
Project state directory: $STATE
Read $STATE/CLAUDE.md for your role, then read $STATE/prompts/plan.md and $STATE/PROGRESS.md.
All state files are in $STATE/. The .lean files are in $PROJECT/." \
    --dangerously-skip-permissions --permission-mode bypassPermissions \
    --verbose --output-format stream-json 2>/dev/null > $LOG

# 2. Prover
STAGE=$(awk '/^## Current Stage/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit}' $STATE/PROGRESS.md)
claude -p "You are the prover agent for project '$(basename $PROJECT)'. Current stage: $STAGE.
Project directory: $PROJECT
Project state directory: $STATE
Read $STATE/CLAUDE.md for your role, then read $STATE/prompts/prover-${STAGE}.md and $STATE/PROGRESS.md.
All state files are in $STATE/. The .lean files are in $PROJECT/." \
    --dangerously-skip-permissions --permission-mode bypassPermissions \
    --verbose --output-format stream-json 2>/dev/null >> $LOG

# 3. Review
SESSION_NUM=$(ls -d $STATE/proof-journal/sessions/session_* 2>/dev/null | wc -l)
SESSION_NUM=$((SESSION_NUM + 1))
mkdir -p $STATE/proof-journal/sessions/session_$SESSION_NUM $STATE/proof-journal/current_session

python3 $ARCHON/scripts/extract-attempts.py $LOG $STATE/proof-journal/current_session/attempts_raw.jsonl

claude -p "You are the review agent for project '$(basename $PROJECT)'. Current stage: $STAGE.
Project directory: $PROJECT
Project state directory: $STATE
Read $STATE/CLAUDE.md for your role, then read $STATE/prompts/review.md.
Session number: $SESSION_NUM.
Pre-processed attempt data: $STATE/proof-journal/current_session/attempts_raw.jsonl (READ THIS FIRST).
Prover log: $LOG
Write your output to: $STATE/proof-journal/sessions/session_$SESSION_NUM/" \
    --dangerously-skip-permissions --permission-mode bypassPermissions

python3 $ARCHON/scripts/validate-review.py $STATE/proof-journal/sessions/session_$SESSION_NUM $STATE/proof-journal/current_session/attempts_raw.jsonl

# 4. Check if done
NEW_STAGE=$(awk '/^## Current Stage/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit}' $STATE/PROGRESS.md)
echo "Stage after iteration: $NEW_STAGE"
```

---

## 11. Key Operational Rules

1. **One message per session** — `claude -p` takes exactly one prompt and runs to completion. You cannot inject follow-up messages.
2. **Read state, then decide** — always read `PROGRESS.md`, `task_results/`, and `PROJECT_STATUS.md` before choosing the next stage.
3. **Hints are your steering wheel** — write to `USER_HINTS.md` to redirect the plan agent. This is the primary way the orchestrator influences strategy.
4. **Review is your eyes** — `PROJECT_STATUS.md` and `proof-journal/` are how the orchestrator understands what happened. Always run review after prover.
5. **Don't fight Claude Code** — if the model gives up on a theorem, don't re-run the same prompt. Write hints with alternative strategies, decompose the problem, or provide informal proof sketches.
6. **Sorry count is ground truth** — don't trust agent self-reports. Always verify via `grep`.
7. **Patience** — a single prover session can run for hours. This is normal for project-level formalization. Only intervene after 90+ minutes of zero progress (verified by log growth, not thinking time).

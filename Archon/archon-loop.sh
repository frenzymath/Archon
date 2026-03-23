#!/usr/bin/env bash
set -euo pipefail

# Ctrl+C exits the entire script, not just the current iteration
trap 'echo ""; err "Interrupted by user."; exit 130' INT

# ============================================================
#  Archon Loop — Ralph-style dual-agent loop for Lean4
#
#  Two agents alternate: plan and prover.
#  Each iteration = fresh Claude Code context.
#  State persists only via PROGRESS.md and git history.
#
#  User interaction (no need to stop the loop):
#    Edit PROGRESS.md while the loop runs:
#    - USER_HINTS.md             → plan agent reads strategic hints
#    - /- USER: ... -/ in .lean  → prover sees file-specific hints
#
#  Parallel mode (default): prover iterations use Claude Code
#  agent teams (--teammate-mode tmux), one prover per sorry-file.
#  Each teammate runs in a tmux window — attach to monitor.
#  Use --serial to disable.
#
#  Logging: all output is tee'd to logs/archon-<timestamp>.log
#  Monitor from another terminal: tail -f Archon/logs/archon-*.readable.log
#
#  Completion detection: `claude -p` runs a single conversation
#  and exits when finished. The process exit is the signal that
#  Claude has completed its work for this iteration.
# ============================================================

# -- Defaults --
MAX_ITERATIONS=50
FORCE_STAGE=""
DRY_RUN=false
PARALLEL=true
VERBOSE_LOGS=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROGRESS_FILE="${SCRIPT_DIR}/PROGRESS.md"
LOG_DIR="${SCRIPT_DIR}/logs"

# -- Color helpers --
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[ARCHON]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ARCHON]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[ARCHON]${NC}  $*"; }
err()   { echo -e "${RED}[ARCHON]${NC}  $*"; }

# -- Parse CLI args --
while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-iterations) MAX_ITERATIONS="$2"; shift 2 ;;
        --stage)          FORCE_STAGE="$2";    shift 2 ;;
        --dry-run)        DRY_RUN=true;        shift   ;;
        --serial)         PARALLEL=false;      shift   ;;
        --verbose-logs)   VERBOSE_LOGS=true;   shift   ;;
        -h|--help)
            echo "Usage: archon-loop.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --max-iterations N   Max loop iterations (default: 50)"
            echo "  --stage STAGE        Override stage (autoformalize|prover|polish)"
            echo "  --serial             Use a single prover (default: parallel, one per sorry-file)"
            echo "  --verbose-logs       Generate raw JSON + readable logs (default: readable only)"
            echo "  --dry-run            Print prompts without launching Claude"
            echo "  -h, --help           Show this help"
            echo ""
            echo "User interaction (while the loop runs):"
            echo "  USER_HINTS.md           → strategic hints (plan agent reads)"
            echo "  /- USER: ... -/ in .lean → file-specific hints (prover reads)"
            echo ""
            echo "Monitoring:"
            echo "  tail -f Archon/logs/archon-*.readable.log"
            exit 0
            ;;
        *) err "Unknown option: $1"; exit 1 ;;
    esac
done

# ============================================================
#  Helper functions (defined before logging so init can use them)
# ============================================================

read_stage() {
    if [[ -n "$FORCE_STAGE" ]]; then
        echo "$FORCE_STAGE"
        return
    fi
    if [[ ! -f "$PROGRESS_FILE" ]]; then
        err "PROGRESS.md not found at $PROGRESS_FILE"
        exit 1
    fi
    local stage
    stage=$(awk '/^## Current Stage/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit}' "$PROGRESS_FILE")
    if [[ -z "$stage" ]]; then
        err "Could not read current stage from PROGRESS.md"
        exit 1
    fi
    echo "$stage"
}

is_complete() {
    [[ -f "$PROGRESS_FILE" ]] || return 1
    local stage
    stage=$(read_stage)
    [[ "$stage" == "COMPLETE" ]]
}

build_prompt() {
    local agent="$1"
    local stage="$2"

    if [[ "$agent" == "plan" ]]; then
        cat <<EOF
You are the plan agent. Current stage: ${stage}.
Read CLAUDE.md for your role, then read .claude/prompts/plan.md and PROGRESS.md.
EOF
    else
        cat <<EOF
You are the prover agent. Current stage: ${stage}.
Read CLAUDE.md for your role, then read .claude/prompts/prover-${stage}.md and PROGRESS.md.
EOF
    fi
}

# ============================================================
#  Run claude -p with stream-json logging
#  Logs full JSON events to log file, extracts readable text
#  and tool names for terminal display.
# ============================================================

# Usage: run_claude "prompt" [extra claude flags...]
run_claude() {
    local prompt="$1"
    shift
    local log_base="${LOG_BASE:-}"

    if [[ -n "$log_base" ]]; then
        local readable_log="${log_base}.readable.log"
        local raw_log="${log_base}.log"
        local verbose="${VERBOSE_LOGS:-false}"
        local stderr_dest="/dev/null"
        [[ "$verbose" == "true" ]] && stderr_dest="$raw_log"
        # Pipe entire stream through a single python3 process
        claude -p "$prompt" \
            --dangerously-skip-permissions --permission-mode bypassPermissions \
            --verbose --output-format stream-json \
            "$@" 2>>"$stderr_dest" | python3 -u -c "
import sys, json

VERBOSE = '$verbose' == 'true'
LOG = open('$raw_log', 'a') if VERBOSE else None
READABLE = open('$readable_log', 'a')

def log(s):
    \"\"\"Write to readable log only.\"\"\"
    READABLE.write(s + '\n')
    READABLE.flush()

def terminal(s):
    \"\"\"Write to terminal only.\"\"\"
    print(s, flush=True)

last_result = ''

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    # Log raw JSON (verbose mode only)
    if LOG:
        LOG.write(line + '\n')
        LOG.flush()

    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        continue

    t = obj.get('type', '')

    # --- Assistant output ---
    if t == 'assistant' and 'message' in obj:
        msg = obj['message']
        if not isinstance(msg, dict):
            continue
        for block in msg.get('content', []):
            bt = block.get('type', '')
            if bt == 'thinking':
                thinking = block.get('thinking', '').strip()
                if thinking:
                    log(f'[THINKING] {thinking}')
            elif bt == 'text':
                text = block.get('text', '').strip()
                if text:
                    log(f'')
                    log(f'[OUTPUT] {text}')
                    log(f'')
                    last_result = text
            elif bt == 'tool_use':
                name = block.get('name', '?')
                inp = block.get('input', {})
                if name == 'Read':
                    fp = inp.get('file_path', '?')
                    log(f'[INPUT] Read {fp}')
                elif name == 'Edit':
                    fp = inp.get('file_path', '?')
                    log(f'[INPUT] Edit {fp}')
                    log(f'  old: {inp.get(\"old_string\", \"\")}')
                    log(f'  new: {inp.get(\"new_string\", \"\")}')
                elif name == 'Write':
                    fp = inp.get('file_path', '?')
                    c = inp.get('content', '')
                    log(f'[INPUT] Write {fp} ({len(c)} chars)')
                    log(f'{c}')
                elif name == 'Bash':
                    log(f'[INPUT] Bash: {inp.get(\"command\", \"?\")}')
                elif name == 'Grep':
                    log(f'[INPUT] Grep {inp.get(\"pattern\", \"?\")} in {inp.get(\"path\", \".\")}')
                elif name == 'Glob':
                    log(f'[INPUT] Glob {inp.get(\"pattern\", \"?\")}')
                elif name == 'Agent':
                    log(f'[INPUT] Agent: {inp.get(\"description\", \"?\")}')
                else:
                    log(f'[INPUT] {name}: {inp}')

    # --- Tool results (what came back) ---
    elif t == 'user' and 'message' in obj:
        msg = obj['message']
        if not isinstance(msg, dict):
            continue
        for block in msg.get('content', []):
            if block.get('type') == 'tool_result':
                content = block.get('content', '')
                if isinstance(content, str) and content.strip():
                    log(f'[OUTPUT] {content}')
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get('type') == 'text':
                            log(f'[OUTPUT] {part.get(\"text\", \"\")}')

    # --- Session end ---
    elif t == 'result':
        cost = obj.get('cost_usd', 0)
        duration = obj.get('duration_ms', 0)
        turns = obj.get('num_turns', 0)
        result = obj.get('result', '')
        summary = result if isinstance(result, str) and result else last_result
        # Terminal: just the final summary
        if summary:
            terminal(summary)
        # Log: full session stats
        log(f'')
        log(f'--- SESSION END ---')
        if cost: log(f'  Cost: \${cost:.2f}')
        if duration: log(f'  Duration: {duration/60000:.1f} min')
        if turns: log(f'  Turns: {turns}')
        if isinstance(result, str) and result:
            log(f'  Summary: {result}')
        log(f'')

if LOG: LOG.close()
READABLE.close()
" || true
        return 0
    else
        claude -p "$prompt" \
            --dangerously-skip-permissions --permission-mode bypassPermissions \
            "$@"
    fi
}

# Portable relative path (macOS lacks realpath --relative-to)
relpath() {
    python3 -c "import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))" "$1" "$2" 2>/dev/null \
        || echo "$1"
}

# Find project .lean files containing sorry (one per line)
# Excludes .lake/, lake-packages/, and hidden directories
find_sorry_files() {
    find "${SCRIPT_DIR}" \
        -path '*/.lake' -prune -o \
        -path '*/lake-packages' -prune -o \
        -path '*/.*' -prune -o \
        -name '*.lean' -print 2>/dev/null \
        | xargs grep -l '\bsorry\b\|· sorry\|·sorry' 2>/dev/null \
        | sort -u || true
}

# Extract files listed in PROGRESS.md Current Objectives (numbered list with **File.lean**)
parse_objective_files() {
    awk '/^## Current Objectives/,/^## /' "$PROGRESS_FILE" \
        | grep -oE '\*\*[^*]+\.lean\*\*' \
        | sed 's/\*\*//g' \
        | while IFS= read -r f; do
            # Resolve relative path to absolute
            local found
            found=$(find "${SCRIPT_DIR}" -path "*/$f" -not -path '*/.lake/*' -not -path '*/lake-packages/*' 2>/dev/null | head -1)
            [[ -n "$found" ]] && echo "$found"
        done \
        | sort -u
}

# ============================================================
#  Parallel prover iteration
#  Uses Claude Code agent teams (--teammate-mode tmux).
#  One prover per sorry-file. Claude Code manages the tmux
#  windows internally — we don't create them ourselves.
# ============================================================

run_parallel_provers() {
    local stage="$1"

    # Archive old results before clearing, so nothing is lost
    local results_dir="${SCRIPT_DIR}/task_results"
    if ls "${results_dir}/"*.md &>/dev/null; then
        local archive="${LOG_DIR}/task_results-$(date +%Y%m%d-%H%M%S)"
        mkdir -p "$archive"
        mv "${results_dir}/"*.md "$archive/"
        info "Archived previous task_results/ to ${archive}"
    fi

    # First try: use files from plan agent's objectives in PROGRESS.md
    # Fall back: scan project for sorry files
    local sorry_files
    sorry_files=$(parse_objective_files)

    if [[ -z "$sorry_files" ]]; then
        info "No files in PROGRESS.md objectives — falling back to sorry scan"
        sorry_files=$(find_sorry_files)
    fi

    if [[ -z "$sorry_files" ]]; then
        info "No files with sorry found. Skipping parallel prover iteration."
        return 0
    fi

    local file_count
    file_count=$(echo "$sorry_files" | wc -l | tr -d ' ')
    info "Found ${file_count} file(s) with sorry — launching parallel provers"

    local prover_prompt_base
    prover_prompt_base=$(cat <<EOF
You are a prover agent. Current stage: ${stage}.
Read CLAUDE.md for your role, then read .claude/prompts/prover-${stage}.md and PROGRESS.md.
Check your .lean file for /- USER: ... -/ comments for file-specific hints.

IMPORTANT:
- You own ONLY the file assigned below. Do NOT edit any other .lean file.
- Write your results to task_results/<your_file>.md when done (see prover-prover.md for format).
- Do NOT edit PROGRESS.md, task_pending.md, or task_done.md.
- Missing Mathlib infrastructure is NEVER a valid reason to leave a sorry. If Mathlib lacks a theorem, implement it yourself, find a detour, or use the informal agent to find an alternative proof path. You are capable of writing Mathlib-level lemmas.
EOF
    )

    # Build file list for the monitor prompt
    local file_list=""
    while IFS= read -r f; do
        local rel
        rel=$(relpath "$f" "$SCRIPT_DIR")
        file_list="${file_list}  - ${rel}"$'\n'
    done <<< "$sorry_files"

    local monitor_prompt
    monitor_prompt=$(cat <<EOF
You are the monitor agent for a parallel prover round. Current stage: ${stage}.
You do NOT write proofs or edit .lean files. Your only job is to supervise ${file_count} prover teammates.

Teammates and their assigned files:
${file_list}
Each teammate writes results to task_results/<file>.md when done.

task_results/ has been cleared before this round. Any file that appears is from the current run.

YOUR RESPONSIBILITIES:
1. Wait for ALL ${file_count} teammates to finish. Check task_results/ periodically to see which result files have appeared. Match by filename — you know exactly which files to expect.
2. Do NOT exit until all ${file_count} expected result files exist in task_results/. Keep checking.
3. If a teammate seems stuck (much longer than others), note it but keep waiting.
4. Once all results are in, collect them:
   - Read each task_results/<file>.md
   - Update task_pending.md with attempt results
   - Migrate resolved theorems to task_done.md
   - Update PROGRESS.md with a summary of what was accomplished
   - Set Next Agent in PROGRESS.md (plan if re-planning needed, prover if more work remains)
5. Do NOT use TeamDelete.
6. Do NOT edit any .lean files.
EOF
    )

    if [[ "$DRY_RUN" == true ]]; then
        echo "=== Monitor (lead) ==="
        echo "$monitor_prompt"
        echo ""
        while IFS= read -r f; do
            local rel
            rel=$(relpath "$f" "$SCRIPT_DIR")
            echo "=== Prover teammate: ${rel} ==="
            echo "${prover_prompt_base}"
            echo "Your assigned file: ${rel}"
            echo ""
        done <<< "$sorry_files"
        return 0
    fi

    # Enable agent teams
    export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1

    # Build teammate args: one --teammate-mode tmux per file
    local teammate_args=()
    while IFS= read -r f; do
        local rel
        rel=$(relpath "$f" "$SCRIPT_DIR")
        teammate_args+=("--teammate-mode" "tmux" "-p" "${prover_prompt_base}"$'\n'"Your assigned file: ${rel}")
    done <<< "$sorry_files"

    info "Monitor (lead) supervising ${file_count} prover teammate(s)"
    info ""
    info "Watch progress from another terminal:"
    info "  tail -f ${LOG_DIR}/archon-*.readable.log    # monitor dialogue"
    info "  watch -n10 'ls -lt task_results/'            # prover results"
    info ""

    run_claude "$monitor_prompt" "${teammate_args[@]}" || true
}

# ============================================================
#  Main loop
# ============================================================

cd "$SCRIPT_DIR"

# -- Pre-flight: verify Claude Code is available and authenticated --
if [[ "$DRY_RUN" != true ]]; then
    if ! command -v claude &>/dev/null; then
        err "Claude Code is not installed. Run setup.sh first."
        exit 1
    fi
    # Quick check: run a trivial prompt to verify authentication
    if ! claude -p "reply with OK" --no-session-persistence &>/dev/null; then
        err "Claude Code cannot run. Possible causes:"
        err "  - Not logged in (run: claude auth)"
        err "  - No API key set (set ANTHROPIC_API_KEY)"
        err "  - Network issue"
        exit 1
    fi
    ok "Claude Code is authenticated and ready"
fi

# ============================================================
#  Stage: init — interactive setup
#  Runs BEFORE logging setup so Claude gets a clean terminal.
#  After init, the user re-runs the script to start the loop.
# ============================================================

STAGE=$(read_stage)

if [[ "$STAGE" == "init" ]]; then
    info "═══════════════════════════════════════════════"
    info "Stage: init — Interactive project setup"
    info "═══════════════════════════════════════════════"
    info "Claude will check the project state and guide you through setup."
    info "When done, Claude will update PROGRESS.md to the next stage."
    info "Then re-run this script to start the automated loop."
    echo ""

    if [[ "$DRY_RUN" == true ]]; then
        echo "Would launch: claude (interactive)"
        exit 0
    fi

    claude "You are in the init stage. Read CLAUDE.md, then read .claude/prompts/init.md and follow its instructions." || true

    # Check if init completed (stage changed)
    NEW_STAGE=$(read_stage)
    if [[ "$NEW_STAGE" == "init" ]]; then
        warn "Stage is still 'init'. Setup may not be complete."
        warn "Re-run: ./archon-loop.sh"
    else
        ok "Setup complete. Stage is now: ${NEW_STAGE}"
        ok "Re-run to start the automated loop: ./archon-loop.sh"
    fi
    exit 0
fi

# ============================================================
#  Logging setup
#  Shell messages go to both terminal and log via log() wrapper.
#  Claude output goes to log via run_claude's stream-json parser.
# ============================================================

LOG_FILE=""
LOG_BASE=""
if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$LOG_DIR" "${SCRIPT_DIR}/task_results"
    LOG_BASE="${LOG_DIR}/archon-$(date +%Y%m%d-%H%M%S)"
    LOG_FILE="${LOG_BASE}.log"
fi

# Redefine helpers to append to readable log (always) and raw log (verbose only)
_log() {
    local ts
    ts="[$(date +%H:%M:%S)]"
    [[ -n "${LOG_BASE:-}" ]] && echo "$ts $*" >> "${LOG_BASE}.readable.log" || true
    [[ "$VERBOSE_LOGS" == true && -n "${LOG_BASE:-}" ]] && echo "$ts $*" >> "${LOG_BASE}.log" || true
}
info()  { echo -e "${CYAN}[ARCHON]${NC}  $*"; _log "[INFO]  $*"; }
ok()    { echo -e "${GREEN}[ARCHON]${NC}  $*"; _log "[OK]    $*"; }
warn()  { echo -e "${YELLOW}[ARCHON]${NC}  $*"; _log "[WARN]  $*"; }
err()   { echo -e "${RED}[ARCHON]${NC}  $*"; _log "[ERROR] $*"; }

info "Archon Loop starting"
info "Max iterations: ${MAX_ITERATIONS}"
info "Working directory: ${SCRIPT_DIR}"
[[ -n "$FORCE_STAGE" ]] && info "Forced stage: ${FORCE_STAGE}"
[[ "$PARALLEL" == true ]] && info "Prover mode: parallel (agent teams)"
[[ "$PARALLEL" != true ]] && info "Prover mode: serial (single prover)"
[[ "$DRY_RUN" == true ]] && warn "DRY RUN mode"
[[ -n "$LOG_BASE" ]] && info "Readable log: ${LOG_BASE}.readable.log"
[[ "$VERBOSE_LOGS" == true && -n "$LOG_BASE" ]] && info "Verbose log: ${LOG_BASE}.log (raw JSON)" || true
info ""
info "To guide agents while the loop runs:"
info "  - Edit USER_HINTS.md          → strategic hints (plan agent reads)"
info "  - Add /- USER: ... -/ in .lean → file-specific hints (prover reads)"
echo ""

# ============================================================
#  Stage: COMPLETE — nothing to do
# ============================================================

if is_complete; then
    ok "PROGRESS.md says COMPLETE. Nothing to do."
    exit 0
fi

# ============================================================
#  Automated loop: plan → prover → plan → prover → ...
#  Each iteration = one plan round + one prover round.
#  Plan always runs first to collect results and set objectives.
# ============================================================

STAGE=$(read_stage)
info "Stage: ${STAGE} — Starting automated loop"
echo ""

for (( i=0; i<MAX_ITERATIONS; i++ )); do
    STAGE=$(read_stage)

    if is_complete; then
        ok "PROGRESS.md says COMPLETE. Exiting loop."
        exit 0
    fi

    # --- Plan phase ---
    info "════════════════════════════════════════"
    info "Iteration $((i+1))/${MAX_ITERATIONS}  |  Stage: ${STAGE}"
    info "════════════════════════════════════════"

    info "Phase 1: Plan agent"
    info "────────────────────────────────────────"

    PLAN_PROMPT=$(build_prompt "plan" "$STAGE")
    if [[ "$DRY_RUN" == true ]]; then
        echo "$PLAN_PROMPT"
        echo ""
    else
        run_claude "$PLAN_PROMPT" || true
    fi

    info "Plan phase finished."
    echo ""

    # Re-check after plan (it may have advanced to COMPLETE)
    if is_complete; then
        ok "PROGRESS.md says COMPLETE. Exiting loop."
        exit 0
    fi

    STAGE=$(read_stage)

    # --- Prover phase ---
    info "Phase 2: Prover agent(s)"
    [[ "$PARALLEL" == true ]] && info "Mode: parallel (agent teams)"
    info "────────────────────────────────────────"

    if [[ "$PARALLEL" == true ]]; then
        run_parallel_provers "$STAGE" || true
    else
        PROVER_PROMPT=$(build_prompt "prover" "$STAGE")
        if [[ "$DRY_RUN" == true ]]; then
            echo "$PROVER_PROMPT"
            echo ""
        else
            run_claude "$PROVER_PROMPT" || true
        fi
    fi

    info "Prover phase finished."
    info "Iteration $((i+1)) complete."
    echo ""
done

warn "Reached max iterations (${MAX_ITERATIONS}). Stopping."

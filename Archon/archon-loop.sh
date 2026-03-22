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
#    - "User Hints (Global)"     → all agents see it next iteration
#    - "User Hints (Plan Agent)" → only plan agent sees it
#
#  Parallel mode (default): prover iterations use Claude
#  agent teams (--teammate-mode tmux), one prover per sorry-file.
#  Use --serial to disable. Requires: CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
#
#  Logging: all output is tee'd to logs/archon-<timestamp>.log
#  Monitor from another terminal: tail -f Archon/logs/archon-*.log
#
#  Completion detection: `claude -p` runs a single conversation
#  and exits when finished. The process exit is the signal that
#  Claude has completed its work for this iteration.
# ============================================================

# -- Defaults --
MAX_ITERATIONS=50
FORCE_AGENT=""
FORCE_STAGE=""
DRY_RUN=false
PARALLEL=true
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
        --agent)          FORCE_AGENT="$2";    shift 2 ;;
        --stage)          FORCE_STAGE="$2";    shift 2 ;;
        --dry-run)        DRY_RUN=true;        shift   ;;
        --serial)         PARALLEL=false;      shift   ;;
        -h|--help)
            echo "Usage: archon-loop.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --max-iterations N   Max loop iterations (default: 50)"
            echo "  --agent plan|prover  Force a specific agent (default: alternate)"
            echo "  --stage STAGE        Override stage (autoformalize|prover|polish)"
            echo "  --serial             Use a single prover (default: parallel, one per sorry-file)"
            echo "  --dry-run            Print prompts without launching Claude"
            echo "  -h, --help           Show this help"
            echo ""
            echo "User interaction (while the loop runs):"
            echo "  Edit PROGRESS.md → 'User Hints (Global)' for all agents"
            echo "  Edit PROGRESS.md → 'User Hints (Plan Agent)' for plan agent only"
            echo ""
            echo "Monitoring:"
            echo "  tail -f Archon/logs/archon-*.log"
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

get_agent() {
    if [[ -n "$FORCE_AGENT" ]]; then
        echo "$FORCE_AGENT"
        return
    fi
    # Read "Next Agent" from PROGRESS.md, default to "plan"
    local agent
    agent=$(awk '/^## Next Agent/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit}' "$PROGRESS_FILE")
    if [[ "$agent" == "plan" || "$agent" == "prover" ]]; then
        echo "$agent"
    else
        echo "plan"
    fi
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

STREAM_PARSER='
import sys, json

def truncate(s, n=500):
    s = str(s)
    return s[:n] + "..." if len(s) > n else s

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        continue

    t = obj.get("type", "")

    # --- Assistant turn: text + tool calls ---
    if t == "assistant" and "message" in obj:
        msg = obj["message"]
        if not isinstance(msg, dict):
            continue
        for block in msg.get("content", []):
            btype = block.get("type", "")
            if btype == "text":
                text = block.get("text", "").strip()
                if text:
                    print(f"\n[CLAUDE]\n{text}\n", flush=True)
            elif btype == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                if name == "Read":
                    print(f"[TOOL] Read {inp.get(\"file_path\", \"?\")}", flush=True)
                elif name == "Edit":
                    fp = inp.get("file_path", "?")
                    old = truncate(inp.get("old_string", ""), 80)
                    new = truncate(inp.get("new_string", ""), 80)
                    print(f"[TOOL] Edit {fp}", flush=True)
                    print(f"  old: {old}", flush=True)
                    print(f"  new: {new}", flush=True)
                elif name == "Write":
                    fp = inp.get("file_path", "?")
                    content = inp.get("content", "")
                    print(f"[TOOL] Write {fp} ({len(content)} chars)", flush=True)
                elif name == "Bash":
                    cmd = truncate(inp.get("command", "?"), 200)
                    print(f"[TOOL] Bash: {cmd}", flush=True)
                elif name == "Grep":
                    print(f"[TOOL] Grep {inp.get(\"pattern\", \"?\")} in {inp.get(\"path\", \".\")}", flush=True)
                elif name == "Glob":
                    print(f"[TOOL] Glob {inp.get(\"pattern\", \"?\")}", flush=True)
                elif name == "Agent":
                    print(f"[TOOL] Agent: {inp.get(\"description\", \"?\")}", flush=True)
                else:
                    print(f"[TOOL] {name}: {truncate(inp, 120)}", flush=True)

    # --- Tool results (user turn with tool_result) ---
    elif t == "user" and "message" in obj:
        msg = obj["message"]
        if not isinstance(msg, dict):
            continue
        for block in msg.get("content", []):
            if block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, str) and content.strip():
                    print(f"[RESULT] {truncate(content, 300)}", flush=True)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            print(f"[RESULT] {truncate(part.get(\"text\", \"\"), 300)}", flush=True)

    # --- Session result ---
    elif t == "result":
        cost = obj.get("cost_usd", 0)
        duration = obj.get("duration_ms", 0)
        turns = obj.get("num_turns", 0)
        result = obj.get("result", "")
        print(f"\n--- SESSION END ---", flush=True)
        if cost: print(f"  Cost: ${cost:.2f}", flush=True)
        if duration: print(f"  Duration: {duration/60000:.1f} min", flush=True)
        if turns: print(f"  Turns: {turns}", flush=True)
        if isinstance(result, str) and result:
            print(f"  Summary: {truncate(result, 500)}", flush=True)
        print(flush=True)
'

run_claude() {
    local prompt="$1"
    shift
    # Remaining args are extra claude flags (e.g. teammate args)

    if [[ -n "${LOG_FILE:-}" ]]; then
        local readable_log="${LOG_FILE%.log}.readable.log"
        claude -p "$prompt" \
            --dangerously-skip-permissions --permission-mode bypassPermissions \
            --verbose --output-format stream-json \
            "$@" 2>>"$LOG_FILE" | while IFS= read -r line; do
            # Log raw JSON event
            echo "$line" >> "$LOG_FILE"
            # Parse and display readable content
            local parsed
            parsed=$(echo "$line" | python3 -c "$STREAM_PARSER" 2>/dev/null) || true
            if [[ -n "$parsed" ]]; then
                echo "$parsed"
                echo "$parsed" >> "$readable_log"
            fi
        done
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

    # Enable agent teams
    export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1

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

    # Build the file list for the plan agent prompt
    local file_list=""
    while IFS= read -r f; do
        local rel
        rel=$(relpath "$f" "$SCRIPT_DIR")
        file_list="${file_list}  - ${rel}"$'\n'
    done <<< "$sorry_files"

    local plan_prompt
    plan_prompt=$(cat <<EOF
You are the plan agent in parallel mode. Current stage: ${stage}.
Read CLAUDE.md for your role, then read .claude/prompts/plan.md and PROGRESS.md.

You have ${file_count} prover teammate(s) working in parallel — one per file.
Each teammate has exclusive ownership of its file. Do NOT edit those files yourself.

Files being worked on by teammates:
${file_list}
Your job: coordinate, update PROGRESS.md, and monitor results.
When all teammates finish, commit the combined work.
EOF
    )

    local prover_prompt_base
    prover_prompt_base=$(cat <<EOF
You are a prover teammate agent. Current stage: ${stage}.
Read CLAUDE.md for your role, then read .claude/prompts/prover-${stage}.md and PROGRESS.md.
Check User Hints (Global) in PROGRESS.md for any user guidance.

IMPORTANT: You own ONLY the file assigned below. Do NOT edit any other .lean file.
EOF
    )

    if [[ "$DRY_RUN" == true ]]; then
        echo "=== Plan agent (lead) ==="
        echo "$plan_prompt"
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

    # Launch lead plan agent with teammates
    # Build teammate args: one --teammate-mode tmux per prover file
    local teammate_args=()
    while IFS= read -r f; do
        local rel
        rel=$(relpath "$f" "$SCRIPT_DIR")
        teammate_args+=("--teammate-mode" "tmux" "-p" "${prover_prompt_base}"$'\n'"Your assigned file: ${rel}")
    done <<< "$sorry_files"

    info "Launching plan agent (lead) with ${file_count} prover teammate(s)..."
    run_claude "$plan_prompt" "${teammate_args[@]}" || true
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
if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$LOG_DIR"
    LOG_FILE="${LOG_DIR}/archon-$(date +%Y%m%d-%H%M%S).log"
fi

# Redefine helpers to also append to both log files
_log() {
    local ts
    ts="[$(date +%H:%M:%S)]"
    [[ -n "${LOG_FILE:-}" ]] && echo "$ts $*" >> "$LOG_FILE"
    [[ -n "${LOG_FILE:-}" ]] && echo "$ts $*" >> "${LOG_FILE%.log}.readable.log"
}
info()  { echo -e "${CYAN}[ARCHON]${NC}  $*"; _log "[INFO]  $*"; }
ok()    { echo -e "${GREEN}[ARCHON]${NC}  $*"; _log "[OK]    $*"; }
warn()  { echo -e "${YELLOW}[ARCHON]${NC}  $*"; _log "[WARN]  $*"; }
err()   { echo -e "${RED}[ARCHON]${NC}  $*"; _log "[ERROR] $*"; }

info "Archon Loop starting"
info "Max iterations: ${MAX_ITERATIONS}"
info "Working directory: ${SCRIPT_DIR}"
[[ -n "$FORCE_AGENT" ]] && info "Forced agent: ${FORCE_AGENT}"
[[ -n "$FORCE_STAGE" ]] && info "Forced stage: ${FORCE_STAGE}"
[[ "$PARALLEL" == true ]] && info "Prover mode: parallel (agent teams)"
[[ "$PARALLEL" != true ]] && info "Prover mode: serial (single prover)"
[[ "$DRY_RUN" == true ]] && warn "DRY RUN mode"
[[ -n "$LOG_FILE" ]] && info "Log file: ${LOG_FILE}"
info ""
info "To guide agents while the loop runs, edit PROGRESS.md:"
info "  - 'User Hints (Global)'      → all agents read this"
info "  - 'User Hints (Plan Agent)'   → only plan agent reads this"
echo ""

# ============================================================
#  Stage: COMPLETE — nothing to do
# ============================================================

if is_complete; then
    ok "PROGRESS.md says COMPLETE. Nothing to do."
    exit 0
fi

# ============================================================
#  Automated loop: plan ↔ prover alternation
#  Only runs for stages: autoformalize, prover, polish
# ============================================================

STAGE=$(read_stage)
info "Stage: ${STAGE} — Starting automated loop"
echo ""

for (( i=0; i<MAX_ITERATIONS; i++ )); do
    # Re-check stage each iteration (plan agent may advance it)
    STAGE=$(read_stage)

    if is_complete; then
        ok "PROGRESS.md says COMPLETE. Exiting loop."
        exit 0
    fi

    AGENT=$(get_agent)

    info "────────────────────────────────────────"
    info "Iteration $((i+1))/${MAX_ITERATIONS}  |  Agent: ${AGENT}  |  Stage: ${STAGE}"
    [[ "$PARALLEL" == true && "$AGENT" == "prover" ]] && info "Mode: parallel (agent teams)"
    info "────────────────────────────────────────"

    if [[ "$PARALLEL" == true && "$AGENT" == "prover" ]]; then
        # Parallel mode: launch one prover per sorry-file via agent teams
        run_parallel_provers "$STAGE" || true
    else
        PROMPT=$(build_prompt "$AGENT" "$STAGE")
        if [[ "$DRY_RUN" == true ]]; then
            echo "$PROMPT"
            echo ""
            continue
        fi
        # Launch Claude with prompt.
        run_claude "$PROMPT" || true
    fi

    info "Iteration $((i+1)) finished."
    echo ""
done

warn "Reached max iterations (${MAX_ITERATIONS}). Stopping."

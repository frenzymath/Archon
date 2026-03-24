#!/usr/bin/env bash
set -euo pipefail

# Ctrl+C exits the entire script, not just the current iteration
trap 'echo ""; err "Interrupted by user."; exit 130' INT

# ============================================================
#  Archon Loop — dual-agent loop for Lean4
#
#  Each iteration = one plan round + one prover round.
#  Plan always runs first to collect results and set objectives.
#  State persists via PROGRESS.md, task_pending.md, task_results/.
#
#  User interaction (no need to stop the loop):
#    - USER_HINTS.md             → plan agent reads strategic hints
#    - /- USER: ... -/ in .lean  → prover sees file-specific hints
#
#  Parallel mode (default): prover iterations use Claude Code
#  agent teams (--teammate-mode tmux), one prover per sorry-file.
#  Use --serial to disable.
#
#  Logging:
#    - logs/archon-*.jsonl         — structured log (one JSON event per line)
#    - logs/archon-*.raw.jsonl     — raw Claude stream (only with --verbose-logs)
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
LOG_BASE=""  # Set later in logging setup

# -- Color helpers with JSONL logging --
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
_log_jsonl() {
    if [[ -n "${LOG_BASE:-}" ]]; then
        local ts level msg escaped
        ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        level="$1"
        msg="$2"
        # Escape quotes and backslashes for JSON
        escaped="${msg//\\/\\\\}"
        escaped="${escaped//\"/\\\"}"
        echo "{\"ts\":\"${ts}\",\"event\":\"shell\",\"level\":\"${level}\",\"message\":\"${escaped}\"}" >> "${LOG_BASE}.jsonl"
    fi
    return 0
}
info()  { echo -e "${CYAN}[ARCHON]${NC}  $*"; _log_jsonl "info" "$*"; }
ok()    { echo -e "${GREEN}[ARCHON]${NC}  $*"; _log_jsonl "ok" "$*"; }
warn()  { echo -e "${YELLOW}[ARCHON]${NC}  $*"; _log_jsonl "warn" "$*"; }
err()   { echo -e "${RED}[ARCHON]${NC}  $*"; _log_jsonl "error" "$*"; }

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
            echo "  --verbose-logs       Also save raw Claude stream events to .raw.jsonl"
            echo "  --dry-run            Print prompts without launching Claude"
            echo "  -h, --help           Show this help"
            echo ""
            echo "User interaction (while the loop runs):"
            echo "  USER_HINTS.md           → strategic hints (plan agent reads)"
            echo "  /- USER: ... -/ in .lean → file-specific hints (prover reads)"
            echo ""
            echo "Monitoring:"
            echo "  tail -f workspace/logs/archon-*.jsonl"
            exit 0
            ;;
        *) err "Unknown option: $1"; exit 1 ;;
    esac
done

# ============================================================
#  Helper functions
# ============================================================

read_stage() {
    if [[ -n "$FORCE_STAGE" ]]; then
        echo "$FORCE_STAGE"
        return
    fi
    if [[ ! -f "$PROGRESS_FILE" ]]; then
        echo -e "${RED}[ARCHON]${NC}  PROGRESS.md not found at $PROGRESS_FILE" >&2
        echo -e "${RED}[ARCHON]${NC}  Create it with: echo -e '# Project Progress\n\n## Current Stage\ninit\n\n## Stages\n- [ ] init\n- [ ] autoformalize\n- [ ] prover\n- [ ] polish\n\n## Current Objectives\n' > $PROGRESS_FILE" >&2
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

# Portable relative path (macOS lacks realpath --relative-to)
relpath() {
    python3 -c "import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))" "$1" "$2" 2>/dev/null \
        || echo "$1"
}

# Find project .lean files containing sorry
find_sorry_files() {
    find "${SCRIPT_DIR}" \
        -path '*/.lake' -prune -o \
        -path '*/lake-packages' -prune -o \
        -path '*/.*' -prune -o \
        -name '*.lean' -print 2>/dev/null \
        | xargs grep -l '\bsorry\b\|· sorry\|·sorry' 2>/dev/null \
        | sort -u || true
}

# Extract files listed in PROGRESS.md Current Objectives
parse_objective_files() {
    awk '/^## Current Objectives/,/^## /' "$PROGRESS_FILE" \
        | grep -oE '\*\*[^*]+\.lean\*\*' \
        | sed 's/\*\*//g' \
        | while IFS= read -r f; do
            local found
            found=$(find "${SCRIPT_DIR}" -path "*/$f" -not -path '*/.lake/*' -not -path '*/lake-packages/*' 2>/dev/null | head -1)
            [[ -n "$found" ]] && echo "$found"
        done \
        | sort -u
}

# ============================================================
#  Run claude -p with JSONL logging
# ============================================================

run_claude() {
    local prompt="$1"
    shift
    local log_base="${LOG_BASE:-}"

    if [[ -n "$log_base" ]]; then
        local jsonl="${log_base}.jsonl"
        local raw_log="${log_base}.raw.jsonl"
        local verbose="${VERBOSE_LOGS:-false}"
        local stderr_dest="/dev/null"
        [[ "$verbose" == "true" ]] && stderr_dest="$raw_log"

        claude -p "$prompt" \
            --dangerously-skip-permissions --permission-mode bypassPermissions \
            --verbose --output-format stream-json \
            "$@" 2>>"$stderr_dest" | python3 -u -c "
import sys, json, datetime

VERBOSE = '$verbose' == 'true'
RAW = open('$raw_log', 'a') if VERBOSE else None
JSONL = open('$jsonl', 'a')

def emit(event_type, **fields):
    row = {'ts': datetime.datetime.now().isoformat(), 'event': event_type, **fields}
    JSONL.write(json.dumps(row) + '\n')
    JSONL.flush()

def terminal(s):
    print(s, flush=True)

last_result = ''

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    if RAW:
        RAW.write(line + '\n')
        RAW.flush()

    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        continue

    t = obj.get('type', '')

    if t == 'assistant' and 'message' in obj:
        msg = obj['message']
        if not isinstance(msg, dict):
            continue
        for block in msg.get('content', []):
            bt = block.get('type', '')
            if bt == 'thinking':
                thinking = block.get('thinking', '').strip()
                if thinking:
                    emit('thinking', content=thinking)
            elif bt == 'text':
                text = block.get('text', '').strip()
                if text:
                    emit('text', content=text)
                    last_result = text
            elif bt == 'tool_use':
                name = block.get('name', '?')
                inp = block.get('input', {})
                emit('tool_call', tool=name, input=inp)

    elif t == 'user' and 'message' in obj:
        msg = obj['message']
        if not isinstance(msg, dict):
            continue
        for block in msg.get('content', []):
            if block.get('type') == 'tool_result':
                content = block.get('content', '')
                if isinstance(content, str):
                    emit('tool_result', content=content)
                elif isinstance(content, list):
                    texts = [p.get('text','') for p in content if isinstance(p,dict) and p.get('type')=='text']
                    emit('tool_result', content='\n'.join(texts))

    elif t == 'result':
        cost = obj.get('total_cost_usd', 0) or obj.get('cost_usd', 0) or 0
        duration = obj.get('duration_ms', 0) or 0
        turns = obj.get('num_turns', 0) or 0
        session_id = obj.get('session_id', '') or ''
        result = obj.get('result', '')
        usage = obj.get('usage', {}) or {}
        model_usage = obj.get('modelUsage', {}) or {}
        summary = result if isinstance(result, str) and result else last_result

        emit('session_end',
            session_id=session_id,
            total_cost_usd=cost,
            duration_ms=duration,
            duration_api_ms=usage.get('duration_api_ms', 0) or 0,
            num_turns=turns,
            input_tokens=usage.get('input_tokens', 0) or 0,
            output_tokens=usage.get('output_tokens', 0) or 0,
            cache_read_input_tokens=usage.get('cache_read_input_tokens', 0) or 0,
            cache_creation_input_tokens=usage.get('cache_creation_input_tokens', 0) or 0,
            model_usage=model_usage,
            summary=summary,
        )

        if summary:
            terminal(summary)
        parts = []
        if duration:  parts.append(f'{duration/60000:.1f}min')
        if cost:      parts.append(f'\${cost:.4f}')
        if usage.get('input_tokens') or usage.get('output_tokens'):
            parts.append(f'in={usage.get(\"input_tokens\",0)} out={usage.get(\"output_tokens\",0)}')
        if turns:     parts.append(f'turns={turns}')
        if parts:
            terminal(f'[COST] {\" | \".join(parts)}')

JSONL.close()
if RAW: RAW.close()
" || true
        return 0
    else
        claude -p "$prompt" \
            --dangerously-skip-permissions --permission-mode bypassPermissions \
            "$@"
    fi
}

# ============================================================
#  Cost summary helpers
# ============================================================

show_cost_summary() {
    local label="$1"
    local offset="${2:-0}"
    local jsonl="${LOG_BASE:-}.jsonl"
    [[ -f "$jsonl" ]] || return 0
    python3 -c "
import sys, json
rows = []
for l in open('$jsonl'):
    l = l.strip()
    if not l: continue
    try:
        r = json.loads(l)
        if r.get('event') == 'session_end': rows.append(r)
    except: pass
rows = rows[${offset}:]
if not rows: sys.exit(0)
cost  = sum(r.get('total_cost_usd', 0) or 0 for r in rows)
dur   = sum(r.get('duration_ms', 0) or 0 for r in rows)
tin   = sum(r.get('input_tokens', 0) or 0 for r in rows)
tout  = sum(r.get('output_tokens', 0) or 0 for r in rows)
turns = sum(r.get('num_turns', 0) or 0 for r in rows)
models = {}
for r in rows:
    for m, u in (r.get('model_usage') or {}).items():
        if m not in models:
            models[m] = {'in': 0, 'out': 0, 'cost': 0.0}
        models[m]['in']   += u.get('inputTokens', 0) or 0
        models[m]['out']  += u.get('outputTokens', 0) or 0
        models[m]['cost'] += u.get('costUSD', 0) or 0
parts = []
if dur:   parts.append(f'{dur/60000:.1f}min')
if cost:  parts.append(f'\${cost:.4f}')
if tin or tout: parts.append(f'in={tin} out={tout}')
if turns: parts.append(f'turns={turns}')
print('$label ' + ' | '.join(parts))
for m, u in models.items():
    print(f'  {m}: in={u[\"in\"]} out={u[\"out\"]} \${u[\"cost\"]:.4f}')
" 2>/dev/null || true
}

cost_log_lines() {
    local jsonl="${LOG_BASE:-}.jsonl"
    [[ -f "$jsonl" ]] && grep -c '"event":"session_end"' "$jsonl" 2>/dev/null || echo 0
}

# ============================================================
#  Parallel prover iteration
# ============================================================

run_parallel_provers() {
    local stage="$1"

    # Archive old results before clearing
    local results_dir="${SCRIPT_DIR}/task_results"
    if ls "${results_dir}/"*.md &>/dev/null; then
        local archive="${LOG_DIR}/task_results-$(date +%Y%m%d-%H%M%S)"
        mkdir -p "$archive"
        mv "${results_dir}/"*.md "$archive/"
        info "Archived previous task_results/ to ${archive}"
    fi

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

    # Single file — run serial, no agent teams overhead
    if [[ "$file_count" -eq 1 ]]; then
        local rel
        rel=$(relpath "$(echo "$sorry_files" | head -1)" "$SCRIPT_DIR")
        info "Only 1 file (${rel}) — running serial prover"
        local single_prompt
        single_prompt=$(build_prompt "prover" "$stage")
        run_claude "$single_prompt" || true
        return 0
    fi

    info "Found ${file_count} file(s) — launching parallel provers"

    local prover_prompt_base
    prover_prompt_base=$(cat <<EOF
You are a prover agent. Current stage: ${stage}.
Read CLAUDE.md for your role, then read .claude/prompts/prover-${stage}.md and PROGRESS.md.
Check your .lean file for /- USER: ... -/ comments for file-specific hints.

IMPORTANT:
- You own ONLY the file assigned below. Do NOT edit any other .lean file.
- Write your results to task_results/<your_file>.md when done (see prover-prover.md for format).
- Do NOT edit PROGRESS.md, task_pending.md, or task_done.md.
- Missing Mathlib infrastructure is NEVER a valid reason to leave a sorry. If Mathlib lacks a theorem, implement it yourself, find a detour, or use the informal agent to find an alternative proof path.
- NEVER revert to a bare sorry. Always leave your partial proof attempt in the code (helper lemmas, commented proof steps, partial by blocks with sorry at the stuck point). The file must compile, but your work must be visible for the next agent.
EOF
    )

    # Build file list for monitor
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
1. Wait for ALL ${file_count} teammates to finish. Check task_results/ periodically to see which result files have appeared.
2. Do NOT exit until all ${file_count} expected result files exist in task_results/. Keep checking.
3. If a teammate seems stuck (much longer than others), note it but keep waiting.
4. Once all results are in, collect them:
   - Read each task_results/<file>.md
   - Update task_pending.md with attempt results
   - Migrate resolved theorems to task_done.md
   - Update PROGRESS.md with a summary of what was accomplished
5. Before exiting, run the /cost command and include the output in your final message. This is the only way we can capture the total team cost.
6. Do NOT use TeamDelete.
7. Do NOT edit any .lean files.
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

    export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1

    local teammate_args=()
    while IFS= read -r f; do
        local rel
        rel=$(relpath "$f" "$SCRIPT_DIR")
        teammate_args+=("--teammate-mode" "tmux" "-p" "${prover_prompt_base}"$'\n'"Your assigned file: ${rel}")
    done <<< "$sorry_files"

    info "Monitor supervising ${file_count} prover teammate(s)"
    info ""
    info "Watch progress from another terminal:"
    info "  tail -f ${LOG_DIR}/archon-*.jsonl"
    info "  watch -n10 'ls -lt task_results/'"
    info ""

    run_claude "$monitor_prompt" "${teammate_args[@]}" || true

    # Emit a note about parallel cost tracking
    if [[ -n "${LOG_BASE:-}" ]]; then
        python3 -c "
import json, datetime
row = {
    'ts': datetime.datetime.now().isoformat(),
    'event': 'parallel_round_end',
    'teammate_count': ${file_count},
    'note': 'session_end cost reflects monitor + teammates if Claude Code aggregates team costs. Check monitor final message for /cost output.'
}
with open('${LOG_BASE}.jsonl', 'a') as f:
    f.write(json.dumps(row) + '\n')
" 2>/dev/null || true
    fi
}

# ============================================================
#  Main
# ============================================================

cd "$SCRIPT_DIR"

# -- Pre-flight --
if [[ "$DRY_RUN" != true ]]; then
    if ! command -v claude &>/dev/null; then
        err "Claude Code is not installed. Run setup.sh first."
        exit 1
    fi
    if ! claude -p "reply with OK" --no-session-persistence &>/dev/null; then
        err "Claude Code cannot run. Possible causes:"
        err "  - Not logged in (run: claude auth)"
        err "  - No API key set (set ANTHROPIC_API_KEY)"
        err "  - Network issue"
        exit 1
    fi
    ok "Claude Code is authenticated and ready"
fi

# -- Check PROGRESS.md exists --
if [[ ! -f "$PROGRESS_FILE" ]]; then
    echo -e "${RED}[ARCHON]${NC}  PROGRESS.md not found at $PROGRESS_FILE" >&2
    echo -e "${RED}[ARCHON]${NC}  Run setup.sh first, or create it manually." >&2
    exit 1
fi

# -- Init stage (interactive, before logging) --
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

# -- Logging setup (automated loop only) --
LOG_BASE=""
if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$LOG_DIR" "${SCRIPT_DIR}/task_results"
    LOG_BASE="${LOG_DIR}/archon-$(date +%Y%m%d-%H%M%S)"
fi

info "Archon Loop starting"
info "Max iterations: ${MAX_ITERATIONS}"
info "Working directory: ${SCRIPT_DIR}"
[[ -n "$FORCE_STAGE" ]] && info "Forced stage: ${FORCE_STAGE}"
[[ "$PARALLEL" == true ]] && info "Prover mode: parallel (agent teams)"
[[ "$PARALLEL" != true ]] && info "Prover mode: serial (single prover)"
[[ "$DRY_RUN" == true ]] && warn "DRY RUN mode"
[[ -n "$LOG_BASE" ]] && info "Log: ${LOG_BASE}.jsonl"
[[ "$VERBOSE_LOGS" == true && -n "$LOG_BASE" ]] && info "Raw stream: ${LOG_BASE}.raw.jsonl" || true
info ""
info "To guide agents while the loop runs:"
info "  - Edit USER_HINTS.md          → strategic hints (plan agent reads)"
info "  - Add /- USER: ... -/ in .lean → file-specific hints (prover reads)"
echo ""

# -- COMPLETE check --
if is_complete; then
    ok "PROGRESS.md says COMPLETE. Nothing to do."
    exit 0
fi

# ============================================================
#  Automated loop: plan → prover → plan → prover → ...
# ============================================================

STAGE=$(read_stage)
info "Stage: ${STAGE} — Starting automated loop"
echo ""

LOOP_START=$SECONDS

for (( i=0; i<MAX_ITERATIONS; i++ )); do
    STAGE=$(read_stage)

    if is_complete; then
        ok "PROGRESS.md says COMPLETE. Exiting loop."
        break
    fi

    info "════════════════════════════════════════"
    info "Iteration $((i+1))/${MAX_ITERATIONS}  |  Stage: ${STAGE}"
    info "════════════════════════════════════════"

    ITER_START=$SECONDS
    ITER_COST_OFFSET=$(cost_log_lines)

    # --- Plan phase ---
    info "Phase 1: Plan agent"
    info "────────────────────────────────────────"

    PLAN_START=$SECONDS
    PLAN_PROMPT=$(build_prompt "plan" "$STAGE")
    if [[ "$DRY_RUN" == true ]]; then
        echo "$PLAN_PROMPT"
        echo ""
    else
        run_claude "$PLAN_PROMPT" || true
    fi

    PLAN_SECS=$(( SECONDS - PLAN_START ))
    info "Plan phase finished. (${PLAN_SECS}s)"
    echo ""

    if is_complete; then
        ok "PROGRESS.md says COMPLETE. Exiting loop."
        break
    fi

    STAGE=$(read_stage)

    # --- Prover phase ---
    info "Phase 2: Prover agent(s)"
    [[ "$PARALLEL" == true ]] && info "Mode: parallel (agent teams)"
    info "────────────────────────────────────────"

    PROVER_START=$SECONDS
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

    PROVER_SECS=$(( SECONDS - PROVER_START ))
    ITER_SECS=$(( SECONDS - ITER_START ))
    info "Prover phase finished. (${PROVER_SECS}s)"
    info "Iteration $((i+1)) complete. Wall time: ${ITER_SECS}s"
    show_cost_summary "  Iteration $((i+1)) totals:" "$ITER_COST_OFFSET"
    echo ""
done

LOOP_SECS=$(( SECONDS - LOOP_START ))
if ! is_complete; then
    warn "Reached max iterations (${MAX_ITERATIONS}). Stopping."
fi
info "Total wall time: ${LOOP_SECS}s"
show_cost_summary "  Loop totals:" 0

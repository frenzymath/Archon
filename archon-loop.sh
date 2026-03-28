#!/usr/bin/env bash
set -euo pipefail

trap 'echo ""; err "Interrupted by user."; exit 130' INT

# ============================================================
#  Archon Loop — dual-agent loop for Lean4
#
#  Usage:
#    ./archon-loop.sh [OPTIONS] [/path/to/lean-project]
#
#  If no path given, uses current directory.
#  Project state lives in <project>/.archon/.
#
#  Each iteration = one plan round + one prover round.
#  Plan always runs first to collect results and set objectives.
#
#  Logging: <project>/.archon/logs/archon-*.jsonl
# ============================================================

ARCHON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -- Defaults --
MAX_ITERATIONS=10
MAX_PARALLEL=8
FORCE_STAGE=""
DRY_RUN=false
PARALLEL=true
VERBOSE_LOGS=false
ENABLE_REVIEW=true
LOG_BASE=""

# -- Color helpers with JSONL logging --
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
_log_jsonl() {
    if [[ -n "${LOG_BASE:-}" ]]; then
        local ts level msg escaped
        ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
        level="$1"
        msg="$2"
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

# -- Parse CLI args (options first, then positional project path) --
PROJECT_ARG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --max-iterations) MAX_ITERATIONS="$2"; shift 2 ;;
        --max-parallel)   MAX_PARALLEL="$2";   shift 2 ;;
        --stage)          FORCE_STAGE="$2";    shift 2 ;;
        --dry-run)        DRY_RUN=true;        shift   ;;
        --serial)         PARALLEL=false;      shift   ;;
        --verbose-logs)   VERBOSE_LOGS=true;   shift   ;;
        --no-review)      ENABLE_REVIEW=false; shift   ;;
        -h|--help)
            echo "Usage: archon-loop.sh [OPTIONS] [/path/to/lean-project]"
            echo ""
            echo "If no path given, uses current directory."
            echo ""
            echo "Options:"
            echo "  --max-iterations N   Max loop iterations (default: 10)"
            echo "  --max-parallel N     Max concurrent provers in parallel mode (default: 4)"
            echo "  --stage STAGE        Override stage (autoformalize|prover|polish)"
            echo "  --serial             Use a single prover (default: parallel, one per sorry-file)"
            echo "  --verbose-logs       Also save raw Claude stream events to .raw.jsonl"
            echo "  --no-review          Skip review phase after prover"
            echo "  --dry-run            Print prompts without launching Claude"
            echo "  -h, --help           Show this help"
            echo ""
            echo "User interaction (while the loop runs):"
            echo "  Edit .archon/USER_HINTS.md in your project"
            echo "  Add /- USER: ... -/ comments in .lean files"
            exit 0
            ;;
        -*) err "Unknown option: $1"; exit 1 ;;
        *)  PROJECT_ARG="$1"; shift ;;
    esac
done

# -- Resolve project path --
BOLD='\033[1m'
if [[ -n "$PROJECT_ARG" ]]; then
    PROJECT_PATH="$(cd "$PROJECT_ARG" 2>/dev/null && pwd)" || { err "Directory not found: $PROJECT_ARG"; exit 1; }
    info "Using specified project path: ${PROJECT_PATH}"
else
    PROJECT_PATH="$(pwd)"
    echo ""
    info "${BOLD}No project path specified — using current directory:${NC}"
    info "  ${PROJECT_PATH}"
    info ""
    info "To run on a project elsewhere, use:"
    info "  ${CYAN}./archon-loop.sh /path/to/your-lean-project${NC}"
    echo ""
fi

if [[ "$PROJECT_PATH" == "$ARCHON_DIR" ]]; then
    err "Cannot use the Archon directory as a project."
    err "Usage: ./archon-loop.sh /path/to/your-lean-project"
    exit 1
fi

PROJECT_NAME="$(basename "$PROJECT_PATH")"
STATE_DIR="${PROJECT_PATH}/.archon"
PROGRESS_FILE="${STATE_DIR}/PROGRESS.md"
LOG_DIR="${STATE_DIR}/logs"

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
        echo -e "${RED}[ARCHON]${NC}  Run ./init.sh ${PROJECT_PATH} first." >&2
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
You are the plan agent for project '${PROJECT_NAME}'. Current stage: ${stage}.
Project directory: ${PROJECT_PATH}
Project state directory: ${STATE_DIR}
Read ${STATE_DIR}/CLAUDE.md for your role, then read ${STATE_DIR}/prompts/plan.md and ${STATE_DIR}/PROGRESS.md.
All state files (PROGRESS.md, task_pending.md, task_done.md, USER_HINTS.md, task_results/) are in ${STATE_DIR}/.
The .lean files are in ${PROJECT_PATH}/.
EOF
    else
        cat <<EOF
You are the prover agent for project '${PROJECT_NAME}'. Current stage: ${stage}.
Project directory: ${PROJECT_PATH}
Project state directory: ${STATE_DIR}
Read ${STATE_DIR}/CLAUDE.md for your role, then read ${STATE_DIR}/prompts/prover-${stage}.md and ${STATE_DIR}/PROGRESS.md.
All state files are in ${STATE_DIR}/. The .lean files are in ${PROJECT_PATH}/.
EOF
    fi
}

relpath() {
    python3 -c "import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))" "$1" "$2" 2>/dev/null \
        || echo "$1"
}

parse_objective_files() {
    awk '
        /^## Current Objectives/ { found=1; next }
        found && /^## /          { exit }
        found                    { print }
    ' "$PROGRESS_FILE" \
        | grep -oE '(\*\*|`)[^*`]+\.lean(\*\*|`)' \
        | sed 's/\*\*//g; s/`//g' \
        | while IFS= read -r f; do
            local found
            found=$(find "${PROJECT_PATH}" -path "*/$f" -not -path '*/.lake/*' -not -path '*/lake-packages/*' 2>/dev/null | head -1)
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

        cd "$PROJECT_PATH"
        claude -p "$prompt" \
            --dangerously-skip-permissions --permission-mode bypassPermissions \
            --verbose --output-format stream-json \
            "$@" 2>>"$stderr_dest" | python3 -u -c "
import sys, json, datetime

VERBOSE = '$verbose' == 'true'
RAW = open('$raw_log', 'a') if VERBOSE else None
JSONL = open('$jsonl', 'a')

def emit(event_type, **fields):
    row = {'ts': datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00', 'Z'), 'event': event_type, **fields}
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
        cd "$PROJECT_PATH"
        claude -p "$prompt" \
            --dangerously-skip-permissions --permission-mode bypassPermissions \
            "$@"
    fi
}

# ============================================================
#  Iteration directory helpers
# ============================================================

next_iter_num() {
    local max_n=0
    if [[ -d "$LOG_DIR" ]]; then
        for d in "$LOG_DIR"/iter-*; do
            [[ -d "$d" ]] || continue
            local n="${d##*iter-}"
            n="${n#"${n%%[!0]*}"}"  # strip leading zeros
            [[ "$n" =~ ^[0-9]+$ ]] && (( n > max_n )) && max_n=$n
        done
    fi
    echo $(( max_n + 1 ))
}

write_meta() {
    local meta_file="$1"
    shift
    # Accepts key=value pairs, writes/updates JSON via python
    python3 -c "
import json, sys, os
path = '$meta_file'
data = {}
if os.path.exists(path):
    with open(path) as f:
        try: data = json.load(f)
        except: pass
for arg in sys.argv[1:]:
    k, v = arg.split('=', 1)
    # Parse nested keys like provers.File.status
    keys = k.split('.')
    d = data
    for part in keys[:-1]:
        if part not in d or not isinstance(d[part], dict):
            d[part] = {}
        d = d[part]
    # Try to parse as JSON value (number, bool, null, list, dict)
    try:
        d[keys[-1]] = json.loads(v)
    except (json.JSONDecodeError, ValueError):
        d[keys[-1]] = v
with open(path, 'w') as f:
    json.dump(data, f, indent=2)
" "$@" 2>/dev/null || true
}

# ============================================================
#  Cost summary helpers
# ============================================================

show_cost_summary() {
    local label="$1"
    local iter_dir="${2:-}"
    [[ -n "$iter_dir" && -d "$iter_dir" ]] || return 0
    python3 -c "
import sys, json, os, glob
rows = []
for jsonl in glob.glob(os.path.join('$iter_dir', '**', '*.jsonl'), recursive=True):
    for l in open(jsonl):
        l = l.strip()
        if not l: continue
        try:
            r = json.loads(l)
            if r.get('event') == 'session_end': rows.append(r)
        except: pass
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

# ============================================================
#  Parallel prover iteration
# ============================================================

run_parallel_provers() {
    local stage="$1"

    # Archive old results
    local results_dir="${STATE_DIR}/task_results"
    if ls "${results_dir}/"*.md &>/dev/null; then
        local archive="${LOG_DIR}/task_results-$(date +%Y%m%d-%H%M%S)"
        mkdir -p "$archive"
        mv "${results_dir}/"*.md "$archive/"
        info "Archived previous task_results/"
    fi

    local sorry_files
    sorry_files=$(parse_objective_files)

    if [[ -z "$sorry_files" ]]; then
        warn "No files parsed from PROGRESS.md ## Current Objectives."
        warn "The plan agent must list target files in **bold** or \`backticks\` (e.g. **Foo/Bar.lean** or \`Foo/Bar.lean\`)."
        warn "Skipping this prover iteration."
        return 0
    fi

    local file_count
    file_count=$(echo "$sorry_files" | wc -l | tr -d ' ')

    if [[ "$file_count" -eq 1 ]]; then
        local rel
        rel=$(relpath "$(echo "$sorry_files" | head -1)" "$PROJECT_PATH")
        info "Only 1 file (${rel}) — running serial prover"

        # Align log path & metadata with parallel branch so dashboard works
        local file_slug
        file_slug=$(echo "$rel" | sed 's|/|_|g; s|\.lean$||')
        local prover_log="${ITER_DIR}/provers/${file_slug}"
        LOG_BASE="$prover_log"

        write_meta "$ITER_META" "provers.${file_slug}.file=${rel}" "provers.${file_slug}.status=running"

        if run_claude "$(build_prompt "prover" "$stage")"; then
            write_meta "$ITER_META" "provers.${file_slug}.status=done"
        else
            write_meta "$ITER_META" "provers.${file_slug}.status=error"
        fi
        return 0
    fi

    info "Found ${file_count} file(s) — launching parallel provers (background processes)"

    local prover_prompt_base
    prover_prompt_base=$(cat <<EOF
You are a prover agent for project '${PROJECT_NAME}'. Current stage: ${stage}.
Project directory: ${PROJECT_PATH}
Project state directory: ${STATE_DIR}
Read ${STATE_DIR}/CLAUDE.md for your role, then read ${STATE_DIR}/prompts/prover-${stage}.md and ${STATE_DIR}/PROGRESS.md.
Check your .lean file for /- USER: ... -/ comments for file-specific hints.

IMPORTANT:
- You own ONLY the file assigned below. Do NOT edit any other .lean file.
- Write your results to ${STATE_DIR}/task_results/<your_file>.md when done.
- Do NOT edit PROGRESS.md, task_pending.md, or task_done.md.
- Missing Mathlib infrastructure is NEVER a valid reason to leave a sorry.
- NEVER revert to a bare sorry. Always leave your partial proof attempt in the code.
EOF
    )

    if [[ "$DRY_RUN" == true ]]; then
        while IFS= read -r f; do
            local rel
            rel=$(relpath "$f" "$PROJECT_PATH")
            echo "=== Prover: ${rel} ==="
        done <<< "$sorry_files"
        return 0
    fi

    info ""
    info "Watch progress:"
    info "  tail -f ${ITER_DIR}/provers/*.jsonl"
    info "  watch -n10 'ls -lt ${STATE_DIR}/task_results/'"
    info ""

    # Launch provers as background processes, respecting MAX_PARALLEL
    local pids=()
    local prover_files=()
    local running=0
    while IFS= read -r f; do
        local rel
        rel=$(relpath "$f" "$PROJECT_PATH")
        local prover_prompt="${prover_prompt_base}"$'\n'"Your assigned file: ${rel}"
        local file_slug
        file_slug=$(echo "$rel" | sed 's|/|_|g; s|\.lean$||')
        local prover_log="${ITER_DIR}/provers/${file_slug}"

        # Wait for a slot if at capacity
        while (( running >= MAX_PARALLEL )); do
            # Wait for any child to finish, then recount
            wait -n 2>/dev/null || true
            running=0
            for pid in "${pids[@]}"; do
                kill -0 "$pid" 2>/dev/null && (( running++ )) || true
            done
        done

        info "  Starting prover for ${rel} (log: provers/${file_slug}.jsonl)"

        write_meta "$ITER_META" "provers.${file_slug}.file=${rel}" "provers.${file_slug}.status=running"

        # Run each prover in a subshell with its own LOG_BASE
        (
            LOG_BASE="$prover_log"
            run_claude "$prover_prompt" || true
        ) &
        pids+=($!)
        prover_files+=("$rel")
        (( running++ )) || true
    done <<< "$sorry_files"

    info "Launched ${#pids[@]} prover process(es) (max ${MAX_PARALLEL} concurrent). Waiting for all to finish..."

    # Wait for all provers and report results
    local failed=0
    for idx in "${!pids[@]}"; do
        local pid="${pids[$idx]}"
        local pfile="${prover_files[$idx]}"
        local file_slug
        file_slug=$(echo "$pfile" | sed 's|/|_|g; s|\.lean$||')
        if wait "$pid"; then
            info "  Prover for ${pfile} finished (pid ${pid})"
            write_meta "$ITER_META" "provers.${file_slug}.status=done"
        else
            warn "  Prover for ${pfile} exited with error (pid ${pid})"
            write_meta "$ITER_META" "provers.${file_slug}.status=error"
            (( failed++ )) || true
        fi
    done

    if [[ "$failed" -gt 0 ]]; then
        warn "${failed}/${#pids[@]} prover(s) had errors"
    else
        ok "All ${#pids[@]} prover(s) finished successfully"
    fi

    # Collect results: update task tracking files
    local results_dir="${STATE_DIR}/task_results"
    local result_count
    result_count=$(ls "${results_dir}/"*.md 2>/dev/null | wc -l | tr -d ' ')
    info "Found ${result_count}/${file_count} task result file(s) in task_results/"

    # Emit parallel round note
    if [[ -n "${LOG_BASE:-}" ]]; then
        python3 -c "
import json, datetime
row = {'ts': datetime.datetime.now().isoformat(), 'event': 'parallel_round_end', 'prover_count': ${file_count}, 'failed': ${failed}}
with open('${LOG_BASE}.jsonl', 'a') as f:
    f.write(json.dumps(row) + '\n')
" 2>/dev/null || true
    fi
}

# ============================================================
#  Review phase
# ============================================================

next_session_num() {
    local journal_dir="${STATE_DIR}/proof-journal/sessions"
    local max_n=0
    if [[ -d "$journal_dir" ]]; then
        for d in "$journal_dir"/session_*; do
            [[ -d "$d" ]] || continue
            local n="${d##*session_}"
            [[ "$n" =~ ^[0-9]+$ ]] && (( n > max_n )) && max_n=$n
        done
    fi
    echo $(( max_n + 1 ))
}

run_review_phase() {
    local stage="$1"

    local session_num
    session_num=$(next_session_num)
    local journal_dir="${STATE_DIR}/proof-journal"
    local session_dir="${journal_dir}/sessions/session_${session_num}"
    local current_session_dir="${journal_dir}/current_session"
    local attempts_file="${current_session_dir}/attempts_raw.jsonl"

    mkdir -p "$session_dir" "$current_session_dir"

    # Phase 3a: Extract attempt data from prover log (deterministic, no LLM)
    info "Extracting attempt data from prover logs..."

    # Concatenate all prover logs from this iteration for the extract script
    local combined_prover_log="${ITER_DIR}/prover.jsonl"
    if [[ -d "${ITER_DIR}/provers" ]] && ls "${ITER_DIR}/provers/"*.jsonl &>/dev/null; then
        combined_prover_log="${ITER_DIR}/provers-combined.jsonl"
        cat "${ITER_DIR}/provers/"*.jsonl > "$combined_prover_log" 2>/dev/null || true
    fi

    python3 "${ARCHON_DIR}/scripts/extract-attempts.py" \
        "$combined_prover_log" "$attempts_file" 2>&1 || true

    # Phase 3b: Run review agent
    local review_prompt
    review_prompt=$(cat <<EOF
You are the review agent for project '${PROJECT_NAME}'. Current stage: ${stage}.
Project directory: ${PROJECT_PATH}
Project state directory: ${STATE_DIR}
Read ${STATE_DIR}/CLAUDE.md for your role, then read ${STATE_DIR}/prompts/review.md.
Session number: ${session_num}.
Pre-processed attempt data: ${attempts_file} (READ THIS FIRST).
Prover log: ${combined_prover_log}

CRITICAL — Write your output files to EXACTLY these paths:
  ${session_dir}/milestones.jsonl
  ${session_dir}/summary.md
  ${session_dir}/recommendations.md
  ${STATE_DIR}/PROJECT_STATUS.md
EOF
    )

    LOG_BASE="${ITER_DIR}/review"
    run_claude "$review_prompt" || true

    # Phase 3c: Validate review output
    info "Validating review output..."
    python3 "${ARCHON_DIR}/scripts/validate-review.py" \
        "$session_dir" "$attempts_file" 2>&1 || true
}

# ============================================================
#  Main
# ============================================================

# -- Pre-flight --
if [[ "$DRY_RUN" != true ]]; then
    if ! command -v claude &>/dev/null; then
        err "Claude Code is not installed. Run setup.sh first."
        exit 1
    fi
    if ! claude -p "reply with OK" --no-session-persistence &>/dev/null; then
        err "Claude Code cannot run. Check: claude auth, ANTHROPIC_API_KEY, network."
        exit 1
    fi
    ok "Claude Code is authenticated and ready"
fi

# -- Check project state exists --
if [[ ! -f "$PROGRESS_FILE" ]]; then
    err "No project state found for '${PROJECT_NAME}'."
    err "Run: ./init.sh ${PROJECT_PATH}"
    exit 1
fi

STAGE=$(read_stage)
if [[ "$STAGE" == "init" ]]; then
    err "Project '${PROJECT_NAME}' is still in init stage."
    err "Run: ./init.sh ${PROJECT_PATH}"
    exit 1
fi

# -- Logging setup --
if [[ "$DRY_RUN" != true ]]; then
    mkdir -p "$LOG_DIR" "${STATE_DIR}/task_results" \
             "${STATE_DIR}/proof-journal/sessions" "${STATE_DIR}/proof-journal/current_session"
fi

info "Archon Loop starting"
info "Project: ${PROJECT_PATH}"
info "State: ${STATE_DIR}"
info "Max iterations: ${MAX_ITERATIONS}"
[[ -n "$FORCE_STAGE" ]] && info "Forced stage: ${FORCE_STAGE}"
[[ "$PARALLEL" == true ]] && info "Prover mode: parallel (max ${MAX_PARALLEL} concurrent)"
[[ "$PARALLEL" != true ]] && info "Prover mode: serial"
[[ "$ENABLE_REVIEW" == true ]] && info "Review: enabled"
[[ "$ENABLE_REVIEW" != true ]] && info "Review: disabled (--no-review)"
[[ "$DRY_RUN" == true ]] && warn "DRY RUN mode"
info "Logs: ${LOG_DIR}/"
info ""
info "User hints: ${STATE_DIR}/USER_HINTS.md"
info "Or add /- USER: ... -/ comments in .lean files"
info ""
info "Dashboard: bash ${ARCHON_DIR}/ui/start.sh --project ${PROJECT_PATH}"
echo ""

# -- COMPLETE check --
if is_complete; then
    ok "Project '${PROJECT_NAME}' is COMPLETE. Nothing to do."
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
    info "Iteration $((i+1))/${MAX_ITERATIONS}  |  Stage: ${STAGE}  |  Project: ${PROJECT_NAME}"
    info "════════════════════════════════════════"

    ITER_START=$SECONDS

    # -- Set up iteration directory --
    if [[ "$DRY_RUN" != true ]]; then
        ITER_NUM=$(next_iter_num)
        ITER_DIR="${LOG_DIR}/iter-$(printf '%03d' "$ITER_NUM")"
        ITER_META="${ITER_DIR}/meta.json"
        mkdir -p "${ITER_DIR}"
        [[ "$PARALLEL" == true ]] && mkdir -p "${ITER_DIR}/provers"
        LOG_BASE="${ITER_DIR}/plan"
        write_meta "$ITER_META" \
            "iteration=${ITER_NUM}" \
            "stage=${STAGE}" \
            "mode=$( [[ "$PARALLEL" == true ]] && echo parallel || echo serial )" \
            "startedAt=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
            "plan.status=running"
        info "Log dir: ${ITER_DIR}"
    fi

    # --- Plan phase ---
    info "Phase 1: Plan agent"
    info "────────────────────────────────────────"

    PLAN_START=$SECONDS
    PLAN_PROMPT=$(build_prompt "plan" "$STAGE")
    if [[ "$DRY_RUN" == true ]]; then
        echo "$PLAN_PROMPT"
    else
        run_claude "$PLAN_PROMPT" || true
    fi

    PLAN_SECS=$(( SECONDS - PLAN_START ))
    info "Plan phase finished. (${PLAN_SECS}s)"
    [[ "$DRY_RUN" != true ]] && write_meta "$ITER_META" "plan.status=done" "plan.durationSecs=${PLAN_SECS}"
    echo ""

    if is_complete; then
        ok "PROGRESS.md says COMPLETE. Exiting loop."
        break
    fi

    STAGE=$(read_stage)

    # --- Prover phase ---
    info "Phase 2: Prover agent(s)"
    [[ "$PARALLEL" == true ]] && info "Mode: parallel"
    info "────────────────────────────────────────"

    PROVER_START=$SECONDS
    [[ "$DRY_RUN" != true ]] && write_meta "$ITER_META" "prover.status=running"
    if [[ "$PARALLEL" == true ]]; then
        run_parallel_provers "$STAGE" || true
    else
        LOG_BASE="${ITER_DIR}/prover"
        PROVER_PROMPT=$(build_prompt "prover" "$STAGE")
        if [[ "$DRY_RUN" == true ]]; then
            echo "$PROVER_PROMPT"
        else
            run_claude "$PROVER_PROMPT" || true
        fi
    fi

    PROVER_SECS=$(( SECONDS - PROVER_START ))
    info "Prover phase finished. (${PROVER_SECS}s)"
    [[ "$DRY_RUN" != true ]] && write_meta "$ITER_META" "prover.status=done" "prover.durationSecs=${PROVER_SECS}"
    echo ""

    # --- Review phase ---
    if [[ "$ENABLE_REVIEW" == true && "$DRY_RUN" != true ]]; then
        info "Phase 3: Review agent"
        info "────────────────────────────────────────"

        REVIEW_START=$SECONDS
        write_meta "$ITER_META" "review.status=running"
        run_review_phase "$STAGE" || true

        REVIEW_SECS=$(( SECONDS - REVIEW_START ))
        info "Review phase finished. (${REVIEW_SECS}s)"
        write_meta "$ITER_META" "review.status=done" "review.durationSecs=${REVIEW_SECS}"
        echo ""
    fi

    ITER_SECS=$(( SECONDS - ITER_START ))
    info "Iteration $((i+1)) complete. Wall time: ${ITER_SECS}s"
    [[ "$DRY_RUN" != true ]] && write_meta "$ITER_META" "completedAt=$(date -u +%Y-%m-%dT%H:%M:%SZ)" "wallTimeSecs=${ITER_SECS}"
    show_cost_summary "  Iteration $((i+1)) totals:" "${ITER_DIR:-}"
    echo ""
done

LOOP_SECS=$(( SECONDS - LOOP_START ))
if ! is_complete; then
    warn "Reached max iterations (${MAX_ITERATIONS}). Stopping."
fi
info "Total wall time: ${LOOP_SECS}s"
show_cost_summary "  Loop totals:" "${LOG_DIR}"
echo ""
info "View results: bash ${ARCHON_DIR}/ui/start.sh --project ${PROJECT_PATH}"

#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  Archon Review — Standalone review of a prover session
#
#  Usage:
#    ./review.sh /path/to/lean-project                # review latest log
#    ./review.sh /path/to/lean-project --log FILE.jsonl  # review specific log
#
#  Runs the review pipeline independently of archon-loop:
#    1. Extracts structured attempt data from the prover log
#    2. Launches the review agent to produce proof journal
#    3. Validates review output quality
#
#  Output goes to <project>/.archon/proof-journal/sessions/
# ============================================================

ARCHON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -- Color helpers --
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${CYAN}[ARCHON]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ARCHON]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[ARCHON]${NC}  $*"; }
err()   { echo -e "${RED}[ARCHON]${NC}  $*"; }

# -- Parse args --
PROJECT_ARG=""
LOG_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --log)  LOG_FILE="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: review.sh [/path/to/lean-project] [OPTIONS]"
            echo ""
            echo "If no path given, uses current directory."
            echo ""
            echo "Options:"
            echo "  --log FILE    Review a specific .jsonl log file (default: latest in .archon/logs/)"
            echo "  -h, --help    Show this help"
            exit 0
            ;;
        -*) err "Unknown option: $1"; exit 1 ;;
        *)  PROJECT_ARG="$1"; shift ;;
    esac
done

# -- Resolve project path --
if [[ -n "$PROJECT_ARG" ]]; then
    PROJECT_PATH="$(cd "$PROJECT_ARG" 2>/dev/null && pwd)" || { err "Directory not found: $PROJECT_ARG"; exit 1; }
else
    PROJECT_PATH="$(pwd)"
    echo ""
    info "${BOLD}No project path specified — using current directory:${NC}"
    info "  ${PROJECT_PATH}"
    echo ""
fi

PROJECT_NAME="$(basename "$PROJECT_PATH")"
STATE_DIR="${PROJECT_PATH}/.archon"

# -- Pre-flight --
if [[ ! -d "$STATE_DIR" ]]; then
    err "No .archon/ found in ${PROJECT_PATH}."
    err "Run: ./init.sh ${PROJECT_PATH}"
    exit 1
fi

if ! command -v claude &>/dev/null; then
    err "Claude Code is not installed. Run setup.sh first."
    exit 1
fi

# -- Find log file --
if [[ -n "$LOG_FILE" ]]; then
    if [[ ! -f "$LOG_FILE" ]]; then
        err "Log file not found: $LOG_FILE"
        exit 1
    fi
else
    # Find the latest .jsonl log
    LOG_FILE=$(ls -t "${STATE_DIR}/logs/"archon-*.jsonl 2>/dev/null | head -1)
    if [[ -z "$LOG_FILE" ]]; then
        err "No log files found in ${STATE_DIR}/logs/"
        err "Run archon-loop.sh first to generate prover logs."
        exit 1
    fi
    info "Using latest log: ${LOG_FILE}"
fi

# -- Determine session number --
JOURNAL_DIR="${STATE_DIR}/proof-journal"
SESSIONS_DIR="${JOURNAL_DIR}/sessions"
mkdir -p "$SESSIONS_DIR" "${JOURNAL_DIR}/current_session"

MAX_N=0
for d in "$SESSIONS_DIR"/session_*; do
    [[ -d "$d" ]] || continue
    n="${d##*session_}"
    [[ "$n" =~ ^[0-9]+$ ]] && (( n > MAX_N )) && MAX_N=$n
done
SESSION_NUM=$(( MAX_N + 1 ))

SESSION_DIR="${SESSIONS_DIR}/session_${SESSION_NUM}"
ATTEMPTS_FILE="${JOURNAL_DIR}/current_session/attempts_raw.jsonl"
mkdir -p "$SESSION_DIR"

info "═══════════════════════════════════════════════"
info "Review — Session ${SESSION_NUM}"
info "═══════════════════════════════════════════════"
info "Project: ${PROJECT_PATH}"
info "Log: ${LOG_FILE}"
info "Output: ${SESSION_DIR}/"
echo ""

# -- Step 1: Extract attempt data --
info "Step 1: Extracting attempt data..."
python3 "${ARCHON_DIR}/scripts/extract-attempts.py" \
    "$LOG_FILE" "$ATTEMPTS_FILE" 2>&1 || {
    err "Failed to extract attempt data"
    exit 1
}
ok "Attempt data extracted"

# -- Step 2: Run review agent --
info "Step 2: Running review agent..."

STAGE=$(awk '/^## Current Stage/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit}' \
    "${STATE_DIR}/PROGRESS.md" 2>/dev/null || echo "unknown")

REVIEW_PROMPT=$(cat <<EOF
You are the review agent for project '${PROJECT_NAME}'. Current stage: ${STAGE}.
Project directory: ${PROJECT_PATH}
Project state directory: ${STATE_DIR}
Read ${STATE_DIR}/CLAUDE.md for your role, then read ${STATE_DIR}/prompts/review.md.
Session number: ${SESSION_NUM}.
Pre-processed attempt data: ${ATTEMPTS_FILE} (READ THIS FIRST).
Prover log: ${LOG_FILE}
Write your output to: ${SESSION_DIR}/
EOF
)

cd "$PROJECT_PATH"
claude -p "$REVIEW_PROMPT" \
    --dangerously-skip-permissions --permission-mode bypassPermissions || true

ok "Review agent finished"

# -- Step 3: Validate output --
info "Step 3: Validating review output..."
python3 "${ARCHON_DIR}/scripts/validate-review.py" \
    "$SESSION_DIR" "$ATTEMPTS_FILE" 2>&1

VALIDATE_EXIT=$?
echo ""

if [[ $VALIDATE_EXIT -eq 0 ]]; then
    ok "Review passed validation"
elif [[ $VALIDATE_EXIT -eq 1 ]]; then
    warn "Review passed with warnings"
else
    warn "Review has validation failures — check output"
fi

echo ""
ok "Session ${SESSION_NUM} review complete."
ok "Results: ${SESSION_DIR}/"
ok ""
ok "Files:"
ls -1 "$SESSION_DIR/" 2>/dev/null | while read -r f; do
    ok "  ${f}"
done

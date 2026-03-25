#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  Archon Init — Per-project setup
#
#  Usage:
#    ./init.sh                          # init current directory
#    ./init.sh /path/to/lean-project    # init an external project
#    ./init.sh workspace/my-project     # init a project in workspace/
#
#  Creates .archon/ inside the target project with:
#    - State files (PROGRESS.md, task tracking, etc.)
#    - Symlinked prompts (auto-updated from Archon source)
#  Sets up .claude/ in the target project with:
#    - Symlinked Archon skills (lean4)
#    - Project-scoped MCP server
#    - User skill/rule directories for custom extensions
# ============================================================

ARCHON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# -- Color helpers --
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
info()  { echo -e "${CYAN}[ARCHON]${NC}  $*"; }
ok()    { echo -e "${GREEN}[ARCHON]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[ARCHON]${NC}  $*"; }
err()   { echo -e "${RED}[ARCHON]${NC}  $*"; }

# -- Determine project path --
if [[ $# -ge 1 && "$1" != -* ]]; then
    # Explicit path given — use it (create if it doesn't exist)
    if [[ ! -d "$1" ]]; then
        mkdir -p "$1"
        info "Created directory: $1"
    fi
    PROJECT_PATH="$(cd "$1" && pwd)"
    info "Using specified project path: ${PROJECT_PATH}"
else
    # No path given — prompt for a project name under workspace/
    echo ""
    info "${BOLD}No project path specified.${NC}"
    info ""
    info "Enter a name to create a new project under workspace/,"
    info "or press Ctrl-C and re-run with a path:"
    info "  ${CYAN}./init.sh /path/to/your-lean-project${NC}"
    echo ""
    read -rp "  Project name: " PROJECT_INPUT
    if [[ -z "$PROJECT_INPUT" ]]; then
        err "No project name entered."
        exit 1
    fi
    PROJECT_PATH="${ARCHON_DIR}/workspace/${PROJECT_INPUT}"
    mkdir -p "$PROJECT_PATH"
    info "Created project at: ${PROJECT_PATH}"
fi

# Don't use Archon dir itself as project
if [[ "$PROJECT_PATH" == "$ARCHON_DIR" ]]; then
    err "Cannot use the Archon directory as a project."
    err "Usage: ./init.sh /path/to/your-lean-project"
    err "   or: ./init.sh workspace/your-project"
    exit 1
fi

# -- Derive project name and state dir --
PROJECT_NAME="$(basename "$PROJECT_PATH")"
STATE_DIR="${PROJECT_PATH}/.archon"

info "Archon directory: ${ARCHON_DIR}"
info "Project: ${PROJECT_PATH}"
info "Project state: ${STATE_DIR}"
echo ""

# -- Pre-flight --
if ! command -v claude &>/dev/null; then
    err "Claude Code is not installed. Run setup.sh first."
    exit 1
fi

# ============================================================
#  Step 1: Create .archon/ state directory with templates
# ============================================================
info "=== Step 1: Setting up .archon/ state directory ==="

mkdir -p "${STATE_DIR}/task_results" "${STATE_DIR}/logs" "${STATE_DIR}/prompts" \
         "${STATE_DIR}/proof-journal/sessions" "${STATE_DIR}/proof-journal/current_session"

for f in PROGRESS.md CLAUDE.md USER_HINTS.md task_pending.md task_done.md; do
    if [[ ! -f "${STATE_DIR}/${f}" ]]; then
        cp "${ARCHON_DIR}/.claude/archon-template/${f}" "${STATE_DIR}/${f}"
    fi
done
ok "State directory ready"

# -- Add .archon/ to the project's .gitignore if it's a git repo --
if [[ -d "${PROJECT_PATH}/.git" ]]; then
    GITIGNORE="${PROJECT_PATH}/.gitignore"
    if [[ ! -f "$GITIGNORE" ]] || ! grep -qxF '.archon/' "$GITIGNORE"; then
        echo '.archon/' >> "$GITIGNORE"
        ok "Added .archon/ to project .gitignore"
    fi
fi

# ============================================================
#  Step 2: Symlink prompts into .archon/prompts/
# ============================================================
info "=== Step 2: Linking prompts ==="

for f in "${ARCHON_DIR}"/.claude/prompts/*.md; do
    local_name="${STATE_DIR}/prompts/$(basename "$f")"
    # Create or update symlink (remove stale copies/links first)
    rm -f "$local_name"
    ln -s "$f" "$local_name"
done
ok "Prompts symlinked to .archon/prompts/ (auto-updated from Archon source)"

# ============================================================
#  Step 3: Install lean-lsp MCP server at project scope
# ============================================================
info "=== Step 3: Installing lean-lsp MCP server (project scope) ==="

LEAN_LSP_MCP_DIR="${ARCHON_DIR}/.claude/tools/lean-lsp-mcp"

cd "$PROJECT_PATH"
MCP_OUTPUT=$(claude mcp add lean-lsp -s project -- uv run --directory "${LEAN_LSP_MCP_DIR}" lean-lsp-mcp 2>&1)
if [ $? -eq 0 ]; then
    ok "lean-lsp MCP server added (project scope)"
elif echo "$MCP_OUTPUT" | grep -qi "already exists"; then
    ok "lean-lsp MCP server already configured"
else
    warn "Failed to add lean-lsp MCP server: $MCP_OUTPUT"
fi

# ============================================================
#  Step 4: Symlink Archon skills + create user skill directories
# ============================================================
info "=== Step 4: Linking Archon skills ==="

ARCHON_SKILLS="${ARCHON_DIR}/.claude/skills/lean4"

# Verify Archon skills are present
if [ ! -f "${ARCHON_SKILLS}/.claude-plugin/plugin.json" ]; then
    err "Archon lean4 skills not found at .claude/skills/lean4/"
    err "The repo may be incomplete — try re-cloning."
    exit 1
fi

# Create .claude/skills/ and .claude/rules/ in the project
mkdir -p "${PROJECT_PATH}/.claude/skills" "${PROJECT_PATH}/.claude/rules"

# Symlink Archon's lean4 skills into the project (as archon-lean4 to avoid
# conflicting with any existing .claude/skills/lean4 the user may have)
ln -sfn "${ARCHON_SKILLS}" "${PROJECT_PATH}/.claude/skills/archon-lean4"
ok "Archon lean4 skills symlinked to .claude/skills/archon-lean4"
ok "User skill/rule directories created (.claude/skills/, .claude/rules/)"

# ============================================================
#  Step 5: Detect and disable conflicting global lean4-skills
# ============================================================
info "=== Step 5: Checking for conflicting global lean4-skills ==="

# Archon's lean4 skills are symlinked locally. If the user also has
# lean4-skills installed globally as a plugin, Claude Code would see
# both sets and not know which to use. Detect and disable the global one.

USER_SETTINGS="$HOME/.claude/settings.json"
GLOBAL_LEAN4_FOUND=false
GLOBAL_LEAN4_NAMES=()

if [[ -f "$USER_SETTINGS" ]] && command -v python3 &>/dev/null; then
    while IFS= read -r plugin_key; do
        [[ -z "$plugin_key" ]] && continue
        GLOBAL_LEAN4_FOUND=true
        GLOBAL_LEAN4_NAMES+=("$plugin_key")
    done < <(python3 -c "
import json, sys
try:
    with open('$USER_SETTINGS') as f:
        data = json.load(f)
    for key in data.get('enabledPlugins', {}):
        if 'lean4' in key.lower() or 'lean4-skills' in key.lower():
            print(key)
except: pass
" 2>/dev/null)
fi

if [[ "$GLOBAL_LEAN4_FOUND" == true ]]; then
    warn "Detected global lean4-skills plugin(s) that would conflict with Archon's skills:"
    for name in "${GLOBAL_LEAN4_NAMES[@]}"; do
        warn "  - ${name}"
    done
    info ""
    info "Disabling for this project to avoid duplicate skill conflicts."
    info "Archon's modified lean4 skills (symlinked) will be used instead."
    info ""

    cd "$PROJECT_PATH"
    for name in "${GLOBAL_LEAN4_NAMES[@]}"; do
        claude plugin disable "$name" --scope project 2>/dev/null && \
            ok "Disabled '${name}' for this project" || \
            warn "Could not auto-disable '${name}'. You may need to disable it manually."
    done

    info ""
    info "${BOLD}NOTE:${NC} Your global lean4-skills is NOT removed — it still works in other projects."
    info "To re-enable it in this project, run:"
    for name in "${GLOBAL_LEAN4_NAMES[@]}"; do
        info "  ${CYAN}cd ${PROJECT_PATH} && claude plugin enable ${name} --scope project${NC}"
    done
    echo ""
else
    ok "No conflicting global lean4-skills detected"
fi

# ============================================================
#  Step 6: Check stage and launch interactive Claude
# ============================================================
STAGE=$(awk '/^## Current Stage/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit}' "${STATE_DIR}/PROGRESS.md")

if [[ "$STAGE" != "init" ]]; then
    ok "Init already complete. Current stage: ${STAGE}"
    ok "Run: ./archon-loop.sh ${PROJECT_PATH}"
    exit 0
fi

info "═══════════════════════════════════════════════"
info "Initializing project: ${PROJECT_NAME}"
info "═══════════════════════════════════════════════"
info "Claude will check the project state and guide you through setup."
echo ""

cd "$PROJECT_PATH"
claude "You are in the init stage for project '${PROJECT_NAME}' at ${PROJECT_PATH}. Read ${STATE_DIR}/CLAUDE.md, then read ${STATE_DIR}/prompts/init.md and follow its instructions. Project state files are in ${STATE_DIR}/. Write PROGRESS.md and other state files there, not in the project directory." || true

# -- Check if init completed --
NEW_STAGE=$(awk '/^## Current Stage/{getline; gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit}' "${STATE_DIR}/PROGRESS.md")

echo ""
if [[ "$NEW_STAGE" == "init" ]]; then
    warn "Stage is still 'init'. Setup may not be complete."
    warn "Re-run: ./init.sh ${PROJECT_PATH}"
else
    ok "Init complete. Stage is now: ${NEW_STAGE}"
    ok ""
    ok "Next steps:"
    ok "  1. Verify: cd ${PROJECT_PATH} && claude → /lean4:doctor"
    ok "  2. Start loop: ./archon-loop.sh ${PROJECT_PATH}"
fi

#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  Archon Setup Script
#  Creates project folder, installs Claude Code + Lean4 tools
# ============================================================

# -- Color helpers --
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

# -- Determine script directory & project folder --
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${SCRIPT_DIR}/Archon"

info "Script directory: ${SCRIPT_DIR}"
info "Project directory: ${PROJECT_DIR}"

# ============================================================
# Phase 0: Create or enter project folder
# ============================================================
if [ -d "${PROJECT_DIR}" ]; then
    warn "Project folder already exists: ${PROJECT_DIR}"
    info "Entering existing folder and continuing setup (existing files will NOT be overwritten)..."
    cd "${PROJECT_DIR}"

    if [ -d ".git" ]; then
        ok "Existing git repository detected, skipping git init"
    else
        git init -q
        ok "Initialized git repository in existing folder"
    fi
else
    info "Creating project folder..."
    mkdir -p "${PROJECT_DIR}"
    cd "${PROJECT_DIR}"
    git init -q
    ok "Created and initialized: ${PROJECT_DIR}"
fi

# ============================================================
# Phase 1: System prerequisites check
# ============================================================
info "=== Phase 1: Checking system prerequisites ==="

# -- git --
if command -v git &>/dev/null; then
    ok "git: $(git --version)"
else
    err "git is not installed. Please install git first."
    exit 1
fi

# -- Python 3 --
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" --version 2>&1 | sed -n 's/.*Python \([0-9]*\.[0-9]*\).*/\1/p')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    err "Python 3.10+ is required but not found."
    err "Install with:"
    err "  Linux: sudo apt install python3 python3-pip python3-venv"
    err "  macOS: brew install python@3.12"
    exit 1
fi
ok "Python: $($PYTHON --version)"

# -- pip --
if ! $PYTHON -m pip --version &>/dev/null; then
    warn "pip not found, installing..."
    $PYTHON -m ensurepip --upgrade 2>/dev/null || {
        err "Cannot install pip via ensurepip. Try:"
        err "  Linux: sudo apt install python3-pip"
        err "  macOS: python3 -m ensurepip --upgrade"
        exit 1
    }
fi
ok "pip: $($PYTHON -m pip --version 2>&1 | head -1)"

# -- curl --
if ! command -v curl &>/dev/null; then
    warn "curl not found, attempting to install..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq curl
    elif command -v brew &>/dev/null; then
        brew install curl
    else
        err "curl is required. Please install it manually."
        exit 1
    fi
fi
ok "curl: available"

# -- elan / lean / lake --
LEAN_MISSING=false
if command -v elan &>/dev/null; then
    ok "elan: $(elan --version 2>&1 | head -1)"
else
    warn "elan not found"
    LEAN_MISSING=true
fi

if command -v lean &>/dev/null; then
    ok "lean: $(lean --version 2>&1 | head -1)"
else
    warn "lean not found in PATH"
    LEAN_MISSING=true
fi

if command -v lake &>/dev/null; then
    ok "lake: $(lake --version 2>&1 | head -1)"
else
    warn "lake not found in PATH"
    LEAN_MISSING=true
fi

if [ "$LEAN_MISSING" = true ]; then
    echo ""
    warn "Lean toolchain components are missing or not in PATH."
    warn "Archon requires elan, lean, and lake to work."
    warn ""
    warn "If not installed, choose one of:"
    warn "  curl https://elan-init.github.io/elan/elan-init.sh -sSf | sh"
    warn "  brew install elan-init    (macOS)"
    warn ""
    warn "If already installed but not in PATH, add to your shell profile:"
    warn "  export PATH=\"\$HOME/.elan/bin:\$PATH\""
    warn ""
    warn "After installing or fixing PATH, re-run this script."
    exit 1
fi

# ============================================================
# Phase 2: Install Python tooling (uv) & packages
# ============================================================
info "=== Phase 2: Python tooling & packages ==="

# -- uv (Python package manager) --
if command -v uv &>/dev/null; then
    ok "uv: $(uv --version)"
    info "Upgrading uv..."
    uv self update 2>/dev/null || true
else
    info "Installing uv..."
    # Try standalone installer first, fall back to pip
    if curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null; then
        export PATH="$HOME/.local/bin:$PATH"
    else
        warn "Standalone installer failed, trying pip..."
        $PYTHON -m pip install --user uv 2>/dev/null || $PYTHON -m pip install uv
        export PATH="$HOME/.local/bin:$PATH"
    fi
    if command -v uv &>/dev/null; then
        ok "uv installed: $(uv --version)"
    else
        err "uv installation failed. Try manually: pip install uv"
        exit 1
    fi
fi

# -- Install local lean-lsp-mcp (Archon fork with rate limit adjustments) --
LEAN_LSP_MCP_DIR="${PROJECT_DIR}/.claude/tools/lean-lsp-mcp"
if [ -f "${LEAN_LSP_MCP_DIR}/pyproject.toml" ]; then
    info "Installing local lean-lsp-mcp from ${LEAN_LSP_MCP_DIR}..."
    uv tool install --force "${LEAN_LSP_MCP_DIR}" && \
        ok "lean-lsp-mcp installed (local Archon fork)" || \
        { err "Failed to install local lean-lsp-mcp"; exit 1; }
else
    warn "Local lean-lsp-mcp not found, falling back to PyPI version"
    uv tool install lean-lsp-mcp 2>/dev/null || uv tool upgrade lean-lsp-mcp 2>/dev/null || true
    ok "lean-lsp-mcp installed (PyPI)"
fi

# -- tmux (required for parallel agent teams) --
if command -v tmux &>/dev/null; then
    ok "tmux: $(tmux -V)"
else
    info "Installing tmux (required for parallel agent teams)..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq tmux
    elif command -v brew &>/dev/null; then
        brew install tmux
    fi
    if command -v tmux &>/dev/null; then
        ok "tmux installed: $(tmux -V)"
    else
        warn "Could not install tmux. Install manually. Parallel mode requires it."
    fi
fi

# -- ripgrep (optional but recommended for search) --
if command -v rg &>/dev/null; then
    ok "ripgrep: $(rg --version | head -1)"
else
    warn "ripgrep not found (optional, enhances search)"
    warn "Install with: sudo apt install ripgrep"
fi

# ============================================================
# Phase 3: Node.js / Claude Code
# ============================================================
info "=== Phase 3: Claude Code ==="

# -- Check if Claude Code is already installed --
CLAUDE_INSTALLED=false
CLAUDE_VERSION=""

if command -v claude &>/dev/null; then
    CLAUDE_INSTALLED=true
    CLAUDE_VERSION=$(claude --version 2>/dev/null || echo "unknown")
    ok "Claude Code is installed: ${CLAUDE_VERSION}"
fi

if [ "$CLAUDE_INSTALLED" = true ]; then
    warn "Claude Code is already installed (${CLAUDE_VERSION})."
    warn "To update, run:  claude update"
    warn "Or reinstall:    curl -fsSL https://claude.ai/install.sh | bash"
else
    info "Installing Claude Code..."
    curl -fsSL https://claude.ai/install.sh | bash
    # Refresh PATH
    export PATH="$HOME/.local/bin:$PATH"
    if command -v claude &>/dev/null; then
        ok "Claude Code installed: $(claude --version 2>/dev/null)"
    else
        err "Claude Code installation failed. Try manually: curl -fsSL https://claude.ai/install.sh | bash"
        exit 1
    fi
fi

# ============================================================
# Phase 4: Configure MCP server (lean-lsp-mcp, local fork)
# ============================================================
info "=== Phase 4: Configuring lean-lsp-mcp MCP server ==="

cd "${PROJECT_DIR}"

info "Adding lean-lsp MCP server to Claude Code (project scope)..."
# Use uv run to launch from local source — picks up local modifications
claude mcp add lean-lsp -s project -- uv run --directory "${LEAN_LSP_MCP_DIR}" lean-lsp-mcp && \
    ok "lean-lsp MCP server added (local fork)" || \
    warn "Failed to add lean-lsp MCP server. Manual setup: claude mcp add lean-lsp -s project -- uv run --directory .claude/tools/lean-lsp-mcp lean-lsp-mcp"

# ============================================================
# Phase 5: Register local lean4-skills plugin
# ============================================================
info "=== Phase 5: Registering local lean4-skills plugin ==="

cd "${PROJECT_DIR}"

# Verify local skills are present (shipped with the repo)
if [ ! -f "${PROJECT_DIR}/.claude/skills/lean4/.claude-plugin/plugin.json" ]; then
    err "Local lean4 skills not found at .claude/skills/lean4/"
    err "The repo may be incomplete — try re-cloning."
    exit 1
fi
ok "Local lean4 skills found at .claude/skills/lean4/"

if [ ! -f "${PROJECT_DIR}/.claude/skills/.claude-plugin/marketplace.json" ]; then
    err "Local marketplace.json not found at .claude/skills/.claude-plugin/"
    exit 1
fi
ok "Local marketplace index found"

# Register the local marketplace and install the plugin
info "Registering local marketplace with Claude Code..."
claude plugin marketplace add "${PROJECT_DIR}/.claude/skills" 2>/dev/null && \
    ok "Local marketplace registered" || \
    { warn "Could not auto-register marketplace. Register manually inside Claude Code:"; \
      warn "  /plugin marketplace add .claude/skills"; }

info "Installing lean4 from local marketplace..."
claude plugin install lean4@archon-local 2>/dev/null && \
    ok "lean4 plugin installed from local skills" || \
    { warn "Could not auto-install plugin. Install manually inside Claude Code:"; \
      warn "  /plugin install lean4@archon-local"; }

# Create/update CLAUDE.md
CLAUDE_MD="${PROJECT_DIR}/CLAUDE.md"
if [ ! -f "$CLAUDE_MD" ]; then
    cat > "$CLAUDE_MD" << 'CLAUDEEOF'
# Archon Project

## Plugins
- lean4 (local): `.claude/skills/lean4/` — edit here to customize skills
  - Registered via local marketplace at `.claude/skills/.claude-plugin/marketplace.json`
CLAUDEEOF
    ok "Created CLAUDE.md"
fi

# ============================================================
# Done
# ============================================================
echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo -e "  Project directory: ${CYAN}${PROJECT_DIR}${NC}"
echo ""
echo -e "  ${YELLOW}Next steps:${NC}"
echo -e "  1. cd ${PROJECT_DIR}"
echo -e "  2. claude"
echo -e "  3. Verify with: ${CYAN}/lean4:doctor${NC}"
echo ""
echo -e "  ${YELLOW}If the plugin didn't auto-register, run inside Claude Code:${NC}"
echo -e "     ${CYAN}/plugin marketplace add .claude/skills${NC}"
echo -e "     ${CYAN}/plugin install lean4@archon-local${NC}"
echo ""
echo -e "  ${YELLOW}To customize lean4 skills:${NC}"
echo -e "     Edit files under ${CYAN}.claude/skills/lean4/${NC}"
echo ""
if [ "$CLAUDE_INSTALLED" = true ]; then
    echo -e "  ${YELLOW}Note:${NC} Claude Code was already installed (${CLAUDE_VERSION})."
    echo -e "  To update: ${CYAN}claude update${NC}"
    echo ""
fi

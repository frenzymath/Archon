#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  Archon Setup Script
#  Installs system prerequisites: Python, uv, Lean, Claude Code
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
ARCHON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

info "Archon directory: ${ARCHON_DIR}"

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
    ok "Claude Code: ${CLAUDE_VERSION} (to update: claude update)"
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
# Phase 4: Check API keys for informal agent (optional)
# ============================================================
info "=== Phase 4: Informal agent API keys (optional) ==="

info "The informal agent lets Claude Code request proof sketches from external models."
info "This does not affect the rest of the Archon workflow — everything else works without it."
info ""

[[ -n "${OPENAI_API_KEY:-}" ]]      && ok "OPENAI_API_KEY is set (OpenAI)" \
                                     || info "  OPENAI_API_KEY not set.      To use OpenAI:      export OPENAI_API_KEY=sk-..."
[[ -n "${GEMINI_API_KEY:-}" ]]      && ok "GEMINI_API_KEY is set (Gemini)" \
                                     || info "  GEMINI_API_KEY not set.      To use Gemini:      export GEMINI_API_KEY=AI..."
[[ -n "${OPENROUTER_API_KEY:-}" ]]  && ok "OPENROUTER_API_KEY is set (OpenRouter)" \
                                     || info "  OPENROUTER_API_KEY not set.  To use OpenRouter:  export OPENROUTER_API_KEY=sk-or-..."

info ""
info "Set any key(s) for the provider(s) you want, then add to ~/.bashrc or ~/.zshrc to persist."

# ============================================================
# Done
# ============================================================
echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""

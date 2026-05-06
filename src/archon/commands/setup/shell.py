"""Shell / process / PATH helpers shared across setup checks."""

from __future__ import annotations

import os
import shutil
import subprocess
from importlib import resources
from pathlib import Path

from archon import log


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, returning the CompletedProcess."""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def run_shell(script: str) -> subprocess.CompletedProcess:
    """Run a shell script string."""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def has(binary: str) -> bool:
    return shutil.which(binary) is not None


def version(cmd: list[str]) -> str:
    """Return first line of `cmd --version` output, or 'unknown'."""
    try:
        r = run(cmd)
        return (r.stdout or r.stderr).strip().splitlines()[0]
    except Exception:
        return "unknown"


def shell_rc() -> Path | None:
    """Return the user's shell rc file (zsh/bash supported), or None."""
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return Path.home() / ".zshrc"
    if "bash" in shell:
        return Path.home() / ".bashrc"
    return None


def data_path(sub_path: str = "") -> Path:
    """Resolve a path inside the bundled `archon/` package data tree."""
    root = resources.files("archon")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


def source_nvm() -> None:
    """Add the active nvm Node binary directory to PATH (no-op if nvm absent)."""
    nvm_dir = Path.home() / ".nvm"
    nvm_sh = nvm_dir / "nvm.sh"
    if not nvm_sh.exists():
        return
    r = run_shell(f'source "{nvm_sh}" && dirname "$(nvm which current)"')
    node_bin = r.stdout.strip()
    if node_bin and Path(node_bin).is_dir():
        os.environ["PATH"] = f"{node_bin}{os.pathsep}{os.environ['PATH']}"


def ensure_path_in_rc() -> None:
    """Add `~/.local/bin` to PATH in the user's shell rc, if not already there."""
    rc = shell_rc()
    if rc is None or not rc.exists():
        return
    line = 'export PATH="$HOME/.local/bin:$PATH"'
    content = rc.read_text()
    if "$HOME/.local/bin" not in content:
        with rc.open("a") as f:
            f.write(f"\n# Added by Archon setup\n{line}\n")
        log.success(f"Added ~/.local/bin to PATH in {rc}")
        log.step(f"Run: source {rc}")

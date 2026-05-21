"""Small helpers used across init steps."""

from __future__ import annotations

import getpass
import json
import shutil
import subprocess
from importlib import resources
from pathlib import Path

import typer

from archon import log


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def has(binary: str) -> bool:
    return shutil.which(binary) is not None


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def data_path(sub_path: str = "") -> Path:
    root = resources.files("archon").joinpath(".archon-src")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


def _files_equal(a: Path, b: Path) -> bool:
    """True iff ``a`` and ``b`` exist and have byte-identical content.

    Used by ``copy_file`` to avoid logging "Overwriting existing file"
    when the destination is already a verbatim copy of the source. A
    cheap mtime check would be faster but is unreliable across
    filesystem operations (and tests that touch the file).
    """
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def copy_file(src: Path, dst: Path, overwrite: bool = False) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Identical content → silent no-op. The previous behavior warned
    # "Overwriting existing file: <name>" for every bundled template
    # the user hadn't touched, which made re-init logs noisy and falsely
    # implied lossy mutations.
    if dst.exists() and _files_equal(src, dst):
        return
    if dst.exists() and overwrite:
        log.warn(f"Overwriting existing file: {dst.name}")
    if overwrite or not dst.exists():
        shutil.copy2(src, dst)


def parse_stage(progress_md: Path) -> str:
    if not progress_md.exists():
        return "init"
    lines = progress_md.read_text().splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## Current Stage"):
            if i + 1 < len(lines):
                return lines[i + 1].strip()
    return "init"


def fail_permission(path: Path, err: Exception | None) -> None:
    """Stop init with a clear message when the project dir isn't writable."""
    log.error(f"Cannot write to project directory: {path}")
    if err is not None:
        log.step(f"  {err}")
    try:
        stat = path.stat() if path.exists() else path.parent.stat()
        try:
            import pwd
            owner = pwd.getpwuid(stat.st_uid).pw_name
        except Exception:
            owner = str(stat.st_uid)
        log.step(f"  Owner: {owner}   Mode: {oct(stat.st_mode & 0o777)}")
    except Exception:
        pass
    user = getpass.getuser()
    log.step("This usually means the directory was created by a different user")
    log.step("(for example, cloned with sudo) or has restrictive permissions.")
    log.step("")
    log.step("Fix it with one of the following, then re-run 'archon init':")
    log.step(f"  sudo chown -R {user}:{user} {path}")
    log.step(f"  chmod u+w {path}")
    raise typer.Exit(1)


def find_global_mcp_lean_lsp() -> list[str]:
    settings = Path.home() / ".claude" / "settings.json"
    data = read_json(settings)
    return [
        k for k in data.get("mcpServers", {})
        if "lean" in k.lower() and "lsp" in k.lower() and "archon" not in k.lower()
    ]


def find_global_lean4_plugins() -> list[str]:
    settings = Path.home() / ".claude" / "settings.json"
    data = read_json(settings)
    return [
        k for k in data.get("enabledPlugins", {})
        if ("lean4" in k.lower() or "lean4-skills" in k.lower())
        and "archon" not in k.lower()
    ]

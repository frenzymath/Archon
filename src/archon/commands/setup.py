"""Install system-level dependencies for Archon."""

import os
import shutil
import subprocess
import sys
from importlib import resources
from pathlib import Path

import typer

from archon import log


# ── module-level state ────────────────────────────────────────────────

# Set by the `setup` command. Controls whether _install_with_pm will
# escalate to sudo, ask the user, or just print manual instructions.
#
# Values:
#   "ask"    — prompt the user before each sudo call (default)
#   "yes"    — auto-accept sudo (non-interactive, e.g. containers/CI)
#   "no"     — never sudo; print manual instructions and return False
_SUDO_MODE: str = "ask"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, returning the CompletedProcess."""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _run_shell(script: str) -> subprocess.CompletedProcess:
    """Run a shell script string."""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def _has(binary: str) -> bool:
    return shutil.which(binary) is not None


def _version(cmd: list[str]) -> str:
    """Return first line of version output, or 'unknown'."""
    try:
        r = _run(cmd)
        return (r.stdout or r.stderr).strip().splitlines()[0]
    except Exception:
        return "unknown"


def _shell_rc() -> Path | None:
    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        return Path.home() / ".zshrc"
    if "bash" in shell:
        return Path.home() / ".bashrc"
    return None


def _data_path(sub_path: str = "") -> Path:
    root = resources.files("archon")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


def _source_nvm() -> None:
    nvm_dir = Path.home() / ".nvm"
    nvm_sh = nvm_dir / "nvm.sh"
    if not nvm_sh.exists():
        return
    r = _run_shell(f'source "{nvm_sh}" && dirname "$(nvm which current)"')
    node_bin = r.stdout.strip()
    if node_bin and Path(node_bin).is_dir():
        os.environ["PATH"] = f"{node_bin}{os.pathsep}{os.environ['PATH']}"


def _ensure_path_in_rc() -> None:
    rc = _shell_rc()
    if rc is None or not rc.exists():
        return
    line = 'export PATH="$HOME/.local/bin:$PATH"'
    content = rc.read_text()
    if "$HOME/.local/bin" not in content:
        with rc.open("a") as f:
            f.write(f"\n# Added by Archon setup\n{line}\n")
        log.success(f"Added ~/.local/bin to PATH in {rc}")
        log.step(f"Run: source {rc}")


# ── sudo / package-manager helpers ────────────────────────────────────


def _detect_pm() -> tuple[str, list[str]] | None:
    """Return (name, install_cmd_prefix) for the first package manager found.

    The install command is a list you can extend with package names.
    Returns None if no supported package manager is available.
    """
    if _has("brew"):
        # Homebrew doesn't need (and refuses) sudo, so we special-case it.
        return ("brew", ["brew", "install"])
    if _has("apt-get"):
        return ("apt-get", ["sudo", "apt-get", "install", "-y"])
    if _has("dnf"):
        return ("dnf", ["sudo", "dnf", "install", "-y"])
    if _has("pacman"):
        return ("pacman", ["sudo", "pacman", "-S", "--noconfirm"])
    return None


def _ask_sudo(pkg: str, cmd: list[str]) -> bool:
    """Return True if we should run a sudo command for this package.

    - _SUDO_MODE="yes"  → always True
    - _SUDO_MODE="no"   → always False
    - _SUDO_MODE="ask"  → prompt the user (default to skip)
    """
    if cmd and cmd[0] != "sudo":
        # Non-sudo command (e.g. brew). No permission needed.
        return True

    if _SUDO_MODE == "yes":
        return True
    if _SUDO_MODE == "no":
        return False

    log.warn(f"Installing '{pkg}' requires sudo: {' '.join(cmd)}")
    return typer.confirm("  Run this command now?", default=False)


def _print_manual_install(pkg: str, install_urls: dict[str, str] | None = None) -> None:
    """Print manual install instructions for a package."""
    log.step(f"Skipping auto-install of '{pkg}'.")
    log.step(f"To install manually, run one of the following (whichever matches your OS):")
    log.step(f"  macOS (Homebrew):  brew install {pkg}")
    log.step(f"  Debian/Ubuntu:     sudo apt-get install -y {pkg}")
    log.step(f"  Fedora/RHEL:       sudo dnf install -y {pkg}")
    log.step(f"  Arch Linux:        sudo pacman -S --noconfirm {pkg}")
    if install_urls:
        for label, url in install_urls.items():
            log.step(f"  {label}: {url}")


def _install_with_pm(pkg: str, install_urls: dict[str, str] | None = None) -> bool:
    """Install a package via the detected package manager, respecting sudo policy.

    Returns True if the install was attempted (does NOT guarantee the binary
    is now available — callers should re-check with _has()).
    """
    pm = _detect_pm()
    if pm is None:
        log.warn(f"No supported package manager found for installing '{pkg}'.")
        _print_manual_install(pkg, install_urls)
        return False

    name, base_cmd = pm
    cmd = base_cmd + [pkg]

    # apt-get needs `update` first to avoid stale indexes; do it under the
    # same sudo policy.
    if name == "apt-get":
        if _ask_sudo(f"apt update (prerequisite for {pkg})", ["sudo", "apt-get", "update", "-qq"]):
            _run(["sudo", "apt-get", "update", "-qq"])
        else:
            log.step("Skipping apt-get update — the install may fail on a stale index.")

    if not _ask_sudo(pkg, cmd):
        _print_manual_install(pkg, install_urls)
        return False

    log.step(f"Running: {' '.join(cmd)}")
    r = _run(cmd)
    if r.returncode != 0:
        log.warn(f"Install command failed: {(r.stderr or r.stdout).strip()}")
        return False
    return True


def _install_bundle_with_pm(
    label: str,
    packages_by_pm: dict[str, list[str]],
    install_urls: dict[str, str] | None = None,
) -> bool:
    """Install a bundle of packages whose names differ across package managers.

    `packages_by_pm` maps PM name ("brew", "apt-get", "dnf", "pacman") to the
    list of packages to install on that PM. Useful for TeX / graphviz where
    Debian calls it `libgraphviz-dev` but Fedora calls it `graphviz-devel`.
    """
    pm = _detect_pm()
    if pm is None:
        log.warn(f"No supported package manager found for installing '{label}'.")
        if install_urls:
            for k, v in install_urls.items():
                log.step(f"  {k}: {v}")
        return False

    name, base_cmd = pm
    pkgs = packages_by_pm.get(name)
    if not pkgs:
        log.warn(f"No package list defined for {name} when installing '{label}'.")
        if install_urls:
            for k, v in install_urls.items():
                log.step(f"  {k}: {v}")
        return False

    cmd = base_cmd + pkgs

    if name == "apt-get":
        if _ask_sudo(f"apt update (prerequisite for {label})", ["sudo", "apt-get", "update", "-qq"]):
            _run(["sudo", "apt-get", "update", "-qq"])
        else:
            log.step("Skipping apt-get update — the install may fail on a stale index.")

    if not _ask_sudo(label, cmd):
        log.step(f"Skipping auto-install of '{label}'. To install manually on your system:")
        log.step(f"  {' '.join(cmd)}")
        if install_urls:
            for k, v in install_urls.items():
                log.step(f"  {k}: {v}")
        return False

    log.step(f"Running: {' '.join(cmd)}")
    r = _run(cmd)
    if r.returncode != 0:
        log.warn(f"Install command failed: {(r.stderr or r.stdout).strip()}")
        return False
    return True


# ── individual checks ─────────────────────────────────────────────────


def _check_git() -> bool:
    if _has("git"):
        log.success(f"git: {_version(['git', '--version'])}")
        return True

    log.step("git not found, attempting install...")
    _install_with_pm("git", {"Manual": "https://git-scm.com/downloads"})

    if _has("git"):
        log.success(f"git installed: {_version(['git', '--version'])}")
        return True
    log.error("git is not available — install it manually and re-run.")
    return False


def _check_python() -> bool:
    v = sys.version_info
    if v >= (3, 10):
        log.success(f"Python: {v.major}.{v.minor}.{v.micro}")
        return True
    log.error(f"Python 3.10+ required, found {v.major}.{v.minor}.{v.micro}")
    log.step("Install: https://www.python.org/downloads/")
    return False


def _check_curl() -> bool:
    if _has("curl"):
        log.success("curl: available")
        return True

    log.step("curl not found, attempting install...")
    _install_with_pm("curl")

    if _has("curl"):
        log.success("curl: installed")
        return True
    log.error("curl is required and is not available.")
    return False


def _check_lean() -> bool:
    elan_bin = Path.home() / ".elan" / "bin"
    if elan_bin.is_dir() and str(elan_bin) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{elan_bin}{os.pathsep}{os.environ['PATH']}"

    ok = True
    for tool in ("elan", "lean", "lake"):
        if _has(tool):
            log.success(f"{tool}: {_version([tool, '--version'])}")
        else:
            ok = False

    if ok:
        return True

    # Installing elan is either via Homebrew (no sudo) or via the official
    # installer script that writes to ~/.elan (also no sudo). Neither needs
    # the sudo gate — but we still warn the user that a script runs.
    if _has("brew"):
        log.step("Installing elan via Homebrew...")
        r = _run(["brew", "install", "elan-init"])
        if r.returncode == 0:
            _run(["elan", "default", "stable"])
    else:
        log.warn("Archon is about to run the official elan installer:")
        log.step("  curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y --default-toolchain stable")
        log.step("This installs to ~/.elan — no sudo required.")
        proceed = typer.confirm("  Run it now?", default=True) if _SUDO_MODE == "ask" else (_SUDO_MODE == "yes")
        if not proceed:
            log.step("Skipping elan install. See: https://lean-lang.org/lean4/doc/quickstart.html")
            return False
        _run_shell("curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- -y --default-toolchain stable")

    if elan_bin.is_dir():
        os.environ["PATH"] = f"{elan_bin}{os.pathsep}{os.environ['PATH']}"

    ok = True
    for tool in ("elan", "lean", "lake"):
        if _has(tool):
            log.success(f"{tool} installed: {_version([tool, '--version'])}")
        else:
            log.warn(f"{tool} still not found after install")
            ok = False

    if not ok:
        log.error("Lean toolchain installation incomplete")
        log.step("Install manually: curl https://elan.lean-lang.org/elan-init.sh -sSf | sh")
        log.step('Then add to PATH: export PATH="$HOME/.elan/bin:$PATH"')
    return ok


def _check_uv() -> bool:
    if _has("uv"):
        log.success(f"uv: {_version(['uv', '--version'])}")
        _run(["uv", "self", "update"])
        return True

    # uv's standalone installer writes to ~/.local/bin — no sudo.
    log.step("Installing uv (to ~/.local/bin, no sudo)...")
    r = _run_shell("curl -LsSf https://astral.sh/uv/install.sh | sh")
    if r.returncode != 0:
        log.warn("Standalone installer failed, trying pip --user...")
        _run([sys.executable, "-m", "pip", "install", "--user", "uv"])
    os.environ["PATH"] = f"{Path.home() / '.local' / 'bin'}{os.pathsep}{os.environ['PATH']}"
    if _has("uv"):
        log.success(f"uv installed: {_version(['uv', '--version'])}")
        _ensure_path_in_rc()
        return True
    log.error("uv installation failed")
    log.step("Install manually: https://docs.astral.sh/uv/getting-started/installation/")
    return False


def _check_ripgrep() -> bool:
    if _has("rg"):
        log.success(f"ripgrep: {_version(['rg', '--version'])}")
        return True

    log.step("ripgrep is optional (used for code search). Attempting install...")
    _install_with_pm("ripgrep", {"Manual": "https://github.com/BurntSushi/ripgrep"})

    if _has("rg"):
        log.success(f"ripgrep installed: {_version(['rg', '--version'])}")
        return True
    log.warn("ripgrep not installed — some search tools will be slower.")
    return False


def _check_claude_code() -> bool:
    if _has("claude"):
        log.success(f"Claude Code: {_version(['claude', '--version'])} (update: claude update)")
        return True

    # Claude Code's installer writes to ~/.local/bin — no sudo.
    log.step("Installing Claude Code (to ~/.local/bin, no sudo, may take a few minutes)...")
    _run_shell("curl -fsSL https://claude.ai/install.sh | bash")
    os.environ["PATH"] = f"{Path.home() / '.local' / 'bin'}{os.pathsep}{os.environ['PATH']}"
    if _has("claude"):
        log.success(f"Claude Code installed: {_version(['claude', '--version'])}")
        return True
    log.error("Claude Code installation failed")
    log.step("Install manually: https://code.claude.com/docs/en/overview")
    return False


# ── blueprint-specific checks ─────────────────────────────────────────
#
# leanblueprint (Patrick Massot's plasTeX-based blueprint tooling) needs:
#   - graphviz + graphviz dev headers  (for pygraphviz → plastexdepgraph)
#   - a LaTeX toolchain                (for the PDF blueprint)
#   - ghostscript, dvisvgm, pdf2svg, pdfcrop  (for the web blueprint's
#     embedded figures and plasTeX's TeX-to-SVG conversion)
#   - the `leanblueprint` Python CLI
#
# Package names diverge across distros, so we list them per-PM.


def _check_graphviz() -> bool:
    """graphviz binary + headers needed to build pygraphviz, which plastexdepgraph uses."""
    have_dot = _has("dot")
    # The dev headers are harder to detect portably — we just check for
    # a well-known header file on Linux, and trust brew on macOS.
    dev_headers_present = (
        Path("/usr/include/graphviz/cgraph.h").exists()
        or Path("/usr/local/include/graphviz/cgraph.h").exists()
        or Path("/opt/homebrew/include/graphviz/cgraph.h").exists()
    )

    if have_dot and dev_headers_present:
        log.success(f"graphviz: {_version(['dot', '-V'])}")
        return True

    if have_dot and not dev_headers_present:
        log.warn("graphviz is installed but development headers seem to be missing.")
        log.step("These are required to build pygraphviz (used by leanblueprint's dep graph).")
    else:
        log.step("graphviz not found, attempting install...")

    ok = _install_bundle_with_pm(
        "graphviz (with dev headers)",
        {
            # brew's `graphviz` formula ships headers, no separate dev pkg.
            "brew": ["graphviz"],
            "apt-get": ["graphviz", "libgraphviz-dev"],
            "dnf": ["graphviz", "graphviz-devel"],
            "pacman": ["graphviz"],
        },
        install_urls={
            "Manual": "https://pygraphviz.github.io/documentation/stable/install.html",
        },
    )

    if _has("dot"):
        log.success(f"graphviz installed: {_version(['dot', '-V'])}")
        return ok
    log.warn("graphviz not installed — the blueprint dependency graph will fail to build.")
    return False


def _check_tex_toolchain() -> bool:
    """LaTeX + auxiliary tools needed by leanblueprint (pdf & web targets)."""
    # Minimal probe: pdflatex/xelatex/lualatex are the common LaTeX engines.
    # For the web target plasTeX itself invokes latex/dvisvgm.
    have_latex = any(_has(b) for b in ("pdflatex", "xelatex", "lualatex", "latex"))
    have_gs = _has("gs")
    have_dvisvgm = _has("dvisvgm")
    have_pdf2svg = _has("pdf2svg")
    have_pdfcrop = _has("pdfcrop")

    if have_latex and have_gs and have_dvisvgm and have_pdf2svg and have_pdfcrop:
        log.success("TeX toolchain: latex, ghostscript, dvisvgm, pdf2svg, pdfcrop all present")
        return True

    missing = []
    if not have_latex:
        missing.append("latex")
    if not have_gs:
        missing.append("ghostscript")
    if not have_dvisvgm:
        missing.append("dvisvgm")
    if not have_pdf2svg:
        missing.append("pdf2svg")
    if not have_pdfcrop:
        missing.append("pdfcrop")

    log.step(f"TeX toolchain incomplete (missing: {', '.join(missing)}), attempting install...")

    ok = _install_bundle_with_pm(
        "TeX toolchain for leanblueprint",
        {
            # Homebrew: the full texlive cask is enormous; we install the
            # smaller pieces. Users who want a fuller TeX setup should
            # install MacTeX themselves.
            "brew": ["texlive", "ghostscript", "dvisvgm", "pdf2svg"],
            "apt-get": [
                "texlive-latex-base",
                "texlive-latex-extra",
                "texlive-extra-utils",  # provides pdfcrop
                "ghostscript",
                "dvisvgm",
                "pdf2svg",
            ],
            "dnf": [
                "texlive-scheme-basic",
                "texlive-collection-latexextra",
                "texlive-pdfcrop",
                "ghostscript",
                "dvisvgm",
                "pdf2svg",
            ],
            "pacman": [
                "texlive-basic",
                "texlive-latexextra",
                "ghostscript",
                "dvisvgm",
                "pdf2svg",
            ],
        },
        install_urls={
            "TeX Live": "https://www.tug.org/texlive/",
            "MacTeX":   "https://www.tug.org/mactex/",
        },
    )

    # Re-probe
    have_latex = any(_has(b) for b in ("pdflatex", "xelatex", "lualatex", "latex"))
    have_gs = _has("gs")
    have_dvisvgm = _has("dvisvgm")
    have_pdf2svg = _has("pdf2svg")
    have_pdfcrop = _has("pdfcrop")

    still_missing = []
    if not have_latex: still_missing.append("latex")
    if not have_gs: still_missing.append("ghostscript")
    if not have_dvisvgm: still_missing.append("dvisvgm")
    if not have_pdf2svg: still_missing.append("pdf2svg")
    if not have_pdfcrop: still_missing.append("pdfcrop")

    if not still_missing:
        log.success("TeX toolchain installed")
        return True

    log.warn(f"TeX toolchain still incomplete: missing {', '.join(still_missing)}")
    log.step("The blueprint PDF/web build may fail until these are installed.")
    return False


def _check_leanblueprint() -> bool:
    """Install (or upgrade) the `leanblueprint` Python CLI.
    """
    already = _has("leanblueprint")


    action = "Upgrading" if already else "Installing"
    log.step(f"{action} leanblueprint via pip --user...")
    r = _run([sys.executable, "-m", "pip", "install", "--user", "--upgrade", "leanblueprint"])
    if r.returncode != 0:
        log.warn(f"pip install failed: {(r.stderr or r.stdout).strip()}")

    if _has("leanblueprint"):
        log.success(f"leanblueprint: {_version(['leanblueprint', '--version'])}")
        _ensure_path_in_rc()
        return True

    log.error("leanblueprint not found after install.")
    log.step("Install manually: pip install -U leanblueprint")
    log.step("See: https://github.com/PatrickMassot/leanblueprint")
    return False


def _check_node() -> bool:
    """Check/install Node.js 18+ via nvm (no sudo)."""
    _source_nvm()

    if _has("node") and _has("npm"):
        r = _run(["node", "-v"])
        version_str = (r.stdout or "").strip().lstrip("v")
        try:
            major = int(version_str.split(".")[0])
        except (ValueError, IndexError):
            major = 0
        if major >= 18:
            log.success(f"Node.js: v{version_str}")
            return True
        log.warn(f"Node.js {version_str} is too old (need 18+), upgrading via nvm...")

    nvm_dir = Path.home() / ".nvm"
    nvm_sh = nvm_dir / "nvm.sh"

    if not nvm_sh.exists():
        log.step("Installing nvm (Node Version Manager, to ~/.nvm, no sudo)...")
        r = _run_shell("curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash")
        if r.returncode != 0:
            log.error("nvm installation failed")
            log.step("Install manually: https://github.com/nvm-sh/nvm")
            return False
        log.success("nvm installed")

    log.step("Installing Node.js via nvm...")
    r = _run_shell(f'source "{nvm_sh}" && nvm install --lts && nvm use --lts')
    if r.returncode != 0:
        log.error(f"Node.js installation via nvm failed: {r.stderr.strip()}")
        log.step("Install manually: https://nodejs.org/")
        return False

    _source_nvm()

    if _has("node") and _has("npm"):
        r = _run(["node", "-v"])
        version_str = (r.stdout or "").strip().lstrip("v")
        log.success(f"Node.js installed: v{version_str}")
        return True

    log.error("Node.js installation succeeded but binaries not found in PATH")
    log.step('Try: source ~/.nvm/nvm.sh && nvm use --lts')
    return False


def _install_dashboard_deps() -> bool:
    ui_dir = _data_path("ui")
    if not ui_dir.exists():
        log.warn("UI files not found in package data — skipping dashboard deps")
        return False

    server_dir = ui_dir / "server"
    client_dir = ui_dir / "client"
    ok = True

    for directory, name in ((server_dir, "server"), (client_dir, "client")):
        package_json = directory / "package.json"
        if not package_json.exists():
            log.warn(f"No package.json in {name} directory — skipping")
            continue

        node_modules = directory / "node_modules"
        lock_marker = node_modules / ".package-lock.json"

        needs_install = False
        if not node_modules.exists():
            needs_install = True
        elif lock_marker.exists() and package_json.stat().st_mtime > lock_marker.stat().st_mtime:
            needs_install = True
        elif _has_wrong_platform_binaries(node_modules):
            log.warn(f"Dashboard {name} has native modules for a different platform")
            needs_install = True

        if not needs_install:
            log.success(f"Dashboard {name} dependencies up to date")
            continue

        if not _npm_install(directory, name, clean=True):
            ok = False

    ok = _build_dashboard_client(client_dir) and ok
    return ok


def _has_wrong_platform_binaries(node_modules: Path) -> bool:
    import platform as _platform

    system = _platform.system().lower()
    machine = _platform.machine().lower()

    if system == "linux":
        expected_fragments = ["linux"]
    elif system == "darwin":
        expected_fragments = ["darwin"]
    else:
        expected_fragments = ["win32", "windows"]

    if machine in ("x86_64", "amd64"):
        expected_fragments.append("x64")
    elif machine in ("arm64", "aarch64"):
        expected_fragments.append("arm64")

    for pkg_prefix in ("@esbuild", "@rollup"):
        pkg_dir = node_modules / pkg_prefix
        if not pkg_dir.is_dir():
            continue
        subdirs = [d.name for d in pkg_dir.iterdir() if d.is_dir()]
        if not subdirs:
            continue
        has_any = len(subdirs) > 0
        has_current = any(
            all(frag in d for frag in expected_fragments)
            for d in subdirs
        )
        if has_any and not has_current:
            return True

    return False


def _npm_install(directory: Path, name: str, clean: bool = False) -> bool:
    if clean:
        node_modules = directory / "node_modules"
        package_lock = directory / "package-lock.json"
        if node_modules.exists():
            log.step(f"Removing {name} node_modules for clean install...")
            import shutil as _shutil
            _shutil.rmtree(node_modules, ignore_errors=True)
        if package_lock.exists():
            package_lock.unlink()

    log.step(f"Installing dashboard {name} dependencies...")
    r = _run(
        ["npm", "install", "--no-fund", "--no-audit", "--loglevel=error"],
        cwd=str(directory),
    )
    if r.returncode != 0:
        log.error(f"Failed to install {name} dependencies: {r.stderr.strip()}")
        return False

    log.success(f"Dashboard {name} dependencies installed")
    return True


def _build_dashboard_client(client_dir: Path) -> bool:
    client_dist = client_dir / "dist" / "index.html"
    client_src = client_dir / "src"
    needs_build = False

    if not client_dist.exists():
        needs_build = True
    elif client_src.exists():
        dist_mtime = client_dist.stat().st_mtime
        for f in client_src.rglob("*"):
            if f.is_file() and f.stat().st_mtime > dist_mtime:
                needs_build = True
                break

    if not needs_build:
        log.success("Dashboard client build up to date")
        return True

    if not (client_dir / "node_modules").exists():
        log.warn("Client node_modules missing — skipping build")
        return False

    vite = client_dir / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite.exists():
        log.warn("Vite not found in node_modules — skipping build")
        return False

    log.step("Building dashboard client...")
    r = _run(
        ["node", str(vite), "build", "--logLevel", "warn"],
        cwd=str(client_dir),
    )

    if r.returncode == 0:
        log.success("Dashboard client built")
        return True

    stderr = r.stderr or ""
    if "rollup" in stderr.lower() and ("cannot find module" in stderr.lower() or "npm has a bug" in stderr.lower()):
        log.warn("Hit known rollup/npm optional dependency bug — retrying with clean install")
        if not _npm_install(client_dir, "client", clean=True):
            return False

        log.step("Retrying client build...")
        r = _run(
            ["node", str(vite), "build", "--logLevel", "warn"],
            cwd=str(client_dir),
        )
        if r.returncode == 0:
            log.success("Dashboard client built (after clean reinstall)")
            return True

    log.error(f"Client build failed: {(r.stderr or '').strip()}")
    return False


def _check_api_keys() -> None:
    keys = {
        "OPENAI_API_KEY": "OpenAI",
        "GEMINI_API_KEY": "Gemini",
        "OPENROUTER_API_KEY": "OpenRouter",
    }
    log.info("The informal agent can request proof sketches from external models.")
    log.info("This is optional — everything else works without it.")
    found_any = False
    for var, label in keys.items():
        if os.environ.get(var):
            value = os.environ[var]
            log.success(f"{var} is set ({label}) : {value[:4]}...{value[-4:]})")
            found_any = True
        else:
            log.step(f"{var} not set — export {var}=... to enable {label}")
    if not found_any:
        log.warn("No external-model API keys found. Set at least one if you want to use the informal agent.")


# ── main command ──────────────────────────────────────────────────────


def setup(
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Accept all sudo-requiring installs non-interactively. "
        "Only use in containers/CI where you trust the environment.",
    ),
    no_sudo: bool = typer.Option(
        False, "--no-sudo",
        help="Never run sudo. For any dependency that would require it, "
        "print manual install instructions and continue.",
    ),
    skip_blueprint: bool = typer.Option(
        False, "--skip-blueprint",
        help="Skip installing blueprint-related dependencies (graphviz, TeX, leanblueprint). "
        "Use this if you don't need the LaTeX blueprint feature.",
    ),
) -> None:
    """Install system-level dependencies.

    Checks and installs (without silent sudo) git, Python 3.10+, curl,
    elan/lean/lake, uv, ripgrep, Claude Code, Node.js (via nvm), dashboard
    npm dependencies, and verifies external-model API keys.

    Also installs leanblueprint and its system-level prerequisites
    (graphviz + dev headers, TeX Live, ghostscript, dvisvgm, pdf2svg,
    pdfcrop), unless --skip-blueprint is passed.

    By default, prompts before any sudo command. Use --yes to auto-accept
    (containers/CI) or --no-sudo to print manual instructions instead.

    tmux is NOT installed — Archon's parallel prover uses ProcessPoolExecutor
    and does not need tmux.
    """
    global _SUDO_MODE
    if yes and no_sudo:
        log.error("--yes and --no-sudo are mutually exclusive.")
        raise typer.Exit(1)
    if yes:
        _SUDO_MODE = "yes"
        log.warn("Running in --yes mode: sudo commands will NOT be confirmed.")
    elif no_sudo:
        _SUDO_MODE = "no"
        log.info("Running in --no-sudo mode: manual instructions will be printed for packaged deps.")
    else:
        _SUDO_MODE = "ask"

    fatal = False

    log.rule("System prerequisites")
    for check in (_check_git, _check_python, _check_curl, _check_lean):
        if not check():
            fatal = True
    if fatal:
        log.error("Required prerequisites missing — fix the errors above and re-run.")
        raise typer.Exit(1)

    log.rule("Python tooling & packages")
    _check_uv()
    _check_ripgrep()

    log.rule("Claude Code")
    _check_claude_code()

    if not skip_blueprint:
        log.rule("Blueprint system dependencies")
        _check_graphviz()
        _check_tex_toolchain()

        log.rule("leanblueprint CLI")
        _check_leanblueprint()
    else:
        log.rule("Blueprint (skipped)")
        log.info("--skip-blueprint set — not installing graphviz, TeX, or leanblueprint.")

    log.rule("Dashboard dependencies")
    node_ok = _check_node()
    if node_ok:
        _install_dashboard_deps()
    else:
        log.warn("Skipping dashboard npm install — Node.js not available")

    log.rule("Informal agent API keys (optional)")
    _check_api_keys()

    log.rule("Setup complete")
    rc = _shell_rc()
    if rc and rc.exists():
        log.warn(f"To pick up PATH changes in new terminals: source {rc}")
    log.success("All dependencies checked. You can now run: archon init")
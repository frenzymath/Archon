"""Pre-loop sanity checks: harness availability, project state, env keys."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import typer

from archon import log
from archon.commands.tooling.inner_git import InnerGit
from archon.state import read_stage


def _loop_requires_claude(project_path: Path) -> bool:
    """Whether any core loop role is routed to the Claude Code runner."""
    from archon.commands.tooling.project_config import (
        load_harness_descriptor,
        load_project_config,
        resolve_role_harness,
    )

    cfg = load_project_config(project_path)
    for role in ("plan", "prover", "review"):
        name = resolve_role_harness(cfg, role)
        descriptor = load_harness_descriptor(cfg, name)
        if descriptor.runner == "claude-code":
            return True
    return False


def preflight(project_path: Path, state_dir: Path, dry_run: bool, backend=None) -> None:
    """Verify required harness auth and that the project has been init'd.

    ``backend`` is the resolved :class:`~archon.agent.ClaudeBackend`. When it
    is a ``ClaudePBackend``, the auth probe must NOT run plain ``claude -p``
    against the default config dir — that uses a different account (often the
    one without credit) and prints a misleading failure. For claude-p we only
    check the binary is installed; auth lives in the interactive session under
    the backend's own ``CLAUDE_CONFIG_DIR``, surfaced live in the run itself.
    """
    progress = state_dir / "PROGRESS.md"

    if not dry_run:
        requires_claude = _loop_requires_claude(project_path)
        if not requires_claude:
            log.success("Core loop roles use non-Claude harnesses; skipping Claude Code auth check")
        else:
            _preflight_claude_backend(backend)

    if not progress.exists():
        log.error(f"No project state found. Run: archon init {project_path}")
        raise typer.Exit(1)

    stage = read_stage(progress)
    if "init" in stage:
        log.error(f"Project is still in init stage. Run: archon init {project_path}")
        raise typer.Exit(1)


def _preflight_claude_backend(backend=None) -> None:
    from archon.agent import ClaudePBackend, InteractiveBackend

    if isinstance(backend, InteractiveBackend):
        # Human-driven foreground claude; auth is the user's interactive
        # session. Like claude-p, the plain `claude -p` auth probe below
        # is misleading for a subscription account, so skip it.
        if not shutil.which("claude"):
            log.error("Claude Code is not installed. Run: archon setup")
            raise typer.Exit(1)
        log.success(
            "Using interactive backend (you will drive claude in the "
            "foreground; provers run serially)."
        )
    elif isinstance(backend, ClaudePBackend):
        if not shutil.which("claude-p"):
            log.error(
                "claude-p is not installed. Install the maintained fork "
                "(adds --trust-workspace for headless TUI runs):\n"
                "  uv tool install --force "
                "git+https://github.com/AxelDlv00/claude-p\n"
                "  (or: pip install "
                "git+https://github.com/AxelDlv00/claude-p)"
            )
            raise typer.Exit(1)
        cfg = backend.config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
        log.success(f"Using claude-p backend (auth via Claude Code session in {cfg})")
    else:
        if not shutil.which("claude"):
            log.error("Claude Code is not installed. Run: archon setup")
            raise typer.Exit(1)
        r = subprocess.run(
            ["claude", "-p", "reply with OK", "--no-session-persistence"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            log.error("Claude Code cannot run. Check: claude auth, ANTHROPIC_API_KEY, network.")
            # raise typer.Exit(1)
        else:
            log.success("Claude Code is authenticated and ready")


def warn_if_lake_unbuilt(project_path: Path) -> None:
    """Warn (don't fail) when the project looks unbuilt.

    A cold ``.lake/build`` is the most common cause of the LSP MCP
    server returning ``success: false`` on the prover's first query —
    which the model historically misread as "the tool doesn't exist"
    and fell back to running ``lean_goal`` as a shell command (which
    obviously fails). Surfacing this up front gives the user a chance
    to ``lake build`` before burning a prover round on a cold cache.

    Skipped silently when there's no ``lakefile.lean`` / ``lakefile.toml``
    (not a Lake project, nothing to warn about).
    """
    has_lake = (
        (project_path / "lakefile.lean").exists()
        or (project_path / "lakefile.toml").exists()
    )
    if not has_lake:
        return
    build_dir = project_path / ".lake" / "build"
    if build_dir.is_dir() and any(build_dir.iterdir()):
        return

    log.warn(
        "No .lake/build artifacts found. The Lean LSP MCP server "
        "may return success: false on its first call and the prover "
        "could waste effort interpreting that as a missing tool. "
        "Consider running `lake build` once before starting the loop."
    )


def warn_if_inner_dirty(project_path: Path) -> None:
    """If the inner git has leftover state, tell the user; do not block."""
    inner = InnerGit(project_path)
    if not inner.is_initialized() or not inner.is_dirty():
        return

    log.warn(
        "Inner git has uncommitted agent work — leftover from a previous "
        "run or manual edits. This is fine: the loop will pick up whatever "
        "is on disk, and the next phase commit will capture it."
    )


def _probe_informal_key(var: str, url: str, timeout: int = 6) -> str | None:
    """Return None if the key is valid (or unreachable), an error string otherwise.

    Makes a GET /models request — zero tokens, no model needed.  Only
    flags definitive auth failures (HTTP 401/403); network errors and
    timeouts are silently ignored so a flaky connection never blocks a run.
    """
    import urllib.error
    import urllib.request

    key = os.environ.get(var, "")
    if not key:
        return None
    try:
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {key}"}
        )
        urllib.request.urlopen(req, timeout=timeout)
        return None  # success
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return f"HTTP {e.code}"
        return None  # other HTTP errors are not auth failures
    except Exception:
        return None  # network / timeout — don't block


# MOONSHOT_API_KEY keys whose prefix indicates they are Kimi-for-Coding
# keys (Anthropic-compatible, coding-agent-only) and won't work for
# direct OpenAI-compatible informal-agent calls.
_KIMI_CODING_PREFIXES = ("sk-kimi-",)


def check_informal_agent_keys() -> None:
    """Warn (don't fail) if no usable external-LLM key exists for the informal agent.

    Checks both key presence and, for Moonshot/DeepSeek, a quick /models
    probe to catch coding-agent-only or invalid keys early.
    """
    keys = (
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
    )
    set_keys = [k for k in keys if os.environ.get(k)]
    if not set_keys:
        log.warn(
            "No API keys for informal agent "
            "(DEEPSEEK_API_KEY / MOONSHOT_API_KEY / OPENROUTER_API_KEY / "
            "OPENAI_API_KEY / GEMINI_API_KEY)"
        )
        log.step(
            "Provers will work without it, but may struggle on hard sorries "
            "where external LLM help would be useful."
        )
        return

    # Validate Moonshot key — "sk-kimi-" prefix means Kimi-for-Coding
    # (Anthropic-compatible). It works for the informal agent via the
    # kimi-anthropic provider (coding endpoint), but NOT the OpenAI-compatible
    # `kimi` route, which returns 401. The informal agent auto-detects the
    # prefix and routes accordingly, so the key stays usable — we just note
    # it and skip the OpenAI-style /models probe below (which would 401).
    moonshot_key = os.environ.get("MOONSHOT_API_KEY", "")
    moonshot_is_coding = moonshot_key and any(
        moonshot_key.startswith(p) for p in _KIMI_CODING_PREFIXES
    )
    if moonshot_is_coding:
        log.step(
            "MOONSHOT_API_KEY is a Kimi-for-Coding key (sk-kimi-) — the "
            "informal agent will use the Anthropic-compatible coding endpoint "
            "(provider 'kimi-anthropic', base from MOONSHOT_BASE_URL). The "
            "OpenAI-compatible 'kimi' provider won't work with this key."
        )

    # Quick /models probe for keys that look plausible but may still be invalid.
    # The Moonshot probe is OpenAI-compatible, so skip it for coding keys.
    _PROBES = {
        "DEEPSEEK_API_KEY": "https://api.deepseek.com/v1/models",
    }
    if not moonshot_is_coding:
        _PROBES["MOONSHOT_API_KEY"] = "https://api.moonshot.cn/v1/models"
    for var, probe_url in _PROBES.items():
        if var not in set_keys:
            continue
        err = _probe_informal_key(var, probe_url)
        if err:
            log.warn(
                f"{var} is set but returns {err} (invalid auth) — "
                f"archon-informal-agent.py cannot use it. "
                f"Set a valid key (standard Moonshot from platform.moonshot.cn / "
                f"DeepSeek / OpenRouter / OpenAI / Gemini) if you want the "
                f"informal-agent tool live."
            )
            set_keys = [k for k in set_keys if k != var]

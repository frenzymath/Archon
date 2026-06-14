"""Verify Archon setup for a project."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from importlib import resources
from pathlib import Path
from typing import ClassVar

import typer

from archon import log


# ── primitives ────────────────────────────────────────────────────────


def _has(binary: str) -> bool:
    return shutil.which(binary) is not None


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _version(cmd: list[str]) -> str:
    try:
        r = _run(cmd)
        return (r.stdout or r.stderr).strip().splitlines()[0]
    except Exception:
        return "unknown"


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _data_path(sub_path: str = "") -> Path:
    root = resources.files("archon").joinpath(".archon-src")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


# ── check classes ─────────────────────────────────────────────────────

CheckRow = tuple[str, str, str]  # (name, status, detail)


class DoctorCheck(ABC):
    """One probe in the doctor report. Returns rows for the results table."""

    title: ClassVar[str] = ""

    @abstractmethod
    def run(self) -> list[CheckRow]:
        ...


class LeanToolchainDoctorCheck(DoctorCheck):
    title = "Lean toolchain"

    def run(self) -> list[CheckRow]:
        rows: list[CheckRow] = []
        for tool in ("elan", "lean", "lake"):
            if _has(tool):
                rows.append((tool, "ok", _version([tool, "--version"])))
            else:
                rows.append((tool, "error", "not found in PATH"))
        return rows


class PythonToolsDoctorCheck(DoctorCheck):
    title = "Python tools"

    def run(self) -> list[CheckRow]:
        rows: list[CheckRow] = []
        v = sys.version_info
        if v >= (3, 10):
            rows.append(("python", "ok", f"{v.major}.{v.minor}.{v.micro}"))
        else:
            rows.append(("python", "error", f"{v.major}.{v.minor} (need 3.10+)"))

        if _has("uv"):
            rows.append(("uv", "ok", _version(["uv", "--version"])))
        else:
            rows.append(("uv", "error", "not found"))
        return rows


class ClaudeCodeDoctorCheck(DoctorCheck):
    title = "Claude Code"

    def __init__(self, *, skip_auth: bool = False) -> None:
        self.skip_auth = skip_auth

    def run(self) -> list[CheckRow]:
        rows: list[CheckRow] = []
        if not _has("claude"):
            rows.append(("claude", "error", "not installed — run: archon setup"))
            return rows

        if self.skip_auth:
            rows.append(
                ("claude", "ok", f"{_version(['claude', '--version'])} (auth skipped)"),
            )
            return rows

        rows.append(("claude", "ok", _version(["claude", "--version"])))
        r = _run(["claude", "-p", "reply with OK", "--no-session-persistence"])
        if r.returncode == 0:
            rows.append(("claude auth", "ok", "authenticated"))
        else:
            rows.append(("claude auth", "error", "not authenticated — check API key"))
        return rows


class ApiKeysDoctorCheck(DoctorCheck):
    title = "API keys"

    _KEYS = {
        "DEEPSEEK_API_KEY": "DeepSeek",
        "MOONSHOT_API_KEY": "Kimi (Moonshot)",
        "OPENROUTER_API_KEY": "OpenRouter",
        "OPENAI_API_KEY": "OpenAI",
        "GEMINI_API_KEY": "Gemini",
    }

    # "sk-kimi-" prefix → Kimi-for-Coding key; Anthropic-compatible. Usable by
    # the informal agent via the kimi-anthropic provider (coding endpoint), but
    # not the OpenAI-compatible `kimi` route.
    _KIMI_CODING_PREFIX = "sk-kimi-"

    _PROBES: dict[str, str] = {
        "MOONSHOT_API_KEY": "https://api.moonshot.cn/v1/models",
        "DEEPSEEK_API_KEY": "https://api.deepseek.com/v1/models",
    }

    def _probe(self, var: str, url: str, timeout: int = 5) -> str | None:
        """Return None if key is valid/unreachable; error string on 401/403."""
        import urllib.error
        import urllib.request

        key = os.environ.get(var, "")
        if not key:
            return None
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
            urllib.request.urlopen(req, timeout=timeout)
            return None
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return f"HTTP {e.code}"
            return None
        except Exception:
            return None

    def run(self) -> list[CheckRow]:
        rows: list[CheckRow] = []
        found_any = False
        for var, label in self._KEYS.items():
            val = os.environ.get(var, "")
            if not val:
                rows.append((f"{label} key", "skipped", "not set"))
                continue

            # Kimi-for-Coding key — Anthropic-compatible. Usable by the informal
            # agent via the kimi-anthropic provider (and by multilane), but not
            # the OpenAI-compatible `kimi` route, so skip the /models probe.
            if var == "MOONSHOT_API_KEY" and val.startswith(self._KIMI_CODING_PREFIX):
                rows.append((
                    f"{label} key",
                    "ok",
                    "sk-kimi- = Kimi-for-Coding (informal agent uses kimi-anthropic)",
                ))
                found_any = True
                continue

            # Probe known endpoints for auth validity
            probe_url = self._PROBES.get(var)
            if probe_url:
                err = self._probe(var, probe_url)
                if err:
                    rows.append((f"{label} key", "warning", f"set but {err} — invalid or wrong key"))
                    continue

            rows.append((f"{label} key", "ok", f"${var} is set"))
            found_any = True

        if not found_any:
            rows.append(
                ("informal agent", "warning", "no valid API keys — informal agent won't work"),
            )
        return rows


class PackageDataDoctorCheck(DoctorCheck):
    title = "Package data"

    _CHECKS = {
        "templates": "archon-template/PROGRESS.md",
        "prompts": "prompts",
        "skills": "skills/lean4/.claude-plugin/plugin.json",
        # The v0.2.0 PR renamed the bundled directory from ``agents/`` to
        # ``subagents/``; the doctor's lookup has to follow or it emits
        # a permanent false-positive ``✗ 1 error(s)``.
        "subagents": "subagents",
        "scripts": "scripts",
        "tools": "tools",
    }

    def run(self) -> list[CheckRow]:
        rows: list[CheckRow] = []
        for name, sub in self._CHECKS.items():
            p = _data_path(sub)
            if p.exists():
                rows.append((f"data: {name}", "ok", str(p.parent if p.is_file() else p)))
            else:
                rows.append((f"data: {name}", "error", f"not found at {p}"))
        return rows


class ProjectStateDoctorCheck(DoctorCheck):
    """Check the project's `.archon/` state directory."""

    title = "Project state"

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path

    def run(self) -> list[CheckRow]:
        rows: list[CheckRow] = []
        state_dir = self.project_path / ".archon"

        if not state_dir.is_dir():
            rows.append(
                (".archon/", "error", f"not found — run: archon init {self.project_path}"),
            )
            return rows
        rows.append((".archon/", "ok", str(state_dir)))

        for name in ("PROGRESS.md", "AGENTS.md"):
            f = state_dir / name
            if f.exists():
                rows.append((name, "ok", f"{f.stat().st_size:,} bytes"))
            else:
                rows.append((name, "error", "missing"))

        prompts_dir = state_dir / "prompts"
        if prompts_dir.is_dir():
            prompt_count = len(list(prompts_dir.glob("*.md")))
            rows.append(("prompts/", "ok", f"{prompt_count} prompt file(s)"))
        else:
            rows.append(("prompts/", "error", "missing"))

        progress = state_dir / "PROGRESS.md"
        if progress.exists():
            stage = self._parse_stage(progress)
            rows.append(("current stage", "ok", stage))

        journal = state_dir / "proof-journal" / "sessions"
        if journal.is_dir():
            sessions = len(list(journal.glob("session_*")))
            rows.append(("proof journal", "ok", f"{sessions} session(s)"))
        else:
            rows.append(("proof journal", "skipped", "no sessions yet"))

        log_dir = state_dir / "logs"
        if log_dir.is_dir():
            iters = len(list(log_dir.glob("iter-*")))
            rows.append(("logs/", "ok", f"{iters} iteration(s)"))
        else:
            rows.append(("logs/", "skipped", "no iterations yet"))

        return rows

    @staticmethod
    def _parse_stage(progress: Path) -> str:
        lines = progress.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("## Current Stage"):
                if i + 1 < len(lines):
                    return lines[i + 1].strip()
                break
        return "unknown"


class ProjectClaudeDoctorCheck(DoctorCheck):
    """Check `.claude/` (skills, tools, MCP)."""

    title = "Project Claude config"

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path

    def run(self) -> list[CheckRow]:
        rows: list[CheckRow] = []
        claude_dir = self.project_path / ".claude"

        if not claude_dir.is_dir():
            rows.append((".claude/", "warning", "not found — skills may not be installed"))
            return rows

        skills_dir = claude_dir / "skills"
        if skills_dir.is_dir():
            skill_count = len([d for d in skills_dir.iterdir() if d.is_dir()])
            rows.append(("user skills", "ok", f"{skill_count} skill(s)"))
        else:
            rows.append(("user skills", "skipped", "none"))

        agents_dir = claude_dir / "agents"
        expected_agents = {"refactor.md", "challenger.md", "analogy.md"}
        if agents_dir.is_dir():
            present = {f.name for f in agents_dir.glob("*.md")}
            missing = expected_agents - present
            if missing:
                rows.append((
                    "subagents",
                    "warning",
                    f"missing: {', '.join(sorted(missing))} — run: archon init",
                ))
            else:
                rows.append((
                    "subagents",
                    "ok",
                    f"{len(present)} agent(s)",
                ))
        else:
            rows.append((
                "subagents",
                "warning",
                "not found — run: archon init",
            ))

        agent = claude_dir / "tools" / "archon-informal-agent.py"
        if agent.exists():
            if agent.is_symlink():
                rows.append(("informal agent", "ok", f"symlink → {agent.resolve()}"))
            else:
                rows.append(("informal agent", "ok", "copied"))
        else:
            rows.append(("informal agent", "warning", "not found"))

        mcp_json = self.project_path / ".mcp.json"
        if mcp_json.exists():
            data = _read_json(mcp_json)
            servers = list(data.get("mcpServers", {}).keys())
            archon_lsp = [s for s in servers if "archon" in s.lower()]
            if archon_lsp:
                rows.append(("archon-lean-lsp", "ok", ", ".join(archon_lsp)))
            else:
                rows.append((
                    "archon-lean-lsp",
                    "warning",
                    f"not found (servers: {', '.join(servers) or 'none'})",
                ))
        else:
            rows.append(("MCP config", "warning", ".mcp.json not found"))

        return rows


class SorryCountDoctorCheck(DoctorCheck):
    title = "Sorry count"

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path

    def run(self) -> list[CheckRow]:
        rows: list[CheckRow] = []
        analyzer = _data_path("skills/lean4/lib/scripts/sorry_analyzer.py")
        if not analyzer.exists():
            rows.append(("sorry count", "skipped", "sorry_analyzer.py not found"))
            return rows

        r = _run([sys.executable, str(analyzer), str(self.project_path), "--format=summary"])
        if r.returncode == 0 and r.stdout.strip():
            rows.append(("sorry count", "ok", r.stdout.strip().splitlines()[-1]))
        else:
            rows.append(("sorry count", "skipped", "could not run analyzer"))
        return rows


# ── orchestrator ──────────────────────────────────────────────────────


class DoctorCommand:
    """Runs all DoctorCheck classes and emits a final summary."""

    def __init__(self, project_path: str, *, skip_auth: bool = False) -> None:
        self.project_path = project_path
        self.skip_auth = skip_auth

    def run(self) -> None:
        resolved = Path(self.project_path).resolve()
        all_rows: list[CheckRow] = []

        log.header("System Tools")
        rows = (
            LeanToolchainDoctorCheck().run()
            + PythonToolsDoctorCheck().run()
            + ClaudeCodeDoctorCheck(skip_auth=self.skip_auth).run()
        )
        log.results_table(rows, title="System")
        all_rows.extend(rows)

        log.header("API Keys (optional)")
        rows = ApiKeysDoctorCheck().run()
        log.results_table(rows, title="External Models")
        all_rows.extend(rows)

        log.header("Package Data")
        rows = PackageDataDoctorCheck().run()
        log.results_table(rows, title="Bundled Data")
        all_rows.extend(rows)

        log.header(f"Project: {resolved.name}")
        rows = ProjectStateDoctorCheck(resolved).run()
        log.results_table(rows, title="State (.archon/)")
        all_rows.extend(rows)

        rows = ProjectClaudeDoctorCheck(resolved).run()
        log.results_table(rows, title="Claude Config (.claude/)")
        all_rows.extend(rows)

        if (resolved / ".archon").is_dir():
            rows = SorryCountDoctorCheck(resolved).run()
            if rows:
                log.results_table(rows, title="Lean Project")
                all_rows.extend(rows)

        self._summarize(all_rows)

    @staticmethod
    def _summarize(rows: list[CheckRow]) -> None:
        errors = sum(1 for _, s, _ in rows if s == "error")
        warnings = sum(1 for _, s, _ in rows if s == "warning")
        log.rule()
        if errors:
            log.error(f"{errors} error(s), {warnings} warning(s) — fix errors before running.")
            raise typer.Exit(1)
        elif warnings:
            log.warn(f"All clear with {warnings} warning(s).")
        else:
            log.success("All checks passed.")


# ── Typer entry point ─────────────────────────────────────────────────


def doctor(
    project_path: str = typer.Argument(".", help="Path to Lean project"),
    skip_auth: bool = typer.Option(
        False, "--skip-auth",
        help="Skip Claude Code authentication test (faster).",
    ),
) -> None:
    """Verify the full Archon setup.

    Checks system tools, package data, project state, skills, MCP,
    and reports any issues.
    """
    DoctorCommand(project_path, skip_auth=skip_auth).run()

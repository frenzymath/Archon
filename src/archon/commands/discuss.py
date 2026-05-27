"""Interactive discussion session with Archon about project state."""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from importlib import resources
from pathlib import Path
from typing import Optional

import typer

from archon import log
from archon.agent import ClaudeAgent, ClaudeBackend, DEFAULT_MODEL
from archon.state import read_stage
from archon.commands.tooling.project_config import load_project_config, resolve_claude_backend


_HINT_PATTERN = re.compile(r"^- \[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] .+$")


def _data_path(sub_path: str = "") -> Path:
    root = resources.files("archon").joinpath(".archon-src")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


def _read_if_exists(path: Path, max_chars: int = 3000) -> str:
    if not path.exists():
        return ""
    content = path.read_text()
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n... (truncated)"
    return content


def _count_hints(hints_file: Path) -> list[str]:
    """Return list of hint lines matching the archon-hint format."""
    if not hints_file.exists():
        return []
    return [
        line.strip() for line in hints_file.read_text().splitlines()
        if _HINT_PATTERN.match(line.strip())
    ]


class DiscussionContext:
    """Builds the pre-loaded context block sent to Claude.

    Splits out the read-many-files work so the command class stays a
    clean orchestrator.
    """

    def __init__(self, project_path: Path) -> None:
        self.project_path = project_path
        self.state_dir = project_path / ".archon"

    def gather(self) -> dict[str, str]:
        return {
            "progress": _read_if_exists(self.state_dir / "PROGRESS.md"),
            "task_pending": _read_if_exists(self.state_dir / "task_pending.md"),
            "task_done": _read_if_exists(self.state_dir / "task_done.md"),
            "project_status": _read_if_exists(self.state_dir / "PROJECT_STATUS.md"),
            "user_hints": _read_if_exists(self.state_dir / "USER_HINTS.md", max_chars=1000),
            "sorry_info": self._sorry_summary(),
            "journal_summary": self._latest_journal_summary(),
        }

    def _sorry_summary(self) -> str:
        analyzer = _data_path("skills/lean4/lib/scripts/sorry_analyzer.py")
        if not analyzer.exists():
            return "(sorry_analyzer not available)"
        try:
            r = subprocess.run(
                [sys.executable, str(analyzer), str(self.project_path), "--format=markdown"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
        return "(could not run sorry_analyzer)"

    def _latest_journal_summary(self) -> str:
        sessions_dir = self.state_dir / "proof-journal" / "sessions"
        if not sessions_dir.exists():
            return ""
        session_dirs = sorted(sessions_dir.glob("session_*"))
        if not session_dirs:
            return ""
        latest = session_dirs[-1]
        parts = []
        for name in ("summary.md", "recommendations.md"):
            f = latest / name
            if f.exists():
                content = f.read_text()
                if len(content) > 3000:
                    content = content[:3000] + "\n\n... (truncated)"
                parts.append(f"### {latest.name}/{name}\n\n{content}")
        return "\n\n".join(parts)


class DiscussCommand:
    """Run an interactive discussion session via Claude."""

    def __init__(
        self,
        project_path: str,
        *,
        focus: str | None = None,
        model: str = DEFAULT_MODEL,
        backend: ClaudeBackend | None = None,
    ) -> None:
        self.project_path = project_path
        self.focus = focus
        self.model = model
        self.backend = backend

    def run(self) -> None:
        resolved = Path(self.project_path).resolve()
        state_dir = resolved / ".archon"
        progress_file = state_dir / "PROGRESS.md"
        hints_file = state_dir / "USER_HINTS.md"

        self._preflight(resolved, state_dir, progress_file)

        try:
            stage = read_stage(progress_file)
        except (FileNotFoundError, ValueError):
            stage = "unknown"

        context = DiscussionContext(resolved).gather()
        hints_before = len(_count_hints(hints_file))

        prompt = self._build_prompt(resolved, state_dir, stage, hints_file, context)

        self._announce(resolved, stage)
        try:
            ClaudeAgent(model=self.model, role="discuss", backend=self.backend or ClaudeBackend()).run_interactive(
                prompt, cwd=resolved,
            )
        except KeyboardInterrupt:
            log.info("Discussion ended")

        self._post_session_summary(hints_file, hints_before)

    # ── private ────────────────────────────────────────────────────────

    @staticmethod
    def _preflight(resolved: Path, state_dir: Path, progress_file: Path) -> None:
        if not state_dir.is_dir():
            log.error(f"No .archon/ found in {resolved}")
            log.step(f"Run: archon init {resolved}")
            raise typer.Exit(1)
        if not progress_file.exists():
            log.error("No PROGRESS.md found")
            log.step(f"Run: archon init {resolved}")
            raise typer.Exit(1)

    def _build_prompt(
        self,
        resolved: Path,
        state_dir: Path,
        stage: str,
        hints_file: Path,
        context: dict[str, str],
    ) -> str:
        focus_section = ""
        if self.focus:
            focus_section = f"""
FOCUS: The mathematician wants to discuss **{self.focus}** specifically.
- If it looks like a file path, read it and explain the current state of every sorry in it.
- If it looks like a theorem/lemma name, find it across the project and explain what has been tried.
- Start the conversation by summarizing the current state of {self.focus}.
"""

        sorry_analyzer_path = _data_path("skills/lean4/lib/scripts/sorry_analyzer.py")
        project_name = resolved.name
        progress_content = context["progress"]
        sorry_info = context["sorry_info"]
        project_status = context["project_status"]
        task_pending = context["task_pending"]
        task_done = context["task_done"]
        user_hints = context["user_hints"]
        journal_summary = context["journal_summary"]

        return textwrap.dedent(f"""\
You are an Archon discussion advisor for project '{project_name}'.
The mathematician wants to understand the current state of the project,
ask questions about blockers, and provide mathematical insights.

Project directory: {resolved}
Project state directory: {state_dir}
Current stage: {stage}

Read {state_dir}/CLAUDE.md for full project context.
{focus_section}
## Rules

### What you MUST NOT do without explicit user consent
- **NEVER edit files without asking first.** Default mode is read-only.
- **NEVER launch or invoke other agents (plan, prover, review).**
- **NEVER run `lake build`, `lake env lean`, or commands that compile the project.**
- **NEVER run `archon loop`, `archon init`, or any archon subcommand.**

### What you CAN always do (no consent needed)
- **Read any file** in the project — .lean files, state files, logs, journal entries, blueprints.
- **Use Lean LSP MCP tools** (read-only inspection):
  - `lean_goal` — check the goal state at a specific line/column in a .lean file
  - `lean_diagnostic_messages` — check current errors/warnings in a file
  - `lean_leansearch` — semantic search for Mathlib lemmas ("continuous function on compact set")
  - `lean_loogle` — type-pattern search ("_ → Continuous _")
  - `lean_local_search` — search within the project's own declarations
  - Any other read-only LSP query
- **Run the sorry analyzer**:
  `python3 {sorry_analyzer_path} {resolved} --format=markdown`
  `python3 {sorry_analyzer_path} {resolved} --format=summary`
- **Explain** proof strategies, Lean errors, Mathlib APIs, agent decisions, blueprint content.
- **Verify proof ideas** by checking goal states and searching for lemmas — tell the
  mathematician whether an approach would likely work, without actually editing any file.

### Dynamic write consent — how to request permission

You may write to files **only after the user explicitly approves** a specific request in
this session. To request consent:
1. State the **exact file path** you want to edit.
2. Show the **exact change** (diff or replacement text, not a description).
3. Explain **why** it is needed.
4. Wait for the user to reply with explicit approval ("yes", "go ahead", "do it", etc.).
   A non-committal response is NOT approval. Never interpret silence or "maybe" as consent.

**Per-file warnings you MUST include in your consent request:**
- `.archon/prompts/*.md` → "⚠ This modifies what Archon agents are told on every future iteration."
- `.lean` files → "I will only add or edit comments (`/- ... -/` or `--`), not proof code."
- `blueprint/src/chapters/*.tex` → "I will only add `% NOTE:` annotations or edit existing comments."
- `.archon/PROGRESS.md`, `.archon/STRATEGY.md`, etc. → "This is a live state file read by the next plan agent."

### Writing hints to USER_HINTS.md

`USER_HINTS.md` has two sections with different lifecycles:
- `## Temporary hints` — consumed by the next plan phase and then cleared. Use for one-shot
  steering ("try route X this iter", "skip Lane F this round").
- `## Persistent hints` — NEVER auto-cleared. These are standing directives that survive every
  iteration. The plan agent treats them as higher priority than its own prompt instructions.
  Use for lasting constraints ("never accept axiom X", "don't touch theorem Y until I say so").

When the mathematician provides an insight or direction to record, decide which section fits:
- One-shot steering → Temporary
- Project-wide constraint or override → Persistent

Tell the user which section you're targeting and why, then get their confirmation before writing.

**Format** — one hint per line:
```
- [YYYY-MM-DDTHH:MM:SSZ] hint text here
```
Append under the correct `## Temporary hints` or `## Persistent hints` heading. Each hint must
be self-contained — the plan agent reads it without this conversation's context.

This format is compatible with `archon hint show` and `archon hint clear`.

## Current project state (pre-loaded)

### PROGRESS.md
{progress_content}

### Sorry analysis
{sorry_info}

### PROJECT_STATUS.md
{project_status if project_status else "(not yet created — no review has run yet)"}

### task_pending.md (attempt history, dead ends)
{task_pending if task_pending else "(empty)"}

### task_done.md (completed theorems)
{task_done if task_done else "(empty)"}

{("### Latest proof journal" + chr(10) + chr(10) + journal_summary) if journal_summary else ""}

### USER_HINTS.md (pending hints)
{user_hints if user_hints else "(no pending hints)"}

## How to behave

1. **Start with a concise status overview**: how many sorries remain, which files are
   closest to completion, which are blocked, what the main obstacles are.
2. **Be concrete**: when the mathematician asks "why is X still sorry", trace through
   task_pending.md and the proof journal to show what was tried and why it failed.
   Use `lean_goal` to show the current goal state if relevant.
3. **Be honest**: if something was tried 3 times and failed, say so clearly. If a sorry
   is probably hard, explain why. Don't sugarcoat.
4. **Verify before claiming**: if the mathematician suggests "can you use lemma X here?",
   actually search for it with lean_leansearch or lean_loogle, check the types, and
   give an informed answer. Don't guess.
5. **Record insights as hints**: when the discussion produces an actionable insight,
   offer to write it as a hint. Confirm the exact text and section (Temporary vs Persistent)
   with the mathematician first. The plan agent will read it at the start of the next
   `archon loop` iteration.
6. **Offer edits when helpful**: if the mathematician wants a targeted change to a prompt,
   a comment in a .lean file, or a state file update, offer to do it via the consent flow
   above rather than just saying "I can't do that".""")

    def _announce(self, resolved: Path, stage: str) -> None:
        log.header("Archon Discuss")
        log.key_value({
            "Project": str(resolved),
            "Stage": stage,
            "Focus": self.focus or "(general)",
            "Writable": "files with explicit per-request user consent",
        })

        log.info("Starting interactive session — Ctrl+C to exit")
        log.step(
            "Archon has Lean LSP access to inspect goals, search lemmas, "
            "and check diagnostics",
        )
        log.step("Any insights will be recorded as hints for the next loop iteration")
        log.step("Review hints afterward: archon hint show -p " + str(resolved))
        log.rule()

    @staticmethod
    def _post_session_summary(hints_file: Path, hints_before: int) -> None:
        log.rule()
        hints_after = _count_hints(hints_file)
        new_hints = hints_after[hints_before:]

        if new_hints:
            log.success(f"{len(new_hints)} new hint(s) recorded during this session:")
            for h in new_hints:
                log.step(h)
            log.info(
                "These will be picked up by the plan agent at the next "
                "`archon loop` iteration",
            )
        else:
            log.info("No new hints recorded during this session")

        if len(hints_after) > 0:
            log.info(f"Total pending hints: {len(hints_after)}")

        log.info("Discussion complete")


def discuss(
    project_path: str = typer.Argument(".", help="Path to Lean project"),
    focus: Optional[str] = typer.Option(
        None, "--focus", "-f",
        help="Focus on a specific file or theorem (e.g. 'Algebra/WLocal.lean' or 'wLocal_iff').",
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-M",
        help=(
            "Model alias. Anthropic: 'opus', 'sonnet', 'haiku' or a full id. "
            "Non-Anthropic (uses .archon/.env credentials): 'kimi', 'deepseek'."
        ),
    ),
    claude_backend: Optional[str] = typer.Option(
        None, "--claude-backend",
        help=(
            "How 'claude -p' is invoked for every headless agent run. "
            "'default': plain claude -p. "
            "'vscode': sets CLAUDE_CODE_ENTRYPOINT=claude-vscode. "
            "'desktop': sets CLAUDE_CODE_ENTRYPOINT=claude-desktop. "
            "(default from .archon/config.json loop.claude_backend or 'default')"
        ),
    ),
) -> None:
    """Start an interactive discussion about the project.

    Opens a conversation with full project context and Lean tooling so you can:
    \b
      - Ask why specific sorries remain unfilled
      - Understand what approaches were tried and why they failed
      - Check goal states and search for Mathlib lemmas interactively
      - Verify whether a proof idea would work (without modifying files)
      - Record insights as hints for the next plan agent iteration

    \b
    This command never modifies .lean files or agent state files.
    The only file it writes to is USER_HINTS.md, in the same format
    as `archon hint add`, so you can review/clear hints with
    `archon hint show` and `archon hint clear`.

    \b
    Examples:
      archon discuss .
      archon discuss . --focus Algebra/WLocal.lean
      archon discuss . --focus wLocal_iff
    """
    project_config = load_project_config(Path(project_path))
    backend = resolve_claude_backend(project_config, cli_value=claude_backend)
    DiscussCommand(project_path, focus=focus, model=model, backend=backend).run()

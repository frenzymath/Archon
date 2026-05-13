"""Prompt builders for Archon's plan / prover / review phases.

The actual ``claude`` invocation lives in :mod:`archon.agent`. This
module only constructs the prompt strings handed to the agent. The
session-end inspection helpers (which read the JSONL log after a run)
live in :mod:`archon.session_log`.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from archon.commands.tooling.blueprint import lean_file_to_chapter_slug


# ── context injection helpers ─────────────────────────────────────────


def _references_summary(
    state_dir: Path | None,
    project_path: Path | None = None,
    max_chars: int = 3000,
) -> str:
    """Read references/summary.md and return a bounded chunk for prompts.

    Empty string if the file doesn't exist or contains only the template.
    """
    if project_path is None and state_dir is not None:
        # state_dir is <project>/.archon — its parent is the project.
        project_path = state_dir.parent

    if project_path is None:
        return ""
    summary = project_path / "references" / "summary.md"
    if not summary.exists():
        return ""
    try:
        content = summary.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

    # Strip the template placeholder; if only placeholder text remains, skip.
    non_meta = [
        l for l in content.splitlines()
        if l.strip() and not l.strip().startswith("<!--")
    ]
    if len(non_meta) <= 3:  # heading + table header + separator row
        return ""

    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n... (truncated)"
    return content


def _blueprint_chapter_hint(project_path: Path, rel_lean_path: str) -> str:
    """Build the 'your blueprint chapter is at X; create it if missing' hint.

    Empty string if no blueprint exists.
    """
    if not (project_path / "blueprint" / "src").is_dir():
        return ""
    slug = lean_file_to_chapter_slug(rel_lean_path)
    rel_chapter = f"blueprint/src/chapters/{slug}.tex"
    return dedent(f"""\
        Blueprint chapter for your file: {rel_chapter}
        - Read it BEFORE writing any Lean code — it contains the informal proof
          written by the plan agent.
        - After you formalize a declaration, mark its blueprint environment with \\leanok.
        - If the chapter file does not yet exist, create it with a minimal \\chapter block
          and note in your task_results that the plan agent should flesh it out.""")


def debug_feedback_block(enabled: bool, state_dir: Path, role: str, iter_num: int) -> str:
    """Inject the optional developer-feedback channel instructions.

    Agents append free-form notes via `>>` to a path they are told never
    to read. When the flag is off, returns empty string and nothing is
    injected — zero token cost on normal runs.
    """
    if not enabled:
        return ""
    feedback_path = state_dir / ".debug-feedback" / "debug_feedback.md"
    return dedent(f"""

        ## Developer feedback channel (optional)

        If during this iteration you notice something that would make Archon
        better — a missing capability, redundant functionality, a
        prompt instruction that contradicts itself, a tool you wish existed,
        new ideas for better efficiency, etc — you may leave a short note
        for the developer by appending to this file with a bash heredoc:

            mkdir -p {feedback_path.parent}
            cat >> {feedback_path} <<'EOF'

            ## iter-{iter_num:03d} · {role}

            <your note here, one concrete observation, under ~200 words>
            EOF

        Rules:
        - This file is WRITE-ONLY from your perspective. Do NOT read it,
          cat it, grep it, or open it in any tool. It is for the developer.
        - Only leave a note if you have something concrete to say. Empty or
          generic feedback ("everything went fine") is noise — skip it.
        - One concrete observation per note. Keep it under ~200 words.
        - This is optional and does not affect your task. Skip it if nothing
          comes to mind.
        """)

# ── prompt builders ───────────────────────────────────────────────────


def build_plan_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
    iter_num: int,
    *, ignore_multilane: bool = False, debug_feedback: bool = False,
) -> str:
    refs = _references_summary(state_dir, project_path)
    refs_block = ""
    if refs:
        refs_block = dedent(f"""

            ## References available for this project

            The file {project_path / 'references' / 'summary.md'} lists the informal sources backing this project.
            Re-read the relevant source (from the `references/` directory) before assigning
            or re-scoping any objective whose target theorem is drawn from it.

            ```markdown
            {refs}
            ```""")

    blueprint_block = ""
    if (project_path / "blueprint" / "src").is_dir():
        blueprint_block = dedent(f"""

            ## Blueprint

            This project has a blueprint at {project_path / 'blueprint'}. Informal proof
            live in {project_path / 'blueprint' / 'src' / 'chapters'}/<slug>.tex,
            one file per Lean source file. The slug mapping is:
              Lean file  Algebra/WLocal.lean  →  chapter  Algebra_WLocal.tex

            When you set objectives, write or update the corresponding chapter .tex file
            with the informal proof sketch BEFORE assigning the prover. The prover reads
            its chapter file and uses it as the source of truth for mathematical content.""")

    multilane_block = ""
    if ignore_multilane:
        multilane_block = dedent(f"""

            IMPORTANT EXPERIMENTAL MULTI-LANE RULES:
            - Treat multi-lane execution as an external runtime detail, not as part of the planning problem.
            - Do NOT inspect or mention {state_dir}/multilane/, lane worktrees, provider/model names, or lane-specific outcomes in PROGRESS.md, task_pending.md, or task_done.md.
            - Plan only from the main project state, the current .lean files, and the standard Archon state files.
            - Keep the plan lane-agnostic unless the user explicitly asks otherwise.""")

    no_directive_block = dedent(f"""

        HARD RULE — refactors:
        - You MUST NOT write to {state_dir}/REFACTOR_DIRECTIVE.md. That file is a leftover from an older Archon flow and is only used by the interactive `archon refactor draft` command the mathematician runs by hand.
        - The autonomous loop's way to refactor is to invoke the `refactor` subagent via the Agent tool, passing the directive INLINE in the prompt (see prompts/plan.md § "Subagent delegation"). The directive is never staged in a file.
        - If the existing {state_dir}/REFACTOR_DIRECTIVE.md, STRATEGY.md, task_pending.md, or PROGRESS.md contain references to the old REFACTOR_DIRECTIVE.md flow (e.g. "write the directive then the refactor agent will pick it up"), treat those as historical noise: prune them when you rewrite those files, and do NOT reproduce that pattern this iteration.""")

    return dedent(f"""\
        You are the plan agent for project '{project_name}'. Current stage: {stage}.
        Archon iteration: {iter_num:03d}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/plan.md and {state_dir}/PROGRESS.md.
        All state files (PROGRESS.md, task_pending.md, task_done.md, USER_HINTS.md, task_results/) are in {state_dir}/.
        The .lean files are in {project_path}/.""") + refs_block + blueprint_block + multilane_block + no_directive_block + debug_feedback_block(debug_feedback, state_dir, "plan", iter_num)


def build_prover_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
    iter_num: int, debug_feedback: bool = False
) -> str:
    return dedent(f"""\
        You are the prover agent for project '{project_name}'. Current stage: {stage}.
        Archon iteration: {iter_num:03d}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/prover-{stage}.md and {state_dir}/PROGRESS.md.
        All state files are in {state_dir}/. The .lean files are in {project_path}/.""") + debug_feedback_block(debug_feedback, state_dir, "prover", iter_num)


def build_parallel_prover_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
    iter_num: int, debug_feedback: bool = False,
    assigned_rel_lean_path: str | None = None,
) -> str:
    """Build the prover prompt, optionally tailored to a specific assigned file.

    When `assigned_rel_lean_path` is provided, the prompt includes a
    pointer to the blueprint chapter for that file.
    """
    bp_hint = ""
    if assigned_rel_lean_path:
        hint = _blueprint_chapter_hint(project_path, assigned_rel_lean_path)
        if hint:
            bp_hint = "\n\n" + hint

    return dedent(f"""\
        You are a prover agent for project '{project_name}'. Current stage: {stage}.
        Archon iteration: {iter_num:03d}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/prover-{stage}.md and {state_dir}/PROGRESS.md.
        Check your .lean file for /- USER: ... -/ comments for file-specific hints.

        IMPORTANT:
        - You own ONLY the file assigned below. Do NOT edit any other .lean file.
        - Write your results to {state_dir}/task_results/<your_file>.md when done.
        - Do NOT edit PROGRESS.md, task_pending.md, or task_done.md.
        - Missing Mathlib infrastructure is NEVER a valid reason to leave a sorry.
        - NEVER revert to a bare sorry. Always leave your partial proof attempt in the code.""") + bp_hint + debug_feedback_block(debug_feedback, state_dir, "parallel prover", iter_num)


def build_refactor_prompt(
    project_name: str, project_path: Path, state_dir: Path, directive: str,
    iter_num: int, slug: str, debug_feedback: bool = False
) -> str:
    """Build the refactor agent's prompt.

    ``slug`` distinguishes multiple refactor calls per iteration and pins
    the report path. The CLI flow (``archon refactor run``) uses the
    fixed slug ``"cli"``; the autonomous loop generates a kebab-case
    slug per call.
    """
    return dedent(f"""\
        You are the refactor agent for project '{project_name}'.
        Archon iteration: {iter_num:03d}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Slug: {slug}
        Read {state_dir}/CLAUDE.md for project context, then read {state_dir}/prompts/refactor.md.

        DIRECTIVE FROM PLAN AGENT:
        {directive}

        Execute this directive. Keep all files compiling (insert sorry at broken proof sites).
        Document every change in {state_dir}/task_results/refactor-{slug}.md
        (include the slug as the `## Slug` field at the top of the report).""") + debug_feedback_block(debug_feedback, state_dir, f"refactor ({slug})", iter_num)


def build_review_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
    session_num: int, session_dir: Path, attempts_file: Path,
    combined_prover_log: Path, iter_num: int, debug_feedback: bool = False
) -> str:
    return dedent(f"""\
        You are the review agent for project '{project_name}'. Current stage: {stage}.
        Archon iteration: {iter_num:03d}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/review.md.
        Session number: {session_num} (matches the iteration number — session_{session_num}/ is the review of iter-{iter_num:03d}).
        Pre-processed attempt data: {attempts_file} (READ THIS FIRST).
        Prover log: {combined_prover_log}

        CRITICAL — Write your output files to EXACTLY these paths:
          {session_dir}/milestones.jsonl
          {session_dir}/summary.md
          {session_dir}/recommendations.md
          {state_dir}/PROJECT_STATUS.md""") + debug_feedback_block(debug_feedback, state_dir, "review", iter_num)

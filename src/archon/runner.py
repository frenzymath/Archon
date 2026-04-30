"""Prompt builders and the legacy ``run_claude`` entry point.

The actual ``claude`` invocation now lives in :mod:`archon.agent`. This
module keeps the per-phase prompt builders (`build_plan_prompt`,
`build_prover_prompt`, …) and a thin ``run_claude`` shim that delegates
to a ``ClaudeAgent`` so existing call sites keep working without a
big-bang rename. New code should construct a ``ClaudeAgent`` directly
to take advantage of role tagging and explicit model selection.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from archon.agent import ClaudeAgent, DEFAULT_MODEL
from archon.commands.tooling.blueprint import lean_file_to_chapter_slug


# ── context injection helpers ─────────────────────────────────────────


def _references_summary(state_dir: Path | None, project_path: Path | None = None, max_chars: int = 3000) -> str:
    """Read references/summary.md and return a bounded chunk for prompts.

    Empty string if the file doesn't exist or is just the template.
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
    if len(non_meta) <= 3:  # only heading + table header + separator row
        return ""

    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n... (truncated)"
    return content


def _blueprint_chapter_hint(
    project_path: Path,
    rel_lean_path: str,
) -> str:
    """Build the 'your blueprint chapter is at X; create it if missing' hint.

    Empty string if no blueprint exists.
    """
    if not (project_path / "blueprint" / "src").is_dir():
        return ""
    slug = lean_file_to_chapter_slug(rel_lean_path)
    rel_chapter = f"blueprint/src/chapters/{slug}.tex"
    return dedent(f"""\
        Blueprint chapter for your file: {rel_chapter}
        - Read it BEFORE writing any Lean code — it contains the informal proof sketch
          written by the plan agent.
        - After you formalize a declaration, mark its blueprint environment with \\leanok.
        - If the chapter file does not yet exist, create it with a minimal \\chapter block
          and note in your task_results that the plan agent should flesh it out.""")


# ── prompt building ───────────────────────────────────────────────────


def build_plan_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
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
            sketches live in {project_path / 'blueprint' / 'src' / 'chapters'}/<slug>.tex,
            one file per Lean source file. The slug mapping is:
              Lean file  Algebra/WLocal.lean  →  chapter  Algebra_WLocal.tex

            When you set objectives, write or update the corresponding chapter .tex file
            with the informal proof sketch BEFORE assigning the prover. The prover reads
            its chapter file and uses it as the source of truth for mathematical content.

            This REPLACES the older informal/*.md convention. Do not write new informal/*.md
            files — write to chapters/*.tex instead.""")

    return dedent(f"""\
        You are the plan agent for project '{project_name}'. Current stage: {stage}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/plan.md and {state_dir}/PROGRESS.md.
        All state files (PROGRESS.md, task_pending.md, task_done.md, USER_HINTS.md, task_results/) are in {state_dir}/.
        The .lean files are in {project_path}/.""") + refs_block + blueprint_block


def build_prover_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
) -> str:
    return dedent(f"""\
        You are the prover agent for project '{project_name}'. Current stage: {stage}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/prover-{stage}.md and {state_dir}/PROGRESS.md.
        All state files are in {state_dir}/. The .lean files are in {project_path}/.""")


def build_parallel_prover_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
    assigned_rel_lean_path: str | None = None,
) -> str:
    """Build the prover prompt, optionally tailored to a specific assigned file.

    When `assigned_rel_lean_path` is provided, the prompt includes a pointer
    to the blueprint chapter for that file. The calling code still appends
    the `Your assigned file: <rel>` line itself (for backwards compatibility
    with existing loop.py code), but now the blueprint hint is ALSO in the
    base prompt so the prover can't miss it.
    """
    bp_hint = ""
    if assigned_rel_lean_path:
        hint = _blueprint_chapter_hint(project_path, assigned_rel_lean_path)
        if hint:
            bp_hint = "\n\n" + hint

    return dedent(f"""\
        You are a prover agent for project '{project_name}'. Current stage: {stage}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/prover-{stage}.md and {state_dir}/PROGRESS.md.
        Check your .lean file for /- USER: ... -/ comments for file-specific hints.

        IMPORTANT:
        - You own ONLY the file assigned below. Do NOT edit any other .lean file.
        - Write your results to {state_dir}/task_results/<your_file>.md when done.
        - Do NOT edit PROGRESS.md, task_pending.md, or task_done.md.
        - Missing Mathlib infrastructure is NEVER a valid reason to leave a sorry.
        - NEVER revert to a bare sorry. Always leave your partial proof attempt in the code.""") + bp_hint


def build_refactor_prompt(
    project_name: str, project_path: Path, state_dir: Path,
    directive: str,
) -> str:
    return dedent(f"""\
        You are the refactor agent for project '{project_name}'.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for project context, then read {state_dir}/prompts/refactor.md.

        DIRECTIVE FROM PLAN AGENT:
        {directive}

        Execute this directive. Keep all files compiling (insert sorry at broken proof sites).
        Document every change in {state_dir}/task_results/refactor.md.""")


def build_review_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
    session_num: int, session_dir: Path, attempts_file: Path,
    combined_prover_log: Path,
) -> str:
    return dedent(f"""\
        You are the review agent for project '{project_name}'. Current stage: {stage}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/review.md.
        Session number: {session_num}.
        Pre-processed attempt data: {attempts_file} (READ THIS FIRST).
        Prover log: {combined_prover_log}

        CRITICAL — Write your output files to EXACTLY these paths:
          {session_dir}/milestones.jsonl
          {session_dir}/summary.md
          {session_dir}/recommendations.md
          {state_dir}/PROJECT_STATUS.md""")


# ── run_claude (delegating shim) ──────────────────────────────────────


def run_claude(
    prompt: str,
    *,
    cwd: Path,
    log_base: Path | None = None,
    verbose_logs: bool = False,
    extra_args: list[str] | None = None,
    model: str = DEFAULT_MODEL,
    role: str | None = None,
) -> bool:
    """Run `claude -p` with optional JSONL logging.

    Thin delegating wrapper around :class:`archon.agent.ClaudeAgent` kept
    for callers that don't yet construct an agent themselves. New code
    should instantiate ``ClaudeAgent(model=..., role=...)`` directly so
    role tagging is explicit at the construction site.

    Args:
        prompt: The prompt string to pass to Claude.
        cwd: Working directory (project path).
        log_base: If provided, enables JSONL logging to {log_base}.jsonl
                  (and optionally {log_base}.raw.jsonl).
        verbose_logs: If True, also write raw stream events.
        extra_args: Additional arguments to pass to claude.
        model: Model alias (`opus`, `sonnet`) or full id forwarded to
            ``claude --model``. Default ``opus``.
        role: Phase tag (`plan` / `prover` / …) stamped into the JSONL.

    Returns:
        True if claude exited successfully, False otherwise.
    """
    return ClaudeAgent(model=model, role=role).run(
        prompt,
        cwd=cwd,
        log_base=log_base,
        verbose_logs=verbose_logs,
        extra_args=extra_args,
    )
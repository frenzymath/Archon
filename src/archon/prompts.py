"""Prompt builders for Archon's plan / prover / review phases.

The actual ``claude`` invocation lives in :mod:`archon.agent`. This
module only constructs the prompt strings handed to the agent. The
session-end inspection helpers (which read the JSONL log after a run)
live in :mod:`archon.session_log`.

Design principle: anything the loop can do deterministically (read a
file, run a check, compute a summary) is injected INTO this module's
output rather than asked of the agent. The agent reads what's in
front of it; the agent does NOT mechanically "go read file X then
clear it". File-reading and file-clearing are the loop's job.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from textwrap import dedent

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)


def _strip_html_comments(text: str) -> str:
    """Strip HTML comments from ``text``.

    ``USER_HINTS.md`` ships an HTML-comment preamble explaining the
    format to the mathematician. The preamble is for the human; the
    plan agent must not see it (otherwise "template only" content
    looks like live hints to the planner). We strip the comment block
    before checking emptiness and before injection.
    """
    return _HTML_COMMENT_RE.sub("", text)

from archon.commands.tooling.blueprint import lean_file_to_chapter_slug
from archon.state.iter_state import (
    format_recent_iter_sidecars_for_prompt,
    objectives_sidecar_path,
    plan_sidecar_path,
    read_recent_iter_sidecars,
    review_sidecar_path,
)


# ── context injection helpers ─────────────────────────────────────────


def _blueprint_doctor_findings_block(
    state_dir: Path,
    iter_num: int,
    *,
    max_orphans: int = 15,
    max_broken_refs: int = 25,
) -> str:
    """Inject the prior iter's blueprint-doctor findings into the plan prompt.

    The doctor runs between prover and review of every iter and writes
    a JSON sidecar at ``logs/iter-NNN/blueprint-doctor.json``. This
    function reads the *prior* iter's sidecar (the most recently
    completed one) and renders its findings as a prompt section the
    plan agent can act on directly — no "go read this file"
    instruction needed.

    The block is empty (no section header) when:

    * iter is 1 (no prior iter ran yet);
    * the prior iter's sidecar is missing or unreadable (e.g. the
      doctor wasn't enabled, or the loop ran without blueprint/);
    * the sidecar is parseable but the doctor reported no findings.

    Caps the rendered findings at ``max_orphans`` / ``max_broken_refs``
    so a worst-case bloated report can't dominate the prompt; the
    overflow note tells the planner to read the full JSON.
    """
    if iter_num <= 1:
        return ""
    prev = iter_num - 1
    json_path = state_dir / "logs" / f"iter-{prev:03d}" / "blueprint-doctor.json"
    if not json_path.is_file():
        return ""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    orphans = data.get("orphan_chapters", []) or []
    broken = data.get("broken_refs", []) or []
    malformed = data.get("malformed_refs", []) or []
    axioms = data.get("axiom_decls", []) or []
    covers_problems = data.get("covers_problems", []) or []
    if not orphans and not broken and not malformed and not axioms and not covers_problems:
        return ""

    lines: list[str] = [
        "",
        "## Blueprint doctor — live structural findings",
        "",
        f"The deterministic blueprint-doctor ran at the end of iter-{prev:03d} "
        f"and flagged the issues below. These are the items the LLM-based "
        f"blueprint-reviewer has been observed to miss; they remain live "
        f"until something in this iter resolves them. Address them THIS "
        f"iter (or explain in the iter sidecar why you are deferring).",
        "",
    ]

    if axioms:
        lines.append("### Axiom declarations (no new axioms — Archon stance)")
        lines.append("")
        lines.append(
            "Every entry below is an `axiom <name> : ...` found under the "
            "project's `.lean` files. Remove each (supply a real proof), "
            "or, when the axiom is the mathematician's explicit boundary "
            "marker, mark it protected in `archon-protected.yaml` and "
            "document the rationale in `STRATEGY.md`."
        )
        lines.append("")
        for entry in axioms[:max_orphans]:
            f = entry.get("file", "")
            n = entry.get("name", "")
            lines.append(f"- `{f}` :: `{n}`")
        if len(axioms) > max_orphans:
            lines.append(
                f"- ... and {len(axioms) - max_orphans} more "
                f"(see `{json_path}` for the full list)."
            )
        lines.append("")

    if covers_problems:
        lines.append("### Chapter coverage problems (`% archon:covers`)")
        lines.append("")
        lines.append(
            "A chapter's `% archon:covers <file> ...` declaration tells the "
            "prover-dispatch gate which Lean files that chapter blueprints. "
            "The issues below would route the gate to the wrong chapter; fix "
            "the declaration (correct the path, or make exactly one chapter "
            "own each file)."
        )
        lines.append("")
        for entry in covers_problems[:max_orphans]:
            lines.append(f"- {entry.get('detail', '')}")
        if len(covers_problems) > max_orphans:
            lines.append(
                f"- ... and {len(covers_problems) - max_orphans} more "
                f"(see `{json_path}` for the full list)."
            )
        lines.append("")

    if orphans:
        lines.append("### Orphan chapters")
        lines.append("")
        lines.append(
            "Files under `blueprint/src/chapters/` not reachable from "
            "`content.tex` via `\\input{...}` (direct or transitive). "
            "Either add an `\\input{...}` line to `content.tex` (if the "
            "chapter is meant to be live) or delete the orphan (if it "
            "is stale)."
        )
        lines.append("")
        for p in orphans[:max_orphans]:
            lines.append(f"- `{p}`")
        if len(orphans) > max_orphans:
            lines.append(
                f"- ... and {len(orphans) - max_orphans} more "
                f"(see `{json_path}` for the full list)."
            )
        lines.append("")

    if malformed:
        lines.append("### Malformed annotations (block blueprint build)")
        lines.append("")
        lines.append(
            "Annotations with an empty argument (`\\uses{}`, `\\proves{}`, "
            "`\\label{}`, `\\ref{}`, ...) or an empty list item "
            "(`\\uses{a,,b}`). plastex emits `Label '' could not be "
            "resolved` for each, then the leanblueprint depgraph builder "
            "enters infinite recursion. **`leanblueprint web` will keep "
            "crashing until every entry below is resolved.** Fix each by "
            "filling in the intended label or removing the empty annotation."
        )
        lines.append("")
        # Group by (chapter, kind, reason) for readability.
        m_by_chapter: dict[str, list[tuple[str, str]]] = {}
        for entry in malformed:
            chapter = entry.get("chapter", "")
            kind = entry.get("kind", "")
            reason = entry.get("reason", "")
            m_by_chapter.setdefault(chapter, []).append((kind, reason))
        m_rendered = 0
        for chapter in sorted(m_by_chapter):
            if m_rendered >= max_broken_refs:
                break
            lines.append(f"- `{chapter}`:")
            for kind, reason in sorted(set(m_by_chapter[chapter])):
                if m_rendered >= max_broken_refs:
                    break
                lines.append(f"  - `\\{kind}{{...}}` — {reason}")
                m_rendered += 1
        m_total = len(malformed)
        if m_rendered < m_total:
            lines.append(
                f"- ... and {m_total - m_rendered} more "
                f"(see `{json_path}` for the full list)."
            )
        lines.append("")

    if broken:
        lines.append("### Broken cross-references")
        lines.append("")
        lines.append(
            "`\\ref{...}` / `\\cref{...}` / `\\uses{...}` / `\\proves{...}` "
            "targets with no matching `\\label{...}` anywhere in the "
            "included tex tree. Each is either a label typo, a label "
            "stranded in an orphan chapter, or a stale `\\uses{...}` list "
            "from a rename."
        )
        lines.append("")
        # Group by chapter for readability.
        by_chapter: dict[str, list[tuple[str, str]]] = {}
        for entry in broken:
            chapter = entry.get("chapter", "")
            kind = entry.get("kind", "")
            label = entry.get("label", "")
            by_chapter.setdefault(chapter, []).append((kind, label))
        rendered = 0
        for chapter in sorted(by_chapter):
            if rendered >= max_broken_refs:
                break
            lines.append(f"- `{chapter}`:")
            for kind, label in sorted(set(by_chapter[chapter])):
                if rendered >= max_broken_refs:
                    break
                lines.append(f"  - `\\{kind}{{{label}}}`")
                rendered += 1
        total = len(broken)
        if rendered < total:
            lines.append(
                f"- ... and {total - rendered} more "
                f"(see `{json_path}` for the full list)."
            )
        lines.append("")

    return "\n".join(lines)


def _axiom_sweep_findings_block(
    state_dir: Path,
    iter_num: int,
    *,
    max_decls: int = 25,
) -> str:
    """Inject the prior iter's axiom-sweep ``sorryAx`` launderings.

    The optional axiom sweep (``loop.axiom_sweep``) runs between prover
    and review and writes ``logs/iter-NNN/axiom-sweep.json``. This reads
    the *prior* iter's sidecar and renders any ``sorryAx``-laundering
    declarations — ones that compile with NO sorry warning yet depend on
    ``sorryAx`` — as a prompt section the plan agent must act on. These
    are invisible to the warning-based sorry count, so without this the
    planner can believe a decl is closed when it is not.

    Empty (no header) when iter is 1, the sidecar is missing/unreadable
    (sweep off, or no Lean project), or no launderings were found.
    """
    if iter_num <= 1:
        return ""
    prev = iter_num - 1
    json_path = state_dir / "logs" / f"iter-{prev:03d}" / "axiom-sweep.json"
    if not json_path.is_file():
        return ""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    launderings = data.get("sorryLaunderings", []) or []
    if not launderings:
        return ""

    lines: list[str] = [
        "",
        "## Axiom sweep — sorryAx laundering (treat as OPEN sorries)",
        "",
        f"The deterministic axiom sweep ran at the end of iter-{prev:03d} and "
        f"found declarations that compile with NO `sorry` warning yet depend "
        f"on `sorryAx` — a `sorry` reached through a clean-compiling delegate. "
        f"The warning-based sorry count does NOT see these, so the headline "
        f"metric may understate the real open surface. Treat each as an open "
        f"sorry: trace the delegate chain to the underlying `sorry` and close "
        f"it (or, if the underlying statement is false, fix the statement). "
        f"Do NOT rely on the sorry count to tell you these are done.",
        "",
    ]
    for entry in launderings[:max_decls]:
        decl = entry.get("decl", "")
        axiom = entry.get("axiom", "sorryAx")
        lines.append(f"- `{decl}` — depends on `{axiom}`")
    if len(launderings) > max_decls:
        lines.append(
            f"- ... and {len(launderings) - max_decls} more "
            f"(see `{json_path}` for the full list)."
        )
    lines.append("")
    return "\n".join(lines)


def _user_hints_block(captured_hints: str | None) -> str:
    """Inject already-captured USER_HINTS.md content into the plan prompt.

    The loop reads ``USER_HINTS.md`` before the plan phase, passes the
    text here, and clears the file after the plan agent succeeds. The
    agent does NOT read or clear the hints file itself; everything the
    user wrote is already in this block.

    ``captured_hints`` of ``None`` or empty string renders the
    "no hints this iter" affordance — the planner reads the prior iter's
    sidecar for any ``## Fallback if no user response`` section (the
    user-silent fallback contract).

    HTML comments in ``captured_hints`` are stripped before both the
    emptiness check and the injection. The bundled ``USER_HINTS.md``
    template is an HTML-comment preamble explaining the format to the
    user; "template only" content must render as "no hints" to the
    planner, not as live instructions.
    """
    stripped_text = (
        _strip_html_comments(captured_hints) if captured_hints else ""
    )
    if not stripped_text.strip():
        return dedent("""

            ## User hints

            No user hints this iteration. If the prior iter's sidecar
            (`iter/iter-{prev}/plan.md`) declares a `## Fallback if no
            user response` section, execute that fallback now and record
            the auto-execution in this iter's sidecar under
            `## User-silent fallback executed`. Otherwise proceed
            normally.
        """)
    return dedent(f"""

        ## User hints

        The user wrote the following in `USER_HINTS.md` for this
        iteration. The loop has already captured the content (shown
        below) and will clear the file once your plan phase succeeds —
        you do NOT need to read `USER_HINTS.md` or clear it yourself.
        Treat anything below as the live hint set; incorporate it into
        your plan.

        ```
        {stripped_text.strip()}
        ```
    """)


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


def _iter_sidecar_context_block(
    state_dir: Path,
    iter_num: int,
    *,
    role: str,
    window: int = 3,
) -> str:
    """Inject per-iter sidecar context into a plan or review prompt.

    Emits a section that:

    * tells the agent where to write its per-iter sidecar
      (``iter/iter-NNN/{plan,review,objectives}.md``);
    * lists the rule "keep STRATEGY.md / PROJECT_STATUS.md / task_*
      stable: do not append iteration-by-iteration narrative there";
    * injects the last ``window`` iters' sidecars verbatim so the
      agent has continuity without re-reading the top-level files
      (which no longer carry per-iter history).
    """
    sidecars = read_recent_iter_sidecars(
        state_dir, current_iter=iter_num, window=window,
    )
    include_plan = role in ("plan", "review")
    include_review = role == "review" or (role == "plan" and len(sidecars) > 0)
    snapshot_block = format_recent_iter_sidecars_for_prompt(
        sidecars,
        include_plan=include_plan,
        include_review=include_review,
        include_objectives=False,
    )

    iter_dir = state_dir / "iter" / f"iter-{iter_num:03d}"
    if role == "plan":
        sidecar_path = plan_sidecar_path(state_dir, iter_num)
        sidecar_name = "plan.md"
        sister_path = objectives_sidecar_path(state_dir, iter_num)
        top_level_warn = dedent("""\
            - Do NOT append iteration-by-iteration narrative to STRATEGY.md.
              STRATEGY.md holds the stable end-state + decomposition only.
              Edit it ONLY when the strategy itself changes (route swap,
              decomposition revised, phase added/removed).
            - Do NOT append per-task attempt history to task_pending.md.
              That file holds the current open-task set with last-known
              state only. Per-attempt detail goes to your iter sidecar.
            """)
    else:  # role == "review"
        sidecar_path = review_sidecar_path(state_dir, iter_num)
        sidecar_name = "review.md"
        sister_path = None
        top_level_warn = dedent("""\
            - Do NOT append session narrative to PROJECT_STATUS.md's
              "Overall Progress" section — that file's narrative log is
              frozen. Your session goes to review.md below.
            - DO keep updating PROJECT_STATUS.md's "Knowledge Base"
              section. Cumulative non-obvious facts (errors not to
              reproduce, reusable proof patterns, Mathlib idioms that
              worked) still belong in the Knowledge Base; only the
              session-by-session log moved out.
            """)

    body = dedent(f"""\

        ## Per-iteration sidecars

        Per-iter narrative lives in ``{iter_dir}/`` (already created
        for you), NOT appended to the top-level state files.

        For THIS iteration write your narrative to:
          {sidecar_path}
    """)
    if sister_path is not None:
        body += f"  {sister_path}\n"
    body += dedent(f"""
        Rules:
        {top_level_warn}
        - The {sidecar_name} sidecar is born-bounded: it contains ONLY
          this iter's content. The full historical record across iters
          is the directory ``{iter_dir.parent}/iter-NNN/{sidecar_name}``,
          one file per iter.
        - You may also read older iters' sidecars on demand from
          ``{iter_dir.parent}/iter-NNN/`` if you need more context than
          the recent window below provides.
    """)
    if snapshot_block:
        body += "\n" + snapshot_block + "\n"
    else:
        body += dedent(f"""
            (No prior iters with sidecar content yet — this is an early
            iteration on this project. Your {sidecar_name} this iter
            will be the first entry future iterations read.)
        """)
    return body


def _subagent_catalog_block(project_path: Path, *, role: str) -> str:
    """Render the available-subagents catalog for a phase agent.

    Auto-injected into ``build_plan_prompt`` / ``build_review_prompt``
    so the phase agent sees the full enabled roster without having to
    ``ls .archon/subagents/`` itself. Descriptor frontmatter drives
    the content — adding/removing a subagent is one file, no template
    edits.

    For each enabled descriptor we surface: ``name``, the
    ``description``, the ``write_domain`` hint, whether it's
    ``read_only``, whether it ``can_spawn`` children, and whether it
    is ``mandatory`` for the calling phase. Mandatory subagents get
    an explicit "you MUST dispatch" instruction at the bottom so the
    agent can't miss it.
    """
    from archon.commands.tooling.project_config import (
        load_project_config,
        resolve_subagents_enabled,
    )
    from archon.subagents.registry import (
        _builtin_dir,
        build_registry,
        load_descriptors_from_dir,
    )

    cfg = load_project_config(project_path)
    enabled = resolve_subagents_enabled(cfg)
    registry = build_registry(project_path, enabled=enabled)

    if len(registry) == 0:
        # Discover what *could* be enabled so the message names them.
        discoverable: dict[str, str] = {}
        for d in (_builtin_dir(), project_path / ".archon" / "subagents"):
            for n, desc in load_descriptors_from_dir(d).items():
                discoverable[n] = (desc.description or "").splitlines()[0] if desc.description else ""
        if not discoverable:
            return dedent("""

                ## Available subagents

                None are installed for this project. Drop descriptors at
                ``.archon/subagents/<name>.md`` (YAML frontmatter + prompt
                body) to make subagents available.
            """)
        lines = [
            "",
            "## Available subagents",
            "",
            "None are currently **enabled** for this project — subagents "
            "ship disabled by default. The following are shipped and ready "
            "to turn on by listing their name in `subagents.enabled` in "
            "`.archon/config.json`:",
            "",
        ]
        for n in sorted(discoverable):
            short = discoverable[n]
            if len(short) > 160:
                short = short[:157] + "..."
            suffix = f" — {short}" if short else ""
            lines.append(f"- `{n}`{suffix}")
        lines.append("")
        lines.append("Example `.archon/config.json` snippet to enable a few:")
        lines.append("")
        lines.append("```json")
        lines.append('"subagents": {')
        lines.append('  "enabled": ["blueprint-reviewer", "strategy-critic", "progress-critic"]')
        lines.append("}")
        lines.append("```")
        lines.append("")
        lines.append(
            "Proceed without subagents for now — this phase will complete "
            "normally; the user has chosen the classic single-agent loop."
        )
        return "\n".join(lines) + "\n"

    descriptors = registry.descriptors()
    mandatory_for_role = [d for d in descriptors if d.is_mandatory_for(role)]

    lines = ["", "## Available subagents", ""]
    for d in descriptors:
        tags: list[str] = []
        if d.is_mandatory_for(role):
            tags.append("HIGHLY RECOMMENDED")
        if d.read_only:
            tags.append("read-only")
        if d.can_spawn:
            tags.append("can spawn children")
        tag_str = f" [{' · '.join(tags)}]" if tags else ""

        domain = d.write_domain or "(see directive — caller declares)"
        # Single-line, truncated description; full body lives in
        # ``.archon/subagents/<name>.md`` and the agent reads it
        # when it actually decides to invoke that subagent.
        desc = (d.description or "").strip().splitlines()
        short = desc[0] if desc else ""
        if len(short) > 200:
            short = short[:197] + "..."
        lines.append(f"- **{d.name}**{tag_str} — write: `{domain}` — {short}")

    lines.append("")
    lines.append(
        "Invoke any subagent via the generic wrapper (Bash, foreground):"
    )
    lines.append("")
    lines.append("```")
    lines.append("python3 .claude/tools/archon-subagent.py \\")
    lines.append("  --name <name> \\")
    lines.append("  --slug <kebab-slug> \\")
    lines.append("  --directive-file <path-to-directive.md> \\")
    lines.append("  --write-domain '<glob>'        # repeat for multiple")
    lines.append("```")
    lines.append("")
    lines.append(
        "When you decide to invoke a subagent, read its full prompt "
        "and directive shape from `.archon/subagents/<name>.md` "
        "before composing the directive."
    )

    if mandatory_for_role:
        names = ", ".join(f"`{d.name}`" for d in mandatory_for_role)
        sidecar_name = f"{role}.md"
        lines.append("")
        lines.append(
            f"**Each [HIGHLY RECOMMENDED] subagent should be dispatched "
            f"this phase unless you have a concrete reason to skip.** For "
            f"this phase that means: {names}. When you choose to skip one "
            f"(e.g. STRATEGY.md unchanged from prior iter and last verdict "
            f"was SOUND, or no new prover output to assess), record the "
            f"rationale as a one-line bullet under a `## Subagent skips` "
            f"section in `iter/iter-NNN/{sidecar_name}`:\n\n"
            f"```markdown\n"
            f"## Subagent skips\n\n"
            f"- <subagent-name>: <one-line reason, naming the condition that justifies the skip>\n"
            f"```\n\n"
            f"A post-phase audit reads that section and silences its "
            f"warning for subagents you skipped with rationale; it warns "
            f"only for subagents that neither dispatched nor were named "
            f"under `## Subagent skips`. Filling templates with hollow "
            f"dispatches when nothing has changed is the failure mode this "
            f"affordance exists to avoid — be willing to skip when the "
            f"input hasn't changed."
        )

    # Workflow guidance section: aggregate dispatcher_notes from
    # every enabled descriptor that has any. Lets each subagent ship
    # its own "how to dispatch me / how to use my output" rules
    # without needing prompt edits.
    with_notes = [d for d in descriptors if d.dispatcher_notes.strip()]
    if with_notes:
        lines.append("")
        lines.append("## Workflow guidance from active subagents")
        lines.append("")
        lines.append(
            "Each enabled subagent below carries usage instructions for "
            "you (the dispatching agent). Read these as workflow rules "
            "that apply this iteration — they encode how to USE the "
            "subagent and when in your phase to dispatch it."
        )
        for d in with_notes:
            lines.append("")
            lines.append(f"### {d.name}")
            lines.append("")
            # Preserve internal formatting (multi-line frontmatter
            # strings often start with hyphenated bullets already).
            lines.append(d.dispatcher_notes.rstrip())

    return "\n".join(lines) + "\n"


# ── stage normalization ───────────────────────────────────────────────


# Canonical prover stage tokens shipped at `.archon/prompts/prover-<stage>.md`.
# Used by ``_normalize_stage_for_prompt_path`` to recover the canonical
# token from a verbose ``## Current Stage`` line; the planner sometimes
# writes things like ``prover (Iter-123: M1.b residual — Steps 1-4 ...)``
# and the raw text breaks the prompt-file path resolution.
_PROVER_STAGES = ("autoformalize", "prover", "polish")


def _normalize_stage_for_prompt_path(stage: str) -> str:
    """Pick the canonical prover-stage token used in `prover-<stage>.md` paths.

    The plan agent occasionally writes ``## Current Stage`` with descriptive
    text appended after the stage token, e.g.::

        ## Current Stage
        prover (Iter-123: M1.b residual — Steps 1-4 of the IsLocalization.of_le)

    The raw text contains parentheses, em-dashes and trailing fragments — when
    embedded into ``.archon/prompts/prover-<stage>.md`` this produces a
    non-existent filename and the prover wastes one boot pivoting back to the
    canonical path. Match the first known prefix instead so the path is always
    one of the three shipped prompts.
    """
    head = stage.strip().lower().lstrip("`*").lstrip()
    for canonical in _PROVER_STAGES:
        if head.startswith(canonical):
            return canonical
    return "prover"


# ── prompt builders ───────────────────────────────────────────────────


def build_plan_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
    iter_num: int,
    *,
    ignore_multilane: bool = False,
    debug_feedback: bool = False,
    recent_iter_window: int = 3,
    captured_user_hints: str | None = None,
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

    sidecar_block = _iter_sidecar_context_block(
        state_dir, iter_num,
        role="plan", window=recent_iter_window,
    )
    catalog_block = _subagent_catalog_block(project_path, role="plan")
    user_hints_block = _user_hints_block(captured_user_hints)
    doctor_block = _blueprint_doctor_findings_block(state_dir, iter_num)
    axiom_sweep_block = _axiom_sweep_findings_block(state_dir, iter_num)

    return dedent(f"""\
        You are the plan agent for project '{project_name}'. Current stage: {stage}.
        Archon iteration: {iter_num:03d}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/plan.md and {state_dir}/PROGRESS.md.
        State files (PROGRESS.md, task_pending.md, task_done.md, task_results/) live in {state_dir}/.
        The .lean files are in {project_path}/.

        Notes on what the loop has already done for you THIS iteration (so you don't repeat it):
        - User hints from USER_HINTS.md have been captured and are injected below under `## User hints`. The loop will clear the file when your plan phase succeeds; you do NOT need to read or clear it yourself.
        - The prior iter's blueprint-doctor findings are injected below under `## Blueprint doctor — live structural findings` (when there were any). You do NOT need to read `logs/iter-{{prev}}/blueprint-doctor.md`; act on what's inline.""") + user_hints_block + doctor_block + axiom_sweep_block + refs_block + blueprint_block + multilane_block + no_directive_block + sidecar_block + catalog_block + debug_feedback_block(debug_feedback, state_dir, "plan", iter_num)


def build_prover_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
    iter_num: int, debug_feedback: bool = False
) -> str:
    stage_path = _normalize_stage_for_prompt_path(stage)
    return dedent(f"""\
        You are the prover agent for project '{project_name}'. Current stage: {stage}.
        Archon iteration: {iter_num:03d}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/prover-{stage_path}.md and {state_dir}/PROGRESS.md.
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

    stage_path = _normalize_stage_for_prompt_path(stage)
    return dedent(f"""\
        You are a prover agent for project '{project_name}'. Current stage: {stage}.
        Archon iteration: {iter_num:03d}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/prover-{stage_path}.md and {state_dir}/PROGRESS.md.
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


def _blueprint_doctor_block(state_dir: Path, iter_num: int) -> str:
    """Surface the blueprint-doctor's report in the review prompt.

    The deterministic doctor (orphan chapters + broken cross-refs) runs
    between prover and review and writes its findings to
    ``logs/iter-NNN/blueprint-doctor.md``. The review agent should read
    it (or note its absence) so structural issues are not lost.
    """
    doctor_md = state_dir / "logs" / f"iter-{iter_num:03d}" / "blueprint-doctor.md"
    return dedent(f"""

        ## Blueprint doctor report

        The deterministic ``blueprint-doctor`` runs between the prover and
        review phases each iteration. Its Markdown report is at:

          {doctor_md}

        Read it before writing your session summary. It flags two classes of
        structural bug that the blueprint-reviewer subagent has been
        observed to miss: orphan chapters (``.tex`` files not ``\\input``'d
        by ``content.tex``) and broken cross-references (``\\ref{{...}}`` /
        ``\\uses{{...}}`` targets that no ``\\label{{...}}`` defines).

        If the doctor reports findings, surface them in your session
        ``summary.md`` and ``recommendations.md`` so the next plan agent
        knows to address them. If the report is missing or empty, that
        means the doctor was either skipped or found nothing to flag.""")


def _sync_leanok_block(state_dir: Path, iter_num: int) -> str:
    """Surface the deterministic ``\\leanok`` sync's run record.

    Before flagging a ``\\leanok`` marker as suspicious (e.g. proof-block
    ``\\leanok`` on a sorry-bodied decl), check the state file:

      ``{state_dir}/sync_leanok-state.json``

    written by the ``sync_leanok`` phase between the prover and review.
    Its ``iter`` field tells you which iteration's tree the sync last
    ran against; ``sha`` pins the inner-git HEAD at that moment;
    ``chapters_touched`` lists chapters whose markers were modified.

    If ``iter`` equals the current review iteration, any ``\\leanok``
    the sync left in place reflects the script's deterministic verdict
    (file compiles, no attributable sorry under that decl) — flag it as
    "genuine laundering" only after auditing the Lean source yourself.
    If the file is missing or ``iter`` lags behind, the markers may
    simply be stale; note that ambiguity in your summary rather than
    raising a CRITICAL.
    """
    state_file = state_dir / "sync_leanok-state.json"
    return dedent(f"""

        ## ``\\leanok`` sync attribution

        Before flagging any proof-block ``\\leanok`` on a sorry-bodied
        decl as headline laundering, consult:

          {state_file}

        Schema: ``{{iter, sha, timestamp, added, removed, chapters_touched}}``.

        - ``iter`` equals this iteration ({iter_num:03d}) ⇒ sync has run for
          the current tree. Any remaining ``\\leanok`` is the script's
          deterministic verdict; only flag genuine laundering after a
          first-hand audit of the Lean source.
        - ``iter`` is older or the file is missing ⇒ markers may be stale.
          Note the ambiguity in ``summary.md`` instead of raising CRITICAL.""")


def build_review_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
    session_num: int, session_dir: Path, attempts_file: Path,
    combined_prover_log: Path, iter_num: int, debug_feedback: bool = False,
    *,
    recent_iter_window: int = 3,
) -> str:
    sidecar_block = _iter_sidecar_context_block(
        state_dir, iter_num,
        role="review", window=recent_iter_window,
    )
    catalog_block = _subagent_catalog_block(project_path, role="review")
    doctor_block = _blueprint_doctor_block(state_dir, iter_num)
    sync_block = _sync_leanok_block(state_dir, iter_num)

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
          {state_dir}/PROJECT_STATUS.md""") + sidecar_block + catalog_block + doctor_block + sync_block + debug_feedback_block(debug_feedback, state_dir, "review", iter_num)

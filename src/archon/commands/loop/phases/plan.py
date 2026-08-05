"""Plan phase: invoke the plan agent and refresh `current_stage`.

Loop-side automation done here so the prompt doesn't have to ask the
agent to do it:

* **User hints**: read ``USER_HINTS.md`` BEFORE the agent starts,
  inject the content into the prompt via ``captured_user_hints=``,
  and clear the file AFTER the plan phase succeeds. The agent does
  not read or clear that file itself.
* **Blueprint-doctor findings**: the build_plan_prompt builder reads
  the prior iter's doctor JSON and injects findings inline — no
  agent-side file read required.
"""

from __future__ import annotations

from collections import Counter
import re
import time
from pathlib import Path

from archon import log
from archon.commands.tooling.iteration import commit_phase
from archon.commands.tooling.project_config import (
    load_project_config,
    resolve_recent_iter_window,
    resolve_subagents_enabled,
)
from archon.prompts import build_plan_prompt
from archon.commands.loop.sorry_count import count_sorries
from archon.state import is_complete, read_stage, write_meta, write_stage
from archon.subagents.audit import check_mandatory_dispatched

from ..plan_validate import AUTO_NOTES_FILENAME
from ..resume import PLAN_CONTINUE, persist_session_id, pick_resume_session
from .base import Phase, PhaseResult


def _capture_auto_notes(state_dir: Path) -> str | None:
    """Read AUTO_NOTES.md (loop-managed validation feedback) if present.

    This is the system-generated counterpart to USER_HINTS.md: plan-validate
    writes dropped/blocked/deferred-objective notes here (never into the
    user-authored USER_HINTS.md). Captured before the plan agent runs and
    cleared after, exactly like user hints, but with no persistent section to
    preserve — it is fully loop-owned.
    """
    notes_file = state_dir / AUTO_NOTES_FILENAME
    if not notes_file.is_file():
        return None
    try:
        return notes_file.read_text(encoding="utf-8")
    except OSError:
        return None


def _clear_auto_notes(state_dir: Path) -> None:
    """Delete AUTO_NOTES.md after the plan phase consumed it.

    Unlike USER_HINTS.md (which keeps a template + persistent section), this
    file is purely loop-owned, so clearing means removing it — the next
    plan-validate run recreates it on demand.
    """
    notes_file = state_dir / AUTO_NOTES_FILENAME
    try:
        notes_file.unlink(missing_ok=True)
    except OSError as e:
        log.warn(f"could not clear {notes_file}: {e}")


def _capture_user_hints(state_dir: Path) -> str | None:
    """Read USER_HINTS.md if present and return its text.

    Returns the raw text (whitespace preserved) when the file exists
    and is readable; returns ``None`` when the file is missing or
    unreadable. The clear step is deferred to
    :func:`_clear_user_hints` so we only clear when the plan phase
    actually consumes the hints — a crashed plan keeps the file
    intact for the retry.
    """
    hints_file = state_dir / "USER_HINTS.md"
    if not hints_file.is_file():
        return None
    try:
        return hints_file.read_text(encoding="utf-8")
    except OSError:
        return None


_PERSISTENT_HEADING = re.compile(r"^##\s+Persistent hints\s*$", re.IGNORECASE | re.MULTILINE)
_TEMPORARY_HEADING = re.compile(r"^##\s+Temporary hints\s*$", re.IGNORECASE | re.MULTILINE)
_HTML_COMMENT_END_RE = re.compile(r"-->")


def _split_hints(text: str) -> tuple[str, str]:
    """Split USER_HINTS.md text into (temporary_body, persistent_block).

    ``persistent_block`` is everything from the ``## Persistent hints``
    heading to EOF (heading line included).  ``temporary_body`` is the
    rest.  Returns (text, "") when no persistent section is found.

    HTML comment blocks are skipped before searching so that a heading
    mentioned inside the comment preamble (e.g. the template's format
    guide) is not mistaken for the actual section boundary.
    """
    # Start searching after the last HTML comment end marker so headings
    # mentioned inside the comment preamble are ignored.
    search_from = 0
    for cm in _HTML_COMMENT_END_RE.finditer(text):
        search_from = cm.end()
    m = _PERSISTENT_HEADING.search(text, search_from)
    if not m:
        return text, ""
    return text[: m.start()], text[m.start():]


def _temporary_hint_entries(text: str) -> list[str]:
    """Return the actual temporary-hint entries, excluding the format guide.

    A hint is normally one timestamped Markdown bullet. Continuation lines
    are kept with their preceding bullet so an edit to a multi-line hint is
    treated as a new entry instead of being partly consumed. Files from before
    the Temporary/Persistent split are treated entirely as temporary content;
    their HTML format guide is still excluded.
    """
    uncommented = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    heading = _TEMPORARY_HEADING.search(uncommented)
    if heading:
        body_start = heading.end()
        if body_start < len(uncommented) and uncommented[body_start] == "\n":
            body_start += 1
        remainder = uncommented[body_start:]
        next_heading = re.search(r"^##\s", remainder, re.MULTILINE)
        body = remainder[:next_heading.start()] if next_heading else remainder
    elif _PERSISTENT_HEADING.search(uncommented):
        body = ""
    else:
        body = uncommented

    entries: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if re.match(r"^\s*-\s+\[", line):
            if current:
                entries.append("\n".join(current).strip())
            current = [line]
        elif current:
            current.append(line)
        elif line.strip():
            # Preserve malformed/manual content rather than silently losing it.
            entries.append(line.strip())
    if current:
        entries.append("\n".join(current).strip())
    return entries


def _uncaptured_temporary_entries(current: str, captured: str) -> list[str]:
    """Keep temporary entries that were not present in the plan snapshot."""
    consumed = Counter(_temporary_hint_entries(captured))
    retained: list[str] = []
    for entry in _temporary_hint_entries(current):
        if consumed[entry]:
            consumed[entry] -= 1
        else:
            retained.append(entry)
    return retained


def _clear_user_hints(state_dir: Path, captured_hints: str | None = None) -> None:
    """Clear temporary hints consumed by a successful plan phase.

    The plan prompt is built from ``captured_hints`` before the agent starts.
    If a user adds or rewrites a temporary hint while that agent is running,
    it was not in the prompt and must survive for the next plan phase. Thus,
    when a capture is supplied, only entries present in that snapshot are
    consumed. Persistent hints remain verbatim across iterations.

    Calling this helper without a capture retains the legacy/reset behavior:
    clear the complete temporary section.

    Pre-section files are migrated into the current template while applying
    the same snapshot comparison. If the bundled template is unavailable,
    clearing remains best-effort and does not raise into the loop.
    """
    hints_file = state_dir / "USER_HINTS.md"
    try:
        template = _read_user_hints_template()
    except Exception:
        template = ""

    # Read the current file to preserve any persistent hints.
    current = ""
    try:
        current = hints_file.read_text(encoding="utf-8")
    except OSError:
        pass

    _, persistent_block = _split_hints(current)
    retained_temporary = (
        _uncaptured_temporary_entries(current, captured_hints)
        if captured_hints is not None
        else []
    )

    # Rebuild from the current template even when the live file predates the
    # Temporary/Persistent split or has lost one of its section headings.
    template_temporary, template_persistent = _split_hints(template)
    if retained_temporary:
        # The template intentionally leaves a blank separator before the
        # persistent heading. Normalize that tail while inserting entries so
        # the Temporary heading has exactly one blank line before them.
        template_temporary = template_temporary.rstrip("\n") + "\n\n"
        template_temporary += "\n".join(retained_temporary) + "\n\n"
    new_content = template_temporary + (persistent_block or template_persistent)

    try:
        hints_file.write_text(new_content, encoding="utf-8")
    except OSError as e:
        log.warn(f"could not clear {hints_file}: {e}")


def _read_user_hints_template() -> str:
    """Return the bundled ``USER_HINTS.md`` template content.

    Resolved via the same ``data_path`` helper ``archon init`` uses, so
    the runtime can't drift from what a fresh ``archon init`` would
    produce. Empty string when the template is missing — defensive
    fallback so a missing-template scenario degrades to clean clear
    behavior rather than crashing the loop.
    """
    from archon.commands.init.utils import data_path
    template_path = data_path("archon-template/USER_HINTS.md")
    if not template_path.is_file():
        return ""
    try:
        return template_path.read_text(encoding="utf-8")
    except OSError:
        return ""


class PlanPhase(Phase):
    name = "Plan agent"
    number = 1
    skip_token = "plan"

    def run(self) -> PhaseResult:
        ctx = self.ctx
        if self.skip_token in ctx.skip_now:
            log.phase(self.number, f"{self.name} — skipped (--from)")
            ctx.current_stage = read_stage(ctx.progress_file, ctx.force_stage())
            return PhaseResult(skipped=True)

        log.phase(self.number, self.name)
        plan_start = time.monotonic()
        cfg = load_project_config(ctx.project_path)
        captured_hints = _capture_user_hints(ctx.state_dir)
        captured_auto_notes = _capture_auto_notes(ctx.state_dir)
        plan_prompt = build_plan_prompt(
            ctx.project_name, ctx.project_path, ctx.state_dir, ctx.current_stage,
            ctx.iter_num,
            ignore_multilane=(
                ctx.options.multilane_preview or ctx.options.multilane_execute
            ),
            debug_feedback=ctx.options.debug_feedback,
            recent_iter_window=resolve_recent_iter_window(cfg),
            captured_user_hints=captured_hints,
            captured_auto_notes=captured_auto_notes,
        )

        if ctx.dry_run:
            log.step("[dry-run] Plan prompt:")
            print(plan_prompt)
        else:
            plan_log = ctx.iter_dir / "plan"
            resume_sid = pick_resume_session(
                ctx.iter_meta, "plan.sessionId",
                enabled=(ctx.resume_phase == self.skip_token),
                label="plan",
                cwd=ctx.project_path,
                jsonl_fallback=Path(str(plan_log) + ".jsonl"),
            )
            ctx.make_agent("plan").run(
                PLAN_CONTINUE if resume_sid else plan_prompt,
                cwd=ctx.project_path,
                log_base=plan_log, verbose_logs=ctx.verbose_logs,
                env_overrides={"ARCHON_ITER_NUM": f"{ctx.iter_num:03d}"},
                resume_session_id=resume_sid,
            )
            persist_session_id(
                ctx.iter_meta, Path(str(plan_log) + ".jsonl"),
                "plan.sessionId",
            )

        plan_secs = int(time.monotonic() - plan_start)
        log.info(f"Plan phase finished ({plan_secs}s)")
        if not ctx.dry_run:
            # Clear USER_HINTS.md AFTER the plan agent has had a chance
            # to consume the captured content. If the plan crashed
            # before completing this far, the file is left intact so
            # the retry sees the same hints.
            if captured_hints is not None and captured_hints.strip():
                _clear_user_hints(ctx.state_dir, captured_hints)
            # AUTO_NOTES.md is fully loop-owned — clear it whenever it had
            # content so each validation note reaches the planner exactly once.
            if captured_auto_notes is not None and captured_auto_notes.strip():
                _clear_auto_notes(ctx.state_dir)
            check_mandatory_dispatched(
                ctx.project_path, ctx.state_dir, ctx.iter_num,
                phase="plan",
                enabled=resolve_subagents_enabled(cfg),
            )
            write_meta(
                ctx.iter_meta,
                **{"plan.status": "done", "plan.durationSecs": plan_secs},
            )
            commit_phase(
                ctx.project_path, iter_num=ctx.iter_num, phase="plan",
                summary=f"stage={ctx.current_stage} ({plan_secs}s)",
            )

        if is_complete(ctx.progress_file, ctx.force_stage()):
            remaining_sorries = None if ctx.dry_run else count_sorries(ctx.project_path)
            if remaining_sorries is not None and remaining_sorries > 0:
                log.warn(
                    f"Plan marked COMPLETE, but {remaining_sorries} sorries "
                    f"were found."
                )
                write_stage(ctx.progress_file, "prover")
                ctx.current_stage = read_stage(ctx.progress_file, ctx.force_stage())
                log.warn(
                    "Stage reset to 'prover' so the loop continues instead "
                    "of accepting an incomplete project."
                )
            else:
                log.success("PROGRESS.md says COMPLETE. Exiting loop.")
                return PhaseResult(completed=True)

        ctx.current_stage = read_stage(ctx.progress_file, ctx.force_stage())
        return PhaseResult()

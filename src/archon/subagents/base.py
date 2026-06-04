"""Descriptor-driven Archon subagents.

One generic ``Subagent`` class drives every subagent invocation. Role
differences come from a :class:`SubagentDescriptor` parsed from
``.archon/subagents/<name>.md`` (YAML frontmatter + prompt body).

Adding a new subagent = drop a single ``.md`` file in the project's
subagent directory (or the built-in one). No Python subclass needed.

Hierarchical dispatch (unchanged from the pre-refactor model): when
``ARCHON_DISPATCH_SLOTS_DIR`` is set (the autonomous loop sets it at
iter start), ``run()`` acquires a slot from the per-iteration
:class:`~archon.dispatch.SlotPool`, validates that the caller-declared
``write_domain`` is a subset of the parent's domain, and appends
start/end records to ``logs/iter-NNN/dispatch.jsonl``. When the env
var is absent (manual CLI flows), all of that is skipped.
"""

from __future__ import annotations

import fnmatch
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

from archon import log
from archon.agent import ClaudeAgent, ClaudeBackend, DEFAULT_MODEL
from archon.commands.tooling.project_config import (
    load_project_config,
    resolve_claude_backend,
    resolve_subagent_model,
)
from archon.dispatch import SlotPool
from archon.prompts import debug_feedback_block


PARENT_SLUG_ENV_VAR = "ARCHON_SUBAGENT_SLUG"
ROOT_PARENT_SLUG = "_root"


class WriteDomainViolation(RuntimeError):
    """Raised when a subagent's declared write-domain isn't ⊆ its parent's."""


@dataclass(frozen=True)
class SubagentDescriptor:
    """One subagent's manifest.

    Parsed from a ``.md`` file with YAML frontmatter — the body of
    that file is :attr:`prompt_body`, which the spawned Claude reads
    via ``<state_dir>/subagents/<name>.md`` (the wrapper makes sure
    that file is on disk when the subagent starts).

    Fields:

    * ``name`` — role tag, filename stem, ``subagents.<name>`` config key.
    * ``description`` — one-line summary surfaced to the dispatching
      agent through the auto-injected catalog block.
    * ``write_domain`` — informational hint about what files this
      subagent typically touches. Actual enforcement uses the
      *caller-declared* ``--write-domain`` at dispatch time.
    * ``read_only`` — when true, the runtime prepends a "you are
      read-only on every project source file" note to the prompt.
    * ``can_spawn`` — informational; whether this subagent should
      dispatch child subagents.
    * ``default_enabled`` — registry filter when ``config.subagents.
      enabled`` is null/missing.
    * ``mandatory`` — list of phase names (``"plan"``, ``"review"``)
      during which this subagent MUST be dispatched at least once.
      The catalog renderer tags them ``[MANDATORY]``; a post-phase
      check warns if the dispatch didn't actually happen.
    * ``dispatcher_notes`` — multi-line text aimed at the *dispatching*
      agent (planner / reviewer), not the spawned subagent. Surfaced
      in the catalog's "Workflow guidance" section. Use this to
      encode rules like "dispatch me before any Lean work" or
      "do NOT pass STRATEGY.md in my directive". Distinct from
      ``description`` (one-liner) and ``prompt_body`` (what the
      spawned Claude reads).
    * ``source_path`` / ``prompt_body`` — set by the registry loader.
    """
    name: str
    description: str = ""
    write_domain: str | None = None
    read_only: bool = False
    can_spawn: bool = False
    default_enabled: bool = True
    mandatory: tuple[str, ...] = ()
    dispatcher_notes: str = ""
    prompt_body: str = ""
    source_path: Path | None = None

    def is_mandatory_for(self, phase: str) -> bool:
        return phase in self.mandatory


@dataclass
class SubagentResult:
    ok: bool
    duration_s: int
    report_path: Path | None
    summary: str


@dataclass
class DispatchRecord:
    """One row in ``logs/iter-NNN/dispatch.jsonl``."""
    role: str
    slug: str
    parent_slug: str
    write_domain: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_dispatch_jsonl(path: Path, row: dict[str, object]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        log.warn(f"dispatch.jsonl write failed at {path}: {e}")


def _read_dispatch_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                rows.append(json.loads(s))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return rows


def _find_parent_record(
    rows: list[dict[str, object]], parent_slug: str,
) -> dict[str, object] | None:
    if parent_slug == ROOT_PARENT_SLUG:
        return None
    for row in reversed(rows):
        if row.get("event") != "dispatch_start":
            continue
        if row.get("slug") == parent_slug:
            return row
    return None


def _domain_covers(parent_globs: list[str], child_globs: list[str]) -> bool:
    """True iff every glob in ``child_globs`` is covered by ``parent_globs``."""
    if not parent_globs:
        return True
    for child in child_globs:
        if not any(_glob_covers(p, child) for p in parent_globs):
            return False
    return True


def _glob_covers(parent: str, child: str) -> bool:
    if parent == child:
        return True
    if parent in ("**", "**/*"):
        return True
    if parent.endswith("/**"):
        prefix = parent[:-3]
        if child == prefix or child.startswith(prefix + "/") or child.startswith(prefix):
            return True
    if parent.endswith("/*"):
        prefix = parent[:-2]
        if "/" not in child.removeprefix(prefix + "/") and child.startswith(prefix + "/"):
            return True
    if fnmatch.fnmatch(child, parent):
        return True
    return False


def _subagent_protected_block(project_path: Path) -> str:
    """The archon-protected.yaml block for subagent prompts.

    Reuses the same renderer the plan/prover/dag prompts get so every
    writer/walker/reviewer sees the mathematician's rules without having to
    open the file. Empty when nothing is protected.
    """
    try:
        from archon.prompts import _protected_block
        return _protected_block(project_path)
    except Exception:
        return ""


def assert_write_domain_not_protected(
    project_path: Path, write_domain: list[str],
) -> None:
    """Raise WriteDomainViolation when a write-domain covers a protected file.

    The deterministic half of archon-protected.yaml: whole-file protection
    (``files:`` globs and ``blueprint: - file:`` rules) is enforced HERE, at
    dispatch time — an agent cannot even declare a write-domain that covers a
    mathematician-owned file. Declaration/label-level protection stays
    advisory (prompt-enforced, reviewer-audited) because it needs semantic
    judgment about *what* changed inside a writable file.

    Resolution is concrete: each protected glob is expanded against the tree
    and every matched file is tested against the declared write-domain.
    """
    if not write_domain:
        return
    from archon.commands.tooling import protect

    ps = protect.load(project_path)
    globs = ps.protected_file_globs()
    if not globs:
        return
    for pat in globs:
        try:
            matched = [p for p in project_path.glob(pat) if p.is_file()]
        except (ValueError, OSError):
            continue
        for f in matched:
            rel = f.relative_to(project_path).as_posix()
            for wd in write_domain:
                if _glob_covers(wd, rel):
                    raise WriteDomainViolation(
                        f"write-domain {wd!r} covers {rel!r}, which is "
                        f"protected by {protect.PROTECTED_FILENAME} "
                        f"(pattern {pat!r}). Mathematician-owned files are "
                        f"read-only for agents — narrow the write-domain."
                    )


class Subagent:
    """One descriptor-driven subagent invocation.

    Construct with a :class:`SubagentDescriptor`; the CLI / wrapper
    look this up via :mod:`archon.subagents.registry`. All differences
    between subagents (name, prompt body, read-only-ness, default
    write-domain hint) come from the descriptor.
    """

    def __init__(
        self,
        descriptor: SubagentDescriptor,
        project_path: Path,
        *,
        model: str | None = None,
        verbose_logs: bool = False,
        backend: ClaudeBackend | None = None,
    ) -> None:
        self.descriptor = descriptor
        self.name = descriptor.name
        self.project_path = project_path
        self.verbose_logs = verbose_logs
        cfg = load_project_config(project_path)
        if model is not None:
            self.model = model
        else:
            self.model = resolve_subagent_model(
                cfg, self.name, fallback=DEFAULT_MODEL,
            )
        if backend is not None:
            self.backend = backend
        else:
            self.backend = resolve_claude_backend(cfg)

    # ── prompt envelope ─────────────────────────────────────────────

    def build_prompt(
        self, *, directive: str, slug: str, iter_num: int,
    ) -> str:
        """Compose the runtime prompt envelope.

        The body the spawned Claude reads sits in
        ``<state_dir>/subagents/<name>.md`` (shipped from the
        descriptor's ``source_path`` during ``archon init`` and
        refreshed by the registry as needed). This envelope just
        wires up the runtime variables and the directive.
        """
        state_dir = self.project_path / ".archon"
        cfg = load_project_config(self.project_path)
        debug_feedback = bool(cfg.loop_section().get("debug_feedback"))
        read_only_note = (
            "\nYou are READ-ONLY on every project source file. Your "
            "only writable target is the report path above.\n"
            if self.descriptor.read_only else ""
        )
        return dedent(f"""\
            You are the {self.name} subagent for project '{self.project_path.name}'.
            Archon iteration: {iter_num:03d}.
            Project directory: {self.project_path}
            Project state directory: {state_dir}

            Slug: {slug}

            Read {state_dir}/subagents/{self.name}.md for your full instructions.
            Read {state_dir}/CLAUDE.md for project-wide context.

            Your directive (also reproduced below for convenience) is at:
              {state_dir}/logs/iter-{iter_num:03d}/{self.name}-{slug}-directive.md

            DIRECTIVE:
            {directive}

            Report: {state_dir}/task_results/{self.name}-{slug}.md
            (When invoked as a child of another subagent, your report
            lands at task_results/<parent_slug>/{self.name}-{slug}.md
            — the Archon CLI handles the path automatically.)
            {read_only_note}""") + _subagent_protected_block(self.project_path) \
            + debug_feedback_block(
                debug_feedback, state_dir, f"{self.name} ({slug})", iter_num,
            )

    def report_path(
        self, slug: str, *, parent_slug: str = ROOT_PARENT_SLUG,
    ) -> Path:
        base = self.project_path / ".archon" / "task_results"
        if parent_slug == ROOT_PARENT_SLUG:
            return base / f"{self.name}-{slug}.md"
        return base / parent_slug / f"{self.name}-{slug}.md"

    # ── run ─────────────────────────────────────────────────────────

    def run(
        self,
        *,
        directive: str,
        slug: str,
        iter_num: int,
        log_base: Path,
        parent_slug: str = ROOT_PARENT_SLUG,
        write_domain: list[str] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SubagentResult:
        write_domain = list(write_domain or [])

        # Hard gate: protected files (archon-protected.yaml) are read-only
        # for every agent — refuse the dispatch outright rather than trust
        # the prompt.
        assert_write_domain_not_protected(self.project_path, write_domain)

        dispatch_log = self._dispatch_log_for(log_base, iter_num)
        if dispatch_log is not None:
            self._validate_write_domain(
                dispatch_log, parent_slug, write_domain,
            )

        prompt = self.build_prompt(
            directive=directive, slug=slug, iter_num=iter_num,
        )
        agent = ClaudeAgent(model=self.model, role=self.name, backend=self.backend)

        start_ts = _now_iso()
        if dispatch_log is not None:
            _append_dispatch_jsonl(dispatch_log, {
                "ts": start_ts,
                "event": "dispatch_start",
                "role": self.name,
                "slug": slug,
                "parent_slug": parent_slug,
                "write_domain": write_domain,
                "log_base": str(log_base),
                "pid": os.getpid(),
            })

        env_overrides = {PARENT_SLUG_ENV_VAR: slug}

        start = time.monotonic()
        pool = SlotPool.from_env()
        if pool is not None:
            with pool.slot():
                ok = agent.run(
                    prompt,
                    cwd=self.project_path,
                    log_base=log_base,
                    verbose_logs=self.verbose_logs,
                    cancel_event=cancel_event,
                    env_overrides=env_overrides,
                )
        else:
            ok = agent.run(
                prompt,
                cwd=self.project_path,
                log_base=log_base,
                verbose_logs=self.verbose_logs,
                cancel_event=cancel_event,
                env_overrides=env_overrides,
            )
        dur = int(time.monotonic() - start)

        report = self.report_path(slug, parent_slug=parent_slug)
        if ok:
            log.success(f"{self.name}/{slug} finished ({dur}s)")
        else:
            log.error(f"{self.name}/{slug} failed ({dur}s)")

        if dispatch_log is not None:
            _append_dispatch_jsonl(dispatch_log, {
                "ts": _now_iso(),
                "event": "dispatch_end",
                "role": self.name,
                "slug": slug,
                "parent_slug": parent_slug,
                "ok": ok,
                "duration_s": dur,
                "report_path": str(report) if report.exists() else None,
            })

        return SubagentResult(
            ok=ok,
            duration_s=dur,
            report_path=report if report.exists() else None,
            summary=self._extract_summary(report),
        )

    # ── helpers ─────────────────────────────────────────────────────

    def _dispatch_log_for(
        self, log_base: Path, iter_num: int,
    ) -> Path | None:
        candidate = log_base.parent
        if not candidate.name.startswith("iter-"):
            return None
        return candidate / "dispatch.jsonl"

    def _validate_write_domain(
        self,
        dispatch_log: Path,
        parent_slug: str,
        write_domain: list[str],
    ) -> None:
        if parent_slug == ROOT_PARENT_SLUG:
            return
        if not write_domain:
            return
        rows = _read_dispatch_jsonl(dispatch_log)
        parent = _find_parent_record(rows, parent_slug)
        if parent is None:
            raise WriteDomainViolation(
                f"Cannot find parent record for parent_slug={parent_slug!r} "
                f"in {dispatch_log}; refusing to dispatch."
            )
        parent_domain = list(parent.get("write_domain") or [])
        if not _domain_covers(parent_domain, write_domain):
            raise WriteDomainViolation(
                f"Child write_domain={write_domain!r} is not a subset of "
                f"parent's domain={parent_domain!r} (parent_slug={parent_slug!r})."
            )

    @staticmethod
    def _extract_summary(report: Path) -> str:
        if not report.exists():
            return ""
        try:
            for line in report.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or s.startswith("<!--"):
                    continue
                return s[:120]
        except OSError:
            return ""
        return ""

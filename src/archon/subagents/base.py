"""Base class for Python-driven Archon subagents.

Each subagent is a thin wrapper around ``ClaudeAgent`` that bakes in a
fixed role tag, prompt template, and report path. Both the CLI (e.g.
``archon refactor run``) and the in-loop tool scripts (e.g.
``.claude/tools/archon-refactor-agent.py``) instantiate these classes
and call ``run()``, so the two execution paths share one code path —
which is the whole point of the migration: the stream parser sees
every subagent invocation regardless of who triggered it.

Hierarchical dispatch (Workstream A): when ``ARCHON_DISPATCH_SLOTS_DIR``
is set (the autonomous loop sets it at iter start), ``run()`` also:

* Acquires a slot from the per-iteration :class:`~archon.dispatch.SlotPool`
  so the total number of concurrent Claude subagent processes stays
  bounded by ``max_parallel`` regardless of how deep the subagent tree
  is.
* Validates that the caller-declared ``write_domain`` is a subset of
  the parent's domain (parent's record comes from ``dispatch.jsonl``).
* Appends a start/end record to ``logs/iter-NNN/dispatch.jsonl`` so the
  dashboard can render the subagent tree and downstream tooling can
  audit who wrote what.

When the env var is absent (the manual CLI flow ``archon refactor run``),
all of the above is skipped — behaviour matches the pre-dispatch model.
"""

from __future__ import annotations

import fnmatch
import json
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from archon import log
from archon.agent import ClaudeAgent, DEFAULT_MODEL
from archon.commands.tooling.project_config import (
    load_project_config,
    resolve_subagent_model,
)
from archon.dispatch import SlotPool


# Env var that tells a Claude subprocess what slug its parent subagent
# was running under. Set by ``Subagent.run`` for each spawned child;
# the wrapper script reads it and forwards via ``--parent-slug``.
PARENT_SLUG_ENV_VAR = "ARCHON_SUBAGENT_SLUG"

# Marker used when a subagent has no parent (i.e. it was launched
# directly by the plan agent, not by another subagent). Reports for
# root-level invocations keep the legacy flat layout
# ``task_results/<role>-<slug>.md`` rather than landing under
# ``task_results/_root/`` — preserves back-compat with the dashboard,
# existing prompts, and any scripts that grep task_results/.
ROOT_PARENT_SLUG = "_root"


class WriteDomainViolation(RuntimeError):
    """Raised when a subagent's declared write-domain isn't ⊆ its parent's."""


@dataclass
class SubagentResult:
    ok: bool
    duration_s: int
    report_path: Path | None        # None if the agent didn't write one
    summary: str                    # one-line summary, "" if none


@dataclass
class DispatchRecord:
    """One row in ``logs/iter-NNN/dispatch.jsonl``.

    Two rows are emitted per Subagent.run call:
    * ``event="dispatch_start"`` just before agent.run
    * ``event="dispatch_end"`` after, with ``ok`` and ``duration_s``
    """
    role: str
    slug: str
    parent_slug: str
    write_domain: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_dispatch_jsonl(path: Path, row: dict[str, object]) -> None:
    """Append one JSON object to dispatch.jsonl, creating it if needed.

    Best-effort: an OSError here doesn't abort the run, just degrades
    the dashboard's subagent-tree view.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        log.warn(f"dispatch.jsonl write failed at {path}: {e}")


def _read_dispatch_jsonl(path: Path) -> list[dict[str, object]]:
    """Read dispatch.jsonl, returning [] if missing or unreadable."""
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
    """Return the most recent ``dispatch_start`` row for ``parent_slug``.

    ``_root`` has no record (top-level subagents are fully trusted).
    Returns the start row so the caller can read its ``write_domain``.
    """
    if parent_slug == ROOT_PARENT_SLUG:
        return None
    for row in reversed(rows):
        if row.get("event") != "dispatch_start":
            continue
        if row.get("slug") == parent_slug:
            return row
    return None


def _domain_covers(parent_globs: list[str], child_globs: list[str]) -> bool:
    """True iff every glob in ``child_globs`` is covered by ``parent_globs``.

    Coverage is structural: child glob X is covered when some parent
    glob P matches X *as a string*, OR when fnmatch agrees that any
    path P matches would also match X. This is best-effort static
    glob-containment — we don't expand against the filesystem. Two
    cheap cases handle the realistic uses:

    * Parent ``**`` (or unrestricted): covers any child.
    * Parent ``Algebra/**`` covers child ``Algebra/Foo.lean`` and
      ``Algebra/Bar/**`` etc.

    Conservative: when in doubt, we refuse. That's the safer default
    for a hard error.
    """
    if not parent_globs:
        # Parent declared nothing — interpret as "unrestricted"
        # (only the root parent can do this in practice).
        return True
    for child in child_globs:
        if not any(_glob_covers(p, child) for p in parent_globs):
            return False
    return True


def _glob_covers(parent: str, child: str) -> bool:
    """One parent-glob covers one child-glob?

    Heuristic that handles the common cases for Lean projects:
    * Equal strings → covered.
    * Parent ends with ``**`` and child startswith the prefix → covered.
    * Parent is a directory prefix of child (substring before ``/**``) → covered.
    * fnmatch agreement on the child as a literal path → covered.
    Otherwise → not covered.
    """
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
        # Parent matches one segment under prefix; child can be that one
        # segment, or a sub-glob entirely under the same segment.
        if "/" not in child.removeprefix(prefix + "/") and child.startswith(prefix + "/"):
            return True
    # Fallback: treat child as a literal path candidate.
    if fnmatch.fnmatch(child, parent):
        return True
    return False


class Subagent(ABC):
    """One Python-driven subagent invocation.

    Subclasses set ``name`` (used as the role tag, the report-filename
    stem, and the config.json key) and override ``build_prompt``. The
    default ``report_path`` uses
    ``task_results/<parent_slug>/<name>-<slug>.md`` (or the flat layout
    when ``parent_slug == _root``); subclasses can override only when
    their report convention truly differs.
    """

    name: str = ""

    def __init__(
        self,
        project_path: Path,
        *,
        model: str | None = None,
        verbose_logs: bool = False,
    ) -> None:
        self.project_path = project_path
        self.verbose_logs = verbose_logs
        if model is not None:
            self.model = model
        else:
            cfg = load_project_config(project_path)
            self.model = resolve_subagent_model(
                cfg, self.name, fallback=DEFAULT_MODEL,
            )

    # ── overrides ────────────────────────────────────────────────────

    @abstractmethod
    def build_prompt(
        self, *, directive: str, slug: str, iter_num: int,
    ) -> str:
        ...

    def report_path(
        self, slug: str, *, parent_slug: str = ROOT_PARENT_SLUG,
    ) -> Path:
        """Where the subagent is told to write its report.

        Default: ``task_results/<role>-<slug>.md`` for root invocations
        (back-compat), ``task_results/<parent>/<role>-<slug>.md``
        otherwise. Subclasses override only if the location truly
        differs.
        """
        base = self.project_path / ".archon" / "task_results"
        if parent_slug == ROOT_PARENT_SLUG:
            return base / f"{self.name}-{slug}.md"
        return base / parent_slug / f"{self.name}-{slug}.md"

    # ── run ──────────────────────────────────────────────────────────

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
        """Run the subagent.

        ``parent_slug`` identifies which subagent (if any) spawned this
        one. ``ROOT_PARENT_SLUG`` indicates a top-level invocation by
        the plan agent. ``write_domain`` is the caller-declared list of
        glob patterns this subagent (and any of its descendants) is
        allowed to write to; validated against the parent's declared
        domain via ``dispatch.jsonl``.

        ``cancel_event`` is forwarded to ``ClaudeAgent.run``; setting it
        from another thread tears down the spawned ``claude`` process
        the same way multilane does for slow lanes. When cancellation
        wins the race the result has ``ok=False`` and any partial
        report on disk is still picked up.
        """
        write_domain = list(write_domain or [])

        # Validate write-domain against the parent's record before
        # spawning anything — fail-fast saves a useless Claude run.
        dispatch_log = self._dispatch_log_for(log_base, iter_num)
        if dispatch_log is not None:
            self._validate_write_domain(
                dispatch_log, parent_slug, write_domain,
            )

        prompt = self.build_prompt(
            directive=directive, slug=slug, iter_num=iter_num,
        )
        agent = ClaudeAgent(model=self.model, role=self.name)

        # Record dispatch_start BEFORE acquiring a slot so the
        # dashboard sees "waiting for slot" entries promptly.
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

        # Tell our child Claude what slug to forward as parent on its
        # own subagent calls. The wrapper script reads this env var.
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
            # CLI flow (archon refactor run) — no slot pool exists.
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

    # ── helpers ──────────────────────────────────────────────────────

    def _dispatch_log_for(
        self, log_base: Path, iter_num: int,
    ) -> Path | None:
        """Locate ``logs/iter-NNN/dispatch.jsonl`` for this run.

        Returns ``None`` when we're not inside an Archon loop iteration
        (the manual ``archon refactor run`` flow uses a different log
        layout). Detection: ``log_base`` should sit under an iter dir;
        if it doesn't, the SlotPool will also be unset and we skip all
        of the dispatch bookkeeping.
        """
        # log_base is typically <state>/logs/iter-NNN/<role>-<slug>
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
        """Raise WriteDomainViolation if write_domain ⊄ parent's domain."""
        if parent_slug == ROOT_PARENT_SLUG:
            # Top-level invocations are trusted (plan agent is in
            # charge). Nothing to validate against.
            return
        if not write_domain:
            # No domain declared; nothing to check. (Children that
            # don't declare a domain are implicitly trusted within
            # their parent's domain — the parent's own validation
            # already constrained the family.)
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

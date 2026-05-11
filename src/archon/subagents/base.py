"""Base class for Python-driven Archon subagents.

Each subagent is a thin wrapper around ``ClaudeAgent`` that bakes in a
fixed role tag, prompt template, and report path. Both the CLI (e.g.
``archon refactor run``) and the in-loop tool scripts (e.g.
``.claude/tools/archon-refactor-agent.py``) instantiate these classes
and call ``run()``, so the two execution paths share one code path —
which is the whole point of the migration: the stream parser sees
every subagent invocation regardless of who triggered it.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from archon import log
from archon.agent import ClaudeAgent, DEFAULT_MODEL
from archon.commands.tooling.project_config import (
    load_project_config,
    resolve_subagent_model,
)


@dataclass
class SubagentResult:
    ok: bool
    duration_s: int
    report_path: Path | None        # None if the agent didn't write one
    summary: str                    # one-line summary, "" if none


class Subagent(ABC):
    """One Python-driven subagent invocation.

    Subclasses set ``name`` (used as the role tag, the report-filename
    stem, and the config.json key) and override ``build_prompt`` /
    ``report_path``. Everything else — model resolution, log layout,
    return-code interpretation — comes from ``ClaudeAgent``.
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

    @abstractmethod
    def report_path(self, slug: str) -> Path:
        """Where the subagent is told to write its report."""

    # ── run ──────────────────────────────────────────────────────────

    def run(
        self,
        *,
        directive: str,
        slug: str,
        iter_num: int,
        log_base: Path,
        cancel_event: threading.Event | None = None,
    ) -> SubagentResult:
        """Run the subagent.

        ``cancel_event`` is forwarded to ``ClaudeAgent.run``; setting it
        from another thread tears down the spawned ``claude`` process
        the same way multilane does for slow lanes. When cancellation
        wins the race the result has ``ok=False`` and any partial
        report on disk is still picked up.
        """
        prompt = self.build_prompt(
            directive=directive, slug=slug, iter_num=iter_num,
        )
        agent = ClaudeAgent(model=self.model, role=self.name)
        start = time.monotonic()
        ok = agent.run(
            prompt,
            cwd=self.project_path,
            log_base=log_base,
            verbose_logs=self.verbose_logs,
            cancel_event=cancel_event,
        )
        dur = int(time.monotonic() - start)

        report = self.report_path(slug)
        if ok:
            log.success(f"{self.name}/{slug} finished ({dur}s)")
        else:
            log.error(f"{self.name}/{slug} failed ({dur}s)")

        return SubagentResult(
            ok=ok,
            duration_s=dur,
            report_path=report if report.exists() else None,
            summary=self._extract_summary(report),
        )

    # ── helpers ──────────────────────────────────────────────────────

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
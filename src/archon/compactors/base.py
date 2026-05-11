"""Base class for pre-agent compactors.

Compactors are smaller, single-purpose Claude runs that rewrite a
state file in place. They share two invariants:

1. **Lossless on actionable content**: errors, dead ends, Mathlib gaps,
   user hints — every piece of cumulative knowledge survives.
2. **Lossy only on old narrative**: prose describing how a closed
   theorem was proved, iteration-by-iteration commentary, redundant
   context that newer entries already cover.

The base class handles model resolution, threshold gating, log layout,
and the rewrite-then-verify loop. Subclasses pin the target file and
the prompt.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent

from archon import log
from archon.agent import ClaudeAgent
from archon.commands.tooling.project_config import (
    CompactionTargetCfg,
    load_project_config,
    resolve_compaction_model,
    resolve_compaction_target,
)


@dataclass
class CompactorResult:
    """Outcome of one compactor run.

    ``ran`` is False when the file was below threshold (skipped); True
    on every actual Claude invocation, even if it failed mid-way. Use
    ``ok`` to gate on success and ``changed`` to know if the rewrite
    actually shrunk anything (for the inner-git commit summary).
    """
    ran: bool
    ok: bool
    duration_s: int
    target_path: Path
    pre_size: int
    post_size: int
    changed: bool

    @property
    def saved_chars(self) -> int:
        return max(0, self.pre_size - self.post_size)


class Compactor(ABC):
    """One pre-agent compactor.

    Subclasses set:

    - ``name``           — used as the role tag (``compact-strategy``,
                           etc.) and as the inner-git commit's ``slug``.
    - ``config_key``     — key under ``compaction.targets`` in
                           ``config.json`` (``strategy_md``, …).
    - ``target_relpath`` — file path relative to ``.archon/`` (e.g.
                           ``"STRATEGY.md"``).
    - ``prompt_filename``— bundled prompt under ``.archon/prompts/``
                           (e.g. ``"compactor-strategy.md"``).

    Subclasses normally don't need to override ``run`` — the base
    handles size check, prompt build, and dispatch. ``build_prompt``
    has a sensible default that injects the file's path and the
    iteration number; override only if a compactor needs special
    framing.
    """

    name: str = ""
    config_key: str = ""
    target_relpath: str = ""
    prompt_filename: str = ""

    def __init__(
        self,
        project_path: Path,
        *,
        model: str | None = None,
        verbose_logs: bool = False,
        target_cfg: CompactionTargetCfg | None = None,
    ) -> None:
        self.project_path = project_path
        self.state_dir = project_path / ".archon"
        self.target_path = self.state_dir / self.target_relpath
        self.verbose_logs = verbose_logs

        cfg = load_project_config(project_path)
        if model is not None:
            self.model = model
        else:
            self.model = resolve_compaction_model(cfg)
        self.target_cfg = target_cfg or resolve_compaction_target(
            cfg, self.config_key,
        )

    # ── gating ──────────────────────────────────────────────────────

    def needs_compaction(self) -> tuple[bool, str]:
        """Return (should-run, human-readable reason).

        Reason is shown in logs and (on skip) helps the user understand
        why nothing happened. We never raise — callers expect a
        decision, not an exception.
        """
        if not self.target_cfg.enabled:
            return False, "disabled in config"
        if not self.target_path.exists():
            return False, f"target missing: {self.target_path}"
        try:
            size = self.target_path.stat().st_size
        except OSError as e:
            return False, f"could not stat target: {e}"
        if size < self.target_cfg.min_chars:
            return (
                False,
                f"size {size} < threshold {self.target_cfg.min_chars}",
            )
        return True, f"size {size} >= threshold {self.target_cfg.min_chars}"

    # ── prompt ──────────────────────────────────────────────────────

    def build_prompt(self, *, iter_num: int) -> str:
        """Default prompt: tell the agent where the file is, where its
        instructions are, and what iteration it's running in.

        Subclasses can override for richer framing. The bundled prompt
        file (``compactor-<role>.md``) carries the actual rules — keep
        the inline prompt minimal so prompt edits don't require a
        Python release.
        """
        prompts_dir = self.state_dir / "prompts"
        return dedent(f"""\
            You are the {self.name} compactor for project '{self.project_path.name}'.
            Archon iteration: {iter_num:03d}.
            Project directory: {self.project_path}
            Project state directory: {self.state_dir}

            Target file (read and rewrite IN PLACE):
              {self.target_path}

            Read your instructions from:
              {prompts_dir / self.prompt_filename}

            Critical rules (the prompt file expands these):
            - You may rewrite ONLY {self.target_path}. Do not touch any
              other file.
            - Information preservation is non-negotiable: every error,
              dead end, Mathlib gap, user-hint cross-reference, and
              recent-iteration detail must survive. Compress only old
              narrative and redundant prose.
            - If unsure whether a paragraph is load-bearing, KEEP it.
            - Your final assistant message must be one line: either
              "COMPACTED: <pre> -> <post> chars" or
              "UNCHANGED: <reason>" if nothing was worth compacting.
            """)

    # ── run ─────────────────────────────────────────────────────────

    def run(
        self,
        *,
        iter_num: int,
        log_base: Path,
        cancel_event: threading.Event | None = None,
    ) -> CompactorResult:
        """Spawn the compactor.

        ``log_base`` is the file stem for the JSONL logs (no
        extension). Caller decides where it lives — typically
        ``iter-NNN/compact-<role>``.
        """
        empty = CompactorResult(
            ran=False, ok=True, duration_s=0,
            target_path=self.target_path,
            pre_size=0, post_size=0, changed=False,
        )

        ok_to_run, reason = self.needs_compaction()
        if not ok_to_run:
            log.info(f"{self.name}: skipped ({reason})")
            return empty

        try:
            pre_size = self.target_path.stat().st_size
        except OSError:
            pre_size = 0

        prompt = self.build_prompt(iter_num=iter_num)
        agent = ClaudeAgent(model=self.model, role=self.name)

        log.info(f"{self.name}: compacting {self.target_path} "
                 f"(pre={pre_size} chars)")
        start = time.monotonic()
        ok = agent.run(
            prompt,
            cwd=self.project_path,
            log_base=log_base,
            verbose_logs=self.verbose_logs,
            cancel_event=cancel_event,
        )
        dur = int(time.monotonic() - start)

        try:
            post_size = self.target_path.stat().st_size
        except OSError:
            post_size = pre_size

        changed = post_size != pre_size
        if ok and changed:
            log.success(
                f"{self.name}: {pre_size} -> {post_size} chars "
                f"({pre_size - post_size:+d}, {dur}s)"
            )
        elif ok:
            log.info(f"{self.name}: no change ({dur}s)")
        else:
            log.error(f"{self.name}: failed ({dur}s)")

        return CompactorResult(
            ran=True, ok=ok, duration_s=dur,
            target_path=self.target_path,
            pre_size=pre_size, post_size=post_size,
            changed=changed,
        )

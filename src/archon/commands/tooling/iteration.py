"""Per-phase and per-iteration finalization.

The inner git at `.archon/.git` gets one commit per agent phase (plan,
refactor, all-provers, review, finalize). The outer git is never touched
by Archon after init.

`lake build` and `leanblueprint web` still run at the end of each
iteration as best-effort verification — failures are surfaced but never
raise.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from archon import log
from archon.commands.tooling.blueprint import Blueprint
from archon.commands.tooling.inner_git import InnerGit
from archon.commands.tooling.lake import Lake


@dataclass
class IterationFinalizationReport:
    """What happened during this iteration's finalize step."""
    inner_git_sha: str | None = None
    inner_git_skipped: bool = False
    lake_build_ok: bool | None = None
    lake_build_error: str | None = None
    lake_build_secs: int | None = None
    blueprint_web_ok: bool | None = None
    blueprint_web_error: str | None = None
    blueprint_web_secs: int | None = None
    warnings: list[str] = field(default_factory=list)

    def to_meta_dict(self) -> dict[str, object]:
        """Return a dict suitable for merging into the iteration's meta.json."""
        d: dict[str, object] = {}
        if self.inner_git_sha is not None:
            d["finalize.innerGit.sha"] = self.inner_git_sha
        if self.inner_git_skipped:
            d["finalize.innerGit.skipped"] = True
        if self.lake_build_ok is not None:
            d["finalize.lake.ok"] = self.lake_build_ok
            d["finalize.lake.durationSecs"] = self.lake_build_secs or 0
            if self.lake_build_error:
                d["finalize.lake.error"] = self.lake_build_error[:500]
        if self.blueprint_web_ok is not None:
            d["finalize.blueprint.ok"] = self.blueprint_web_ok
            d["finalize.blueprint.durationSecs"] = self.blueprint_web_secs or 0
            if self.blueprint_web_error:
                d["finalize.blueprint.error"] = self.blueprint_web_error[:500]
        return d


# ── per-phase commit helper ───────────────────────────────────────────


def commit_phase(
    project_path: str | Path,
    *,
    iter_num: int,
    phase: str,
    summary: str,
    file_slug: str | None = None,
) -> str | None:
    """Commit the current inner-git tree as "archon[NNN/phase(/slug)]: summary".

    Best-effort: if the inner git isn't initialized or there's nothing to
    commit, returns None without raising.
    """
    git = InnerGit(project_path)
    if not git.is_initialized():
        return None
    try:
        made, sha = git.commit_phase(
            iter_num=iter_num, phase=phase,
            summary=summary, file_slug=file_slug,
        )
    except Exception as e:
        log.warn(f"inner git commit failed (phase={phase}): {e}")
        return None
    if not made:
        return None
    suffix = f"/{file_slug}" if file_slug else ""
    log.success(f"[inner git] archon[{iter_num:03d}/{phase}{suffix}] {sha}")
    return sha


# ── end-of-iteration finalizer ────────────────────────────────────────


class IterationFinalizer:
    """Run non-fatal cleanup/verification at the end of each loop iteration.

    Three steps, all best-effort:
      1. Commit everything still dirty in the inner git as
         `archon[NNN/finalize]: <summary>`.
      2. `lake build` — surface errors, don't raise.
      3. `leanblueprint web` — surface errors, don't raise.

    Per-phase commits happen earlier, inside the loop, via `commit_phase`.
    This finalizer just catches any leftover state (task_results archival,
    sorry-count snapshot, etc.) and wraps up the iteration.
    """

    def __init__(
        self,
        project_path: str | Path,
        *,
        do_git: bool = True,
        do_lake_build: bool = True,
        do_blueprint_web: bool = True,
    ):
        self.project_path = Path(project_path).resolve()
        self.do_git = do_git
        self.do_lake_build = do_lake_build
        self.do_blueprint_web = do_blueprint_web

    def run(
        self,
        *,
        iter_num: int,
        stage: str,
        sorry_count: int | None = None,
    ) -> IterationFinalizationReport:
        report = IterationFinalizationReport()

        if self.do_git:
            self._commit(report, iter_num, stage, sorry_count)

        if self.do_lake_build:
            self._lake_build(report)

        if self.do_blueprint_web:
            self._blueprint_web(report)

        return report

    # ── steps ─────────────────────────────────────────────────────────

    def _commit(
        self,
        report: IterationFinalizationReport,
        iter_num: int,
        stage: str,
        sorry_count: int | None,
    ) -> None:
        git = InnerGit(self.project_path)
        if not git.is_initialized():
            report.inner_git_skipped = True
            return

        if not git.is_dirty():
            r = git._run(["status", "--porcelain"], check=False)
            if not r.stdout.strip():
                report.inner_git_skipped = True
                return

        summary = f"stage={stage}"
        if sorry_count is not None:
            summary += f" sorry={sorry_count}"

        try:
            made, sha = git.commit_phase(
                iter_num=iter_num, phase="finalize", summary=summary,
            )
        except Exception as e:
            report.warnings.append(f"inner git commit failed: {e}")
            report.inner_git_skipped = True
            return

        if not made:
            report.inner_git_skipped = True
            return

        report.inner_git_sha = sha
        log.success(f"Committed: archon[{iter_num:03d}/finalize] {summary}"
                    + (f" ({sha})" if sha else ""))

    def _lake_build(self, report: IterationFinalizationReport) -> None:
        if not Lake.available():
            report.warnings.append("lake not on PATH — skipping build")
            return

        lake = Lake(self.project_path)
        info = lake.lakefile_info()
        if info.path is None:
            report.warnings.append("no lakefile found — skipping build")
            return

        start = time.monotonic()
        try:
            lake.build()
            report.lake_build_ok = True
            report.lake_build_secs = int(time.monotonic() - start)
            log.success(f"lake build ok ({report.lake_build_secs}s)")
        except Exception as e:
            report.lake_build_ok = False
            report.lake_build_secs = int(time.monotonic() - start)
            full_error = str(e)
            report.lake_build_error = full_error

            state_dir = self.project_path / ".archon"
            if state_dir.is_dir():
                try:
                    (state_dir / "last_lake_build.log").write_text(
                        full_error, encoding="utf-8"
                    )
                except OSError:
                    pass

            preview = full_error.strip().splitlines()
            snippet = ""
            for line in reversed(preview):
                low = line.lower()
                if "error" in low or "failed" in low:
                    snippet = line.strip()[:300]
                    break
            if not snippet and preview:
                snippet = preview[-1].strip()[:300]

            log.warn(f"lake build failed ({report.lake_build_secs}s): {snippet or 'unknown'}")
            log.step(f"Full output: {state_dir / 'last_lake_build.log'}")
            log.step("Continuing — the plan agent will see the error next iteration")

    def _blueprint_web(self, report: IterationFinalizationReport) -> None:
        bp = Blueprint(self.project_path)
        if not bp.is_initialized():
            return
        if not Blueprint.available():
            report.warnings.append("leanblueprint not on PATH — skipping web build")
            return

        start = time.monotonic()
        try:
            bp.web()
            report.blueprint_web_ok = True
            report.blueprint_web_secs = int(time.monotonic() - start)
            log.success(f"leanblueprint web ok ({report.blueprint_web_secs}s)")
        except Exception as e:
            report.blueprint_web_ok = False
            report.blueprint_web_secs = int(time.monotonic() - start)
            report.blueprint_web_error = str(e)
            preview = str(e).strip().splitlines()
            preview_text = " / ".join(preview[-2:])[:300] if preview else "unknown"
            log.warn(f"leanblueprint web failed ({report.blueprint_web_secs}s): {preview_text}")
            log.step("Continuing — the plan agent may need to adjust chapter TeX")

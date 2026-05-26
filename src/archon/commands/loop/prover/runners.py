"""Serial and parallel prover runners.

`SerialProverRunner` runs one prover invocation in the project's main
checkout. `ParallelProverRunner` fans out one prover per file in
`PROGRESS.md ## Current Objectives` over a `ProcessPoolExecutor`.

Both runners write meta status into the iteration's `meta.json` so the
dashboard can surface live state.
"""

from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from archon import log
from archon.agent import ClaudeAgent, ClaudeBackend
from archon.prompts import (
    build_parallel_prover_prompt,
    build_prover_prompt,
)
from archon.state import (
    archive_task_results,
    parse_objective_files,
    parse_objectives_with_modes,
    write_meta,
)

from ..resume import PROVER_CONTINUE, persist_session_id, pick_resume_session
from ..utils import file_slug, relpath
from .environment import ProverEnvironment, snapshot_baseline


def _load_mode_content(state_dir: Path, mode_name: str | None) -> str | None:
    """Load the body of a prover-mode descriptor (after frontmatter), or None."""
    if not mode_name:
        return None
    mode_file = state_dir / "prover-modes" / f"{mode_name}.md"
    if not mode_file.exists():
        return None
    text = mode_file.read_text(encoding="utf-8")
    # Strip YAML frontmatter (---...---) so only the body is injected.
    import re as _re
    stripped = _re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=_re.DOTALL)
    return stripped.strip() or None


def _run_single_prover(
    prompt: str,
    cwd: Path,
    log_base: Path,
    verbose_logs: bool,
    model: str,
    snap_dir: Path | None = None,
    project_path: Path | None = None,
    resume_session_id: str | None = None,
    backend: ClaudeBackend | None = None,
) -> bool:
    """Top-level for `ProcessPoolExecutor` — must be importable by the worker."""
    agent = ClaudeAgent(model=model, role="prover", backend=backend or ClaudeBackend())
    if snap_dir is not None and project_path is not None:
        with ProverEnvironment(
            snap_dir=snap_dir,
            prover_jsonl=Path(str(log_base) + ".jsonl"),
            project_path=project_path,
        ):
            return agent.run(
                prompt, cwd=cwd, log_base=log_base, verbose_logs=verbose_logs,
                resume_session_id=resume_session_id,
            )
    return agent.run(
        prompt, cwd=cwd, log_base=log_base, verbose_logs=verbose_logs,
        resume_session_id=resume_session_id,
    )


class SerialProverRunner:
    """Runs a single prover prompt over the whole stage.

    The plan agent's objectives mention chapters; we don't pre-split by
    file because we don't know which file a serial run will touch in
    which order.
    """

    def __init__(
        self,
        *,
        project_name: str,
        project_path: Path,
        state_dir: Path,
        stage: str,
        iter_dir: Path,
        iter_num: int,
        verbose_logs: bool,
        model: str,
        debug_feedback: bool = False,
        iter_meta: Path | None = None,
        resume_enabled: bool = False,
        backend: ClaudeBackend | None = None,
    ) -> None:
        self.project_name = project_name
        self.project_path = project_path
        self.state_dir = state_dir
        self.stage = stage
        self.iter_dir = iter_dir
        self.iter_num = iter_num
        self.verbose_logs = verbose_logs
        self.model = model
        self.debug_feedback = debug_feedback
        self.iter_meta = iter_meta
        self.resume_enabled = resume_enabled
        self.backend = backend or ClaudeBackend()

    def run(self, *, dry_run: bool, progress_file: Path) -> None:
        prompt = build_prover_prompt(
            self.project_name, self.project_path, self.state_dir, self.stage,
            self.iter_num, debug_feedback=self.debug_feedback,
        )
        if dry_run:
            log.step("[dry-run] Prover prompt:")
            print(prompt)
            return

        archive_task_results(self.state_dir, self.iter_dir)

        prover_log = self.iter_dir / "prover"
        for sf in parse_objective_files(progress_file, self.project_path):
            srel = relpath(sf, self.project_path)
            sslug = file_slug(srel)
            snapshot_baseline(sf, self.iter_dir / "snapshots" / sslug)

        resume_sid = pick_resume_session(
            self.iter_meta, "prover.sessionId",
            enabled=self.resume_enabled, label="prover",
            cwd=self.project_path,
            jsonl_fallback=Path(str(prover_log) + ".jsonl"),
        )
        with ProverEnvironment(
            snap_dir=self.iter_dir / "snapshots",
            prover_jsonl=Path(str(prover_log) + ".jsonl"),
            project_path=self.project_path,
            serial_mode=True,
        ):
            ClaudeAgent(model=self.model, role="prover", backend=self.backend).run(
                PROVER_CONTINUE if resume_sid else prompt,
                cwd=self.project_path,
                log_base=prover_log, verbose_logs=self.verbose_logs,
                resume_session_id=resume_sid,
            )
        persist_session_id(
            self.iter_meta, Path(str(prover_log) + ".jsonl"),
            "prover.sessionId",
        )


class ParallelProverRunner:
    """Runs one prover per objective file in a process pool.

    Single-file rounds collapse to serial-with-blueprint-pointer for
    determinism: spawning a process pool for one worker just adds noise.
    """

    def __init__(
        self,
        *,
        project_name: str,
        project_path: Path,
        state_dir: Path,
        stage: str,
        iter_dir: Path,
        iter_meta: Path,
        iter_num: int,
        max_parallel: int,
        max_objectives: int,
        block_on_blocked_deps: bool,
        verbose_logs: bool,
        model: str,
        dashboard_url: str | None = None,
        blueprint_url: str | None = None,
        debug_feedback: bool = False,
        resume_enabled: bool = False,
        backend: ClaudeBackend | None = None,
    ) -> None:
        self.project_name = project_name
        self.project_path = project_path
        self.state_dir = state_dir
        self.stage = stage
        self.iter_dir = iter_dir
        self.iter_meta = iter_meta
        self.iter_num = iter_num
        self.max_parallel = max_parallel
        self.max_objectives = max_objectives
        self.block_on_blocked_deps = block_on_blocked_deps
        self.verbose_logs = verbose_logs
        self.model = model
        self.dashboard_url = dashboard_url
        self.blueprint_url = blueprint_url
        self.debug_feedback = debug_feedback
        self.resume_enabled = resume_enabled
        self.backend = backend or ClaudeBackend()

    def run(self, *, dry_run: bool) -> None:
        progress = self.state_dir / "PROGRESS.md"
        archive_task_results(self.state_dir, self.iter_dir)

        objectives_with_modes = parse_objectives_with_modes(progress, self.project_path)
        sorry_files = [p for p, _ in objectives_with_modes]
        file_modes: dict[str, str | None] = {
            str(p): m for p, m in objectives_with_modes
        }
        if not sorry_files:
            log.warn("No files parsed from PROGRESS.md ## Current Objectives.")
            log.warn("The plan agent must list target files in **bold** or `backticks`.")
            log.warn("Skipping prover iteration.")
            return

        # Drop files whose transitive imports failed the previous lake
        # build. plan_validate already filtered these and hinted the
        # planner; the runner enforces it again so a stale PROGRESS.md
        # replayed via --from prover still gets the filter applied.
        if self.block_on_blocked_deps:
            from ..blocked_deps import (
                build_local_import_graph,
                filter_objectives_for_blocked_deps,
                parse_blocked_files_from_log,
            )
            log_path = self.state_dir / "last_lake_build.log"
            blocked = parse_blocked_files_from_log(
                log_path, project_path=self.project_path,
            )
            if blocked:
                graph = build_local_import_graph(self.project_path)
                sorry_files, dropped = filter_objectives_for_blocked_deps(
                    sorry_files,
                    blocked=blocked,
                    graph=graph,
                    project_path=self.project_path,
                )
                if dropped:
                    log.warn(
                        f"Dropped {len(dropped)} objective(s) whose "
                        f"transitive imports failed the previous lake "
                        f"build — they were already hinted to the "
                        f"planner via USER_HINTS."
                    )
                if not sorry_files:
                    log.warn(
                        "All objectives are blocked by upstream compile "
                        "errors; skipping prover dispatch."
                    )
                    return

        # Hard cap on dispatched provers. plan_validate already warned
        # loudly and queued the deferred list into USER_HINTS; we slice
        # here so the dispatcher is deterministic even if plan_validate
        # was bypassed (e.g. --from prover replay of a stale PROGRESS.md
        # that's over the cap).
        if len(sorry_files) > self.max_objectives:
            log.warn(
                f"PROGRESS.md lists {len(sorry_files)} objectives — "
                f"capping dispatch at {self.max_objectives}. The remaining "
                f"{len(sorry_files) - self.max_objectives} files are "
                f"already queued for the next plan agent via USER_HINTS."
            )
            sorry_files = sorry_files[: self.max_objectives]

        file_count = len(sorry_files)

        if dry_run:
            for f in sorry_files:
                mode = file_modes.get(str(f))
                mode_tag = f" [mode: {mode}]" if mode else ""
                log.step(f"[dry-run] Prover: {relpath(f, self.project_path)}{mode_tag}")
            return

        if file_count == 1:
            self._run_single_file(sorry_files[0], mode_name=file_modes.get(str(sorry_files[0])))
            return

        self._run_fanout(sorry_files, file_modes=file_modes)

    def _run_single_file(self, target: Path, mode_name: str | None = None) -> None:
        rel = relpath(target, self.project_path)
        slug = file_slug(rel)
        log.info(f"Only 1 file ({rel}) — running serial prover")

        prover_log = self.iter_dir / "provers" / slug
        meta_update: dict[str, object] = {
            f"provers.{slug}.file": rel,
            f"provers.{slug}.status": "running",
        }
        if mode_name:
            meta_update[f"provers.{slug}.mode"] = mode_name
        write_meta(self.iter_meta, **meta_update)

        snap_dir = self.iter_dir / "snapshots" / slug
        snapshot_baseline(target, snap_dir)

        mode_content = _load_mode_content(self.state_dir, mode_name)
        base_prompt = build_parallel_prover_prompt(
            self.project_name, self.project_path, self.state_dir, self.stage,
            self.iter_num,
            assigned_rel_lean_path=rel,
            debug_feedback=self.debug_feedback,
            mode_name=mode_name,
            mode_content=mode_content,
        )
        prompt = f"{base_prompt}\nYour assigned file: {rel}"
        resume_sid = pick_resume_session(
            self.iter_meta, f"provers.{slug}.sessionId",
            enabled=self.resume_enabled, label=f"prover[{slug}]",
            cwd=self.project_path,
            jsonl_fallback=Path(str(prover_log) + ".jsonl"),
        )
        with ProverEnvironment(
            snap_dir=snap_dir,
            prover_jsonl=Path(str(prover_log) + ".jsonl"),
            project_path=self.project_path,
        ):
            ok = ClaudeAgent(model=self.model, role="prover", backend=self.backend).run(
                PROVER_CONTINUE if resume_sid else prompt,
                cwd=self.project_path,
                log_base=prover_log, verbose_logs=self.verbose_logs,
                resume_session_id=resume_sid,
            )

        persist_session_id(
            self.iter_meta, Path(str(prover_log) + ".jsonl"),
            f"provers.{slug}.sessionId",
        )
        write_meta(
            self.iter_meta,
            **{f"provers.{slug}.status": "done" if ok else "error"},
        )

    def _run_fanout(self, sorry_files: list[Path], *, file_modes: dict[str, str | None] | None = None) -> None:
        file_count = len(sorry_files)
        log.info(
            f"Found {file_count} file(s) — launching parallel provers "
            f"(max {self.max_parallel} concurrent)"
        )

        log.info("Watch progress:")
        if self.dashboard_url:
            log.step(f"Dashboard:       {self.dashboard_url}")
            log.step(f"Iteration view:  {self.dashboard_url}/logs")
        if self.blueprint_url:
            log.step(f"Blueprint:       {self.blueprint_url}")
        log.step(f"tail -f {self.iter_dir}/provers/*.jsonl")
        log.step(f"watch -n10 'ls -lt {self.state_dir}/task_results/'")

        file_modes = file_modes or {}
        futures = {}
        prover_logs: dict[str, Path] = {}
        with ProcessPoolExecutor(
            max_workers=min(self.max_parallel, file_count),
        ) as pool:
            for f in sorry_files:
                rel = relpath(f, self.project_path)
                slug = file_slug(rel)
                prover_log = self.iter_dir / "provers" / slug
                prover_logs[slug] = prover_log

                mode_name = file_modes.get(str(f))
                mode_content = _load_mode_content(self.state_dir, mode_name)
                base_prompt = build_parallel_prover_prompt(
                    self.project_name, self.project_path, self.state_dir, self.stage,
                    self.iter_num,
                    assigned_rel_lean_path=rel,
                    debug_feedback=self.debug_feedback,
                    mode_name=mode_name,
                    mode_content=mode_content,
                )
                prompt = f"{base_prompt}\nYour assigned file: {rel}"

                snap_dir = self.iter_dir / "snapshots" / slug
                snapshot_baseline(f, snap_dir)

                # Per-slug resume: each parallel prover keeps its own
                # session id under provers.<slug>.sessionId. Files added
                # in this round that weren't part of the prior run have
                # no stored id; pick_resume_session degrades to fresh
                # (or recovers from the slug's JSONL fallback if the
                # prior run crashed mid-prove).
                resume_sid = pick_resume_session(
                    self.iter_meta, f"provers.{slug}.sessionId",
                    enabled=self.resume_enabled, label=f"prover[{slug}]",
                    cwd=self.project_path,
                    jsonl_fallback=Path(str(prover_log) + ".jsonl"),
                )
                if resume_sid:
                    submit_prompt = PROVER_CONTINUE
                else:
                    submit_prompt = prompt

                log.step(f"Starting prover for {rel}")
                meta_update: dict[str, object] = {
                    f"provers.{slug}.file": rel,
                    f"provers.{slug}.status": "running",
                }
                if mode_name:
                    meta_update[f"provers.{slug}.mode"] = mode_name
                write_meta(self.iter_meta, **meta_update)

                future = pool.submit(
                    _run_single_prover,
                    submit_prompt, self.project_path, prover_log,
                    self.verbose_logs, self.model,
                    snap_dir, self.project_path, resume_sid,
                    self.backend,
                )
                futures[future] = (rel, slug)

            failed = 0
            for future in as_completed(futures):
                rel, slug = futures[future]
                try:
                    ok = future.result()
                except Exception:
                    ok = False
                # Stamp the session id from the prover's JSONL — works
                # whether this was a fresh run or a --resume continuation
                # (Claude reports the same session id back on resume, so
                # the next --resume keeps targeting the same conversation).
                persist_session_id(
                    self.iter_meta,
                    Path(str(prover_logs[slug]) + ".jsonl"),
                    f"provers.{slug}.sessionId",
                )
                status = "done" if ok else "error"
                write_meta(self.iter_meta, **{f"provers.{slug}.status": status})
                if ok:
                    log.success(f"Prover finished: {rel}")
                else:
                    log.error(f"Prover failed: {rel}")
                    failed += 1

        if failed:
            log.warn(f"{failed}/{file_count} prover(s) had errors")
        else:
            log.success(f"All {file_count} prover(s) finished")

        results_dir = self.state_dir / "task_results"
        result_count = len(list(results_dir.glob("*.md"))) if results_dir.exists() else 0
        log.info(f"Task result files: {result_count}/{file_count}")

        self._emit_round_end(file_count, failed)

    def _emit_round_end(self, prover_count: int, failed: int) -> None:
        provers_dir = self.iter_dir / "provers"
        target = None
        if provers_dir.exists():
            # is_file() filters dangling symlinks — left over from cancelled
            # multilane runs or prior runs with a different lane set.
            logs = sorted(
                p for p in provers_dir.glob("*.jsonl")
                if not p.name.endswith(".raw.jsonl") and p.is_file()
            )
            if logs:
                target = logs[0]
        if target is None:
            target = self.iter_dir / "parallel.jsonl"

        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": "parallel_round_end",
            "prover_count": prover_count,
            "failed": failed,
        }
        with target.open("a") as f:
            f.write(json.dumps(row) + "\n")

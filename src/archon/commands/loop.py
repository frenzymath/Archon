"""Start the automated plan → prove → review loop."""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from textwrap import dedent
from typing import Optional

import typer

from archon import log
from archon.agent import ClaudeAgent, DEFAULT_MODEL
from archon.commands.tooling.blueprint import BlueprintServer
from archon.commands.tooling.inner_git import InnerGit
from archon.commands.tooling.iteration import (
    IterationFinalizer,
    IterationFinalizationReport,
    commit_phase,
)
from archon.commands.tooling.version import warn_if_mismatch
from archon.runner import (
    build_parallel_prover_prompt,
    build_plan_prompt,
    build_prover_prompt,
    build_refactor_prompt,
    build_review_prompt,
)
from archon.state import (
    CostData,
    archive_task_results,
    cost_summary,
    is_complete,
    next_iter_num,
    next_session_num,
    parse_objective_files,
    read_stage,
    utcnow_iso,
    write_meta,
)
from archon.types import Stage


def _data_path(sub_path: str = "") -> Path:
    root = resources.files("archon").joinpath(".archon-src")
    if sub_path:
        return Path(str(root.joinpath(sub_path)))
    return Path(str(root))


def _relpath(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _file_slug(rel: str) -> str:
    return rel.replace("/", "_").replace(os.sep, "_").removesuffix(".lean")


# ── inner git dirty warning ───────────────────────────────────────────


def _warn_if_inner_dirty(project_path: Path) -> None:
    """If the inner git has leftover state, tell the user; do not block."""
    inner = InnerGit(project_path)
    if not inner.is_initialized() or not inner.is_dirty():
        return

    log.warn(
        "Inner git has uncommitted agent work — leftover from a previous "
        "run or manual edits. This is fine: the loop will pick up whatever "
        "is on disk, and the next phase commit will capture it."
    )


# ── dashboard auto-launch ─────────────────────────────────────────────


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _find_free_port(start: int = 8080, attempts: int = 20) -> int | None:
    for p in range(start, start + attempts):
        if _port_free(p):
            return p
    return None


def _start_dashboard(project_path: Path, open_browser: bool) -> tuple[subprocess.Popen | None, int | None]:
    if not shutil.which("node") or not shutil.which("npm"):
        log.warn("Dashboard skipped: Node.js / npm not found (run: archon setup)")
        return None, None

    port = _find_free_port(8080)
    if port is None:
        log.warn("Dashboard skipped: could not find a free port in 8080–8099")
        return None, None

    cmd = ["archon", "dashboard", str(project_path), "--port", str(port)]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        log.warn(f"Dashboard failed to start: {e}")
        return None, None

    for _ in range(10):
        time.sleep(0.5)
        if not _port_free(port):
            break
        if proc.poll() is not None:
            log.warn("Dashboard process exited before binding its port")
            return None, None

    url = f"http://localhost:{port}"
    log.panel(
        f"Dashboard is live at [bold cyan]{url}[/bold cyan]\n"
        f"Watch iterations, parallel provers, diffs, and the proof journal update live.",
        title="Archon Dashboard",
        style="cyan",
    )

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _cleanup():
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
    atexit.register(_cleanup)

    return proc, port


def _start_blueprint_server(project_path: Path) -> tuple[BlueprintServer | None, str | None]:
    """Start the blueprint web server. Returns (server, url) or (None, None).

    The server can only start if blueprint/web/ exists (has been built by
    `leanblueprint web` at least once). The caller is expected to re-try
    this after the first iteration's finalize step builds the web output.
    """
    server = BlueprintServer(project_path)
    if not server.available:
        log.info("Blueprint server deferred: blueprint/web/ not built yet — "
                 "will try again after the first iteration's finalize step.")
        return server, None

    proc, port = server.start()
    if proc is None or port is None:
        log.warn("Blueprint server failed to start")
        return server, None

    url = f"http://localhost:{port}"
    log.panel(
        f"Blueprint preview at [bold cyan]{url}[/bold cyan]\n"
        f"Serves the HTML rendering of blueprint/ — refreshes on each iteration's "
        f"`leanblueprint web` build.",
        title="Blueprint",
        style="cyan",
    )
    atexit.register(server.stop)
    return server, url


def _maybe_start_deferred_blueprint_server(
    server: BlueprintServer | None,
    current_url: str | None,
) -> str | None:
    """If the server hasn't launched yet but is now available, start it.

    Called after each iteration's finalize step. Returns the updated URL.
    """
    if server is None or current_url is not None:
        return current_url
    if not server.available:
        return None

    proc, port = server.start()
    if proc is None or port is None:
        return None
    url = f"http://localhost:{port}"
    log.panel(
        f"Blueprint preview at [bold cyan]{url}[/bold cyan]\n"
        f"First `leanblueprint web` build completed — server is live.",
        title="Blueprint",
        style="cyan",
    )
    atexit.register(server.stop)
    return url


# ── sorry counting ───────────────────────────────────────────────────


def _count_sorries(project_path: Path) -> int | None:
    analyzer = _data_path("skills/lean4/lib/scripts/sorry_analyzer.py")
    if analyzer.exists():
        try:
            r = subprocess.run(
                [sys.executable, str(analyzer), str(project_path), "--format=summary"],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0 and r.stdout.strip():
                last_line = r.stdout.strip().splitlines()[-1]
                m = re.search(r"(\d+)", last_line)
                if m:
                    return int(m.group(1))
        except Exception:
            pass

    try:
        r = subprocess.run(
            ["bash", "-c",
             "find " + str(project_path) + " -name '*.lean' -not -path '*/.lake/*' "
             "-not -path '*/lake-packages/*' "
             "| xargs grep -c 'sorry' 2>/dev/null "
             "| grep -v ':0$' | awk -F: '{s+=$2} END {print s}'"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.strip())
    except Exception:
        pass

    return None


# ── environment checks ────────────────────────────────────────────────


def _check_informal_agent_keys() -> None:
    keys = ("OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY")
    if not any(os.environ.get(k) for k in keys):
        log.warn("No API keys for informal agent (OPENAI_API_KEY / GEMINI_API_KEY / OPENROUTER_API_KEY)")
        log.step("Provers will work without it, but may struggle on hard sorries where external LLM help would be useful.")


# ── refactor phase ────────────────────────────────────────────────────


def _read_refactor_directive(state_dir: Path) -> str | None:
    directive_file = state_dir / "REFACTOR_DIRECTIVE.md"
    if not directive_file.exists():
        return None
    content = directive_file.read_text().strip()
    if not content:
        return None
    lines = [l.strip() for l in content.splitlines() if l.strip() and not l.strip().startswith("<!--")]
    non_header = [l for l in lines if not l.startswith("#")]
    if not non_header:
        return None
    return content


def _archive_refactor_directive(directive: str, iter_dir: Path) -> None:
    iter_dir.mkdir(parents=True, exist_ok=True)
    archive_path = iter_dir / "refactor-directive.md"
    header = dedent(f"""\
        <!-- Archived from REFACTOR_DIRECTIVE.md at {utcnow_iso()} -->
        <!-- This is the directive the plan agent wrote for the refactor agent in this iteration. -->

        """)
    archive_path.write_text(header + directive + "\n")


def _archive_refactor_report(state_dir: Path, iter_dir: Path) -> None:
    report_src = state_dir / "task_results" / "refactor.md"
    if not report_src.exists():
        return
    iter_dir.mkdir(parents=True, exist_ok=True)
    report_dst = iter_dir / "refactor-report.md"
    try:
        shutil.copy2(report_src, report_dst)
    except OSError:
        pass


def _clear_refactor_directive(state_dir: Path) -> None:
    directive_file = state_dir / "REFACTOR_DIRECTIVE.md"
    if directive_file.exists():
        directive_file.write_text(
            "# Refactor Directive\n\n"
            "<!-- Plan agent: write your refactoring directive here. -->\n"
            "<!-- The refactor agent will execute it at the start of the next iteration. -->\n"
            "<!-- This file is cleared after each refactor run. -->\n"
        )


def _build_post_refactor_plan_prompt(
    project_name: str, project_path: Path, state_dir: Path, stage: str,
) -> str:
    return dedent(f"""\
        You are the plan agent for project '{project_name}'. Current stage: {stage}.
        Project directory: {project_path}
        Project state directory: {state_dir}
        Read {state_dir}/CLAUDE.md for your role, then read {state_dir}/prompts/plan.md and {state_dir}/PROGRESS.md.
        All state files (PROGRESS.md, task_pending.md, task_done.md, USER_HINTS.md, task_results/) are in {state_dir}/.
        The .lean files are in {project_path}/.

        IMPORTANT — POST-REFACTOR VERIFICATION PASS:
        The refactor agent has just run. Read {state_dir}/task_results/refactor.md FIRST
        to understand what changed. Then follow the "Post-Refactor Verification" section
        in your prompt (plan.md).

        CRITICAL: Do NOT write a new REFACTOR_DIRECTIVE.md in this pass. The refactor
        loop runs at most once per iteration. If further refactoring is needed, document
        it in task_pending.md — it will be addressed in the next iteration.""")


def _run_refactor_phase(
    project_name: str,
    project_path: Path,
    state_dir: Path,
    directive: str,
    iter_dir: Path,
    iter_meta: Path,
    verbose_logs: bool,
    model: str,
) -> bool:
    log.phase(2, "Refactor agent")
    log.info("Plan agent requested structural changes")

    directive_lines = directive.strip().splitlines()
    preview_lines = [l.strip() for l in directive_lines
                     if l.strip() and not l.strip().startswith("#") and not l.strip().startswith("<!--")]
    for line in preview_lines[:5]:
        log.step(line)
    if len(preview_lines) > 5:
        log.step("... (%d more lines)" % (len(preview_lines) - 5))

    _archive_refactor_directive(directive, iter_dir)
    write_meta(iter_meta, **{"refactor.status": "running"})

    refactor_start = time.monotonic()
    prompt = build_refactor_prompt(project_name, project_path, state_dir, directive)
    refactor_log = iter_dir / "refactor"
    ok = ClaudeAgent(model=model, role="refactor").run(
        prompt, cwd=project_path, log_base=refactor_log, verbose_logs=verbose_logs,
    )
    refactor_secs = int(time.monotonic() - refactor_start)

    write_meta(iter_meta, **{
        "refactor.status": "done" if ok else "error",
        "refactor.durationSecs": refactor_secs,
    })

    if ok:
        log.success("Refactor agent finished (%ds)" % refactor_secs)
    else:
        log.error("Refactor agent failed (%ds)" % refactor_secs)

    _clear_refactor_directive(state_dir)
    return ok


# ── snapshot / env helpers ────────────────────────────────────────────


def _snapshot_baseline(file_path: Path, snap_dir: Path) -> None:
    snap_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(file_path, snap_dir / "baseline.lean")
    except OSError:
        pass


def _set_prover_env(
    snap_dir: Path | str,
    prover_jsonl: Path | str,
    project_path: Path | str,
    serial_mode: bool = False,
) -> dict[str, str]:
    old = {}
    env_vars = {
        "ARCHON_SNAPSHOT_DIR": str(snap_dir),
        "ARCHON_PROVER_JSONL": str(prover_jsonl),
        "ARCHON_PROJECT_PATH": str(project_path),
    }
    if serial_mode:
        env_vars["ARCHON_SERIAL_MODE"] = "true"
    for k, v in env_vars.items():
        old[k] = os.environ.get(k)
        os.environ[k] = v
    return old


def _unset_prover_env(old: dict[str, str]) -> None:
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ── preflight ─────────────────────────────────────────────────────────


def _preflight(project_path: Path, state_dir: Path, dry_run: bool) -> None:
    progress = state_dir / "PROGRESS.md"

    if not dry_run:
        if not shutil.which("claude"):
            log.error("Claude Code is not installed. Run: archon setup")
            raise typer.Exit(1)
        r = subprocess.run(
            ["claude", "-p", "reply with OK", "--no-session-persistence"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            log.error("Claude Code cannot run. Check: claude auth, ANTHROPIC_API_KEY, network.")
            raise typer.Exit(1)
        log.success("Claude Code is authenticated and ready")

    if not progress.exists():
        log.error(f"No project state found. Run: archon init {project_path}")
        raise typer.Exit(1)

    stage = read_stage(progress)
    if stage == "init":
        log.error(f"Project is still in init stage. Run: archon init {project_path}")
        raise typer.Exit(1)


def _emit_parallel_round_end(iter_dir: Path, prover_count: int, failed: int) -> None:
    provers_dir = iter_dir / "provers"
    target = None
    if provers_dir.exists():
        logs = sorted(provers_dir.glob("*.jsonl"))
        if logs:
            target = logs[0]
    if target is None:
        target = iter_dir / "parallel.jsonl"

    row = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": "parallel_round_end",
        "prover_count": prover_count,
        "failed": failed,
    }
    with target.open("a") as f:
        f.write(json.dumps(row) + "\n")


# ── parallel provers ──────────────────────────────────────────────────


def _run_single_prover(
    prompt: str,
    cwd: Path,
    log_base: Path,
    verbose_logs: bool,
    model: str,
    snap_dir: Path | None = None,
    project_path: Path | None = None,
) -> bool:
    if snap_dir is not None and project_path is not None:
        old_env = _set_prover_env(
            snap_dir=snap_dir,
            prover_jsonl=Path(str(log_base) + ".jsonl"),
            project_path=project_path,
        )
    else:
        old_env = None

    try:
        return ClaudeAgent(model=model, role="prover").run(
            prompt, cwd=cwd, log_base=log_base, verbose_logs=verbose_logs,
        )
    finally:
        if old_env is not None:
            _unset_prover_env(old_env)


def _run_parallel_provers(
    project_name: str,
    project_path: Path,
    state_dir: Path,
    stage: str,
    iter_dir: Path,
    iter_meta: Path,
    max_parallel: int,
    verbose_logs: bool,
    dry_run: bool,
    model: str,
    dashboard_url: str | None = None,
    blueprint_url: str | None = None,
) -> None:
    progress = state_dir / "PROGRESS.md"
    archive_task_results(state_dir, iter_dir)

    sorry_files = parse_objective_files(progress, project_path)
    if not sorry_files:
        log.warn("No files parsed from PROGRESS.md ## Current Objectives.")
        log.warn("The plan agent must list target files in **bold** or `backticks`.")
        log.warn("Skipping prover iteration.")
        return

    file_count = len(sorry_files)

    if dry_run:
        for f in sorry_files:
            rel = _relpath(f, project_path)
            log.step(f"[dry-run] Prover: {rel}")
        return

    # Single file → run serial (but still with blueprint-aware prompt)
    if file_count == 1:
        rel = _relpath(sorry_files[0], project_path)
        slug = _file_slug(rel)
        log.info(f"Only 1 file ({rel}) — running serial prover")

        prover_log = iter_dir / "provers" / slug
        write_meta(iter_meta, **{f"provers.{slug}.file": rel, f"provers.{slug}.status": "running"})

        snap_dir = iter_dir / "snapshots" / slug
        _snapshot_baseline(sorry_files[0], snap_dir)

        old_env = _set_prover_env(
            snap_dir=snap_dir,
            prover_jsonl=Path(str(prover_log) + ".jsonl"),
            project_path=project_path,
        )
        try:
            base_prompt = build_parallel_prover_prompt(
                project_name, project_path, state_dir, stage,
                assigned_rel_lean_path=rel,
            )
            prompt = f"{base_prompt}\nYour assigned file: {rel}"
            ok = ClaudeAgent(model=model, role="prover").run(
                prompt, cwd=project_path, log_base=prover_log, verbose_logs=verbose_logs,
            )
        finally:
            _unset_prover_env(old_env)

        write_meta(iter_meta, **{f"provers.{slug}.status": "done" if ok else "error"})
        return

    log.info(f"Found {file_count} file(s) — launching parallel provers (max {max_parallel} concurrent)")

    log.info("Watch progress:")
    if dashboard_url:
        log.step(f"Dashboard:       {dashboard_url}")
        log.step(f"Iteration view:  {dashboard_url}/logs")
    if blueprint_url:
        log.step(f"Blueprint:       {blueprint_url}")
    log.step(f"tail -f {iter_dir}/provers/*.jsonl")
    log.step(f"watch -n10 'ls -lt {state_dir}/task_results/'")

    futures = {}
    with ProcessPoolExecutor(max_workers=min(max_parallel, file_count)) as pool:
        for f in sorry_files:
            rel = _relpath(f, project_path)
            slug = _file_slug(rel)
            prover_log = iter_dir / "provers" / slug

            # Build a per-file prompt so each prover gets the blueprint
            # chapter pointer for its specific file.
            base_prompt = build_parallel_prover_prompt(
                project_name, project_path, state_dir, stage,
                assigned_rel_lean_path=rel,
            )
            prompt = f"{base_prompt}\nYour assigned file: {rel}"

            snap_dir = iter_dir / "snapshots" / slug
            _snapshot_baseline(f, snap_dir)

            log.step(f"Starting prover for {rel}")
            write_meta(iter_meta, **{f"provers.{slug}.file": rel, f"provers.{slug}.status": "running"})

            future = pool.submit(
                _run_single_prover,
                prompt, project_path, prover_log, verbose_logs, model,
                snap_dir, project_path,
            )
            futures[future] = (rel, slug)

        failed = 0
        for future in as_completed(futures):
            rel, slug = futures[future]
            try:
                ok = future.result()
            except Exception:
                ok = False
            status = "done" if ok else "error"
            write_meta(iter_meta, **{f"provers.{slug}.status": status})
            if ok:
                log.success(f"Prover finished: {rel}")
            else:
                log.error(f"Prover failed: {rel}")
                failed += 1

    if failed:
        log.warn(f"{failed}/{file_count} prover(s) had errors")
    else:
        log.success(f"All {file_count} prover(s) finished")

    results_dir = state_dir / "task_results"
    result_count = len(list(results_dir.glob("*.md"))) if results_dir.exists() else 0
    log.info(f"Task result files: {result_count}/{file_count}")

    _emit_parallel_round_end(iter_dir, file_count, failed)


# ── review phase ──────────────────────────────────────────────────────


def _run_review_phase(
    project_name: str,
    project_path: Path,
    state_dir: Path,
    stage: str,
    iter_dir: Path,
    verbose_logs: bool,
    model: str,
) -> None:
    session_num = next_session_num(state_dir)
    journal_dir = state_dir / "proof-journal"
    session_dir = journal_dir / "sessions" / f"session_{session_num}"
    current_session_dir = journal_dir / "current_session"
    attempts_file = current_session_dir / "attempts_raw.jsonl"

    session_dir.mkdir(parents=True, exist_ok=True)
    current_session_dir.mkdir(parents=True, exist_ok=True)

    log.step("Extracting attempt data from prover logs...")
    provers_dir = iter_dir / "provers"
    if provers_dir.exists() and list(provers_dir.glob("*.jsonl")):
        combined = iter_dir / "provers-combined.jsonl"
        with combined.open("w") as out:
            for jf in sorted(provers_dir.glob("*.jsonl")):
                out.write(jf.read_text())
    else:
        combined = iter_dir / "prover.jsonl"

    extract_script = _data_path("scripts/extract-attempts.py")
    if extract_script.exists():
        subprocess.run(
            [sys.executable, str(extract_script), str(combined), str(attempts_file)],
            capture_output=True,
        )

    prompt = build_review_prompt(
        project_name, project_path, state_dir, stage,
        session_num, session_dir, attempts_file, combined,
    )
    review_log = iter_dir / "review"
    ClaudeAgent(model=model, role="review").run(
        prompt, cwd=project_path, log_base=review_log, verbose_logs=verbose_logs,
    )

    validate_script = _data_path("scripts/validate-review.py")
    if validate_script.exists():
        subprocess.run(
            [sys.executable, str(validate_script), str(session_dir), str(attempts_file)],
            capture_output=True,
        )


# ── finalize phase ────────────────────────────────────────────────────


def _run_finalize_phase(
    project_path: Path,
    iter_num: int,
    stage: str,
    sorry_count: int | None,
    iter_meta: Path,
    *,
    do_git: bool,
    do_lake_build: bool,
    do_blueprint_web: bool,
) -> IterationFinalizationReport:
    log.phase(5, "Finalize (git / lake / blueprint)")

    finalizer = IterationFinalizer(
        project_path,
        do_git=do_git,
        do_lake_build=do_lake_build,
        do_blueprint_web=do_blueprint_web,
    )
    report = finalizer.run(iter_num=iter_num, stage=stage, sorry_count=sorry_count)

    write_meta(iter_meta, **report.to_meta_dict())
    for w in report.warnings:
        log.warn(w)

    return report


# ── main command ──────────────────────────────────────────────────────


def loop(
    project_path: str = typer.Argument(".", help="Path to Lean project"),
    max_iterations: int = typer.Option(
        10, "--max-iterations", "-m", help="Max plan→prover→review cycles.",
    ),
    max_parallel: int = typer.Option(
        8, "--max-parallel", help="Max concurrent provers in parallel mode.",
    ),
    stage: Optional[Stage] = typer.Option(
        None, "--stage", "-s",
        help="Force a stage instead of reading from PROGRESS.md.",
    ),
    parallel: bool = typer.Option(
        True, "--parallel/--serial",
        help="Run provers in parallel (one per file) or serially.",
    ),
    verbose_logs: bool = typer.Option(
        False, "--verbose-logs",
        help="Save raw Claude stream events to .raw.jsonl.",
    ),
    no_review: bool = typer.Option(
        False, "--no-review",
        help="Skip review phase after each iteration.",
    ),
    no_refactor: bool = typer.Option(
        False, "--no-refactor",
        help="Skip the refactor phase even if a directive exists.",
    ),
    no_finalize: bool = typer.Option(
        False, "--no-finalize",
        help="Skip the end-of-iteration git commit / lake build / blueprint web.",
    ),
    no_git_commit: bool = typer.Option(
        False, "--no-git-commit",
        help="Skip only the per-iteration git commit (keeps lake/blueprint).",
    ),
    no_lake_build: bool = typer.Option(
        False, "--no-lake-build",
        help="Skip only the per-iteration `lake build`.",
    ),
    no_blueprint_web: bool = typer.Option(
        False, "--no-blueprint-web",
        help="Skip only the per-iteration `leanblueprint web`.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print prompts without launching Claude.",
    ),
    no_dashboard: bool = typer.Option(
        False, "--no-dashboard",
        help="Do not auto-start the web dashboard.",
    ),
    blueprint_server_flag: bool = typer.Option(
        False, "--blueprint-server",
        help="Start a local HTTP server serving blueprint/web/ alongside the dashboard.",
    ),
    open_browser: bool = typer.Option(
        False, "--open",
        help="Open the dashboard in a browser as soon as it starts.",
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-M",
        help="Claude model alias (e.g. 'opus', 'sonnet') or full id used for "
             "every plan / refactor / prover / review phase in the loop.",
    ),
) -> None:
    """Start the automated plan → prove → review loop.

    Each iteration:
      1. Plan agent (reads project state, writes objectives)
      2. Refactor agent (only if the plan wrote REFACTOR_DIRECTIVE.md)
      3. Prover agent(s) — parallel or serial
      4. Review agent (unless --no-review)
      5. Finalize: git commit, lake build, leanblueprint web (non-fatal)

    By default the web dashboard is launched in the background. Pass
    --blueprint-server to also start the blueprint HTML server.
    """
    resolved = Path(project_path).resolve()
    project_name = resolved.name
    state_dir = resolved / ".archon"
    progress_file = state_dir / "PROGRESS.md"
    log_dir = state_dir / "logs"
    force_stage = stage.value if stage else None

    _preflight(resolved, state_dir, dry_run)

    if not dry_run:
        log_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "task_results").mkdir(exist_ok=True)
        (state_dir / "proof-journal" / "sessions").mkdir(parents=True, exist_ok=True)
        (state_dir / "proof-journal" / "current_session").mkdir(parents=True, exist_ok=True)

    current_stage = read_stage(progress_file, force_stage)

    prover_mode = "parallel" if parallel else "serial"
    if parallel:
        prover_mode += f" (max {max_parallel})"

    # Resolve per-step finalize flags.
    do_git = not no_finalize and not no_git_commit
    do_lake = not no_finalize and not no_lake_build
    do_bp_web = not no_finalize and not no_blueprint_web

    config = {
        "Project": str(resolved),
        "Stage": force_stage or current_stage,
        "Max iterations": str(max_iterations),
        "Prover mode": prover_mode,
        "Review": "enabled" if not no_review else "disabled",
        "Refactor": "enabled" if not no_refactor else "disabled",
        "Finalize": _describe_finalize(do_git, do_lake, do_bp_web),
        "Dashboard": "disabled" if no_dashboard else "enabled",
        "Blueprint server": "enabled" if blueprint_server_flag else "disabled",
        "Logs": str(log_dir),
        "User hints": str(state_dir / "USER_HINTS.md"),
    }
    if dry_run:
        config["Mode"] = "[yellow]DRY RUN[/yellow]"

    log.header("Archon Loop")
    log.key_value(config)
    warn_if_mismatch(resolved)

    # Warn (but do not block) if the inner git has leftover agent work.
    # The user may have Ctrl-C'd a previous loop mid-phase, or manually
    # edited .lean files between runs. We continue normally — the plan
    # agent will see whatever is on disk and the next phase commit will
    # capture it.
    _warn_if_inner_dirty(resolved)

    # ── Start background services ────────────────────────────────────
    dashboard_url: str | None = None
    blueprint_server: BlueprintServer | None = None
    blueprint_url: str | None = None
    if not dry_run and not no_dashboard:
        _, dashboard_port = _start_dashboard(resolved, open_browser)
        if dashboard_port:
            dashboard_url = f"http://localhost:{dashboard_port}"

    if not dry_run and blueprint_server_flag:
        blueprint_server, blueprint_url = _start_blueprint_server(resolved)

    if is_complete(progress_file, force_stage):
        log.success(f"Project '{project_name}' is COMPLETE. Nothing to do.")
        if dashboard_url:
            log.step(f"Review results in the dashboard: {dashboard_url}")
        return

    if not dry_run:
        _check_informal_agent_keys()

    initial_sorry = _count_sorries(resolved) if not dry_run else None
    if initial_sorry is not None:
        log.info(f"Starting sorry count: {initial_sorry}")

    if not dashboard_url:
        log.info(f"To visualize progress and logs, run: `archon dashboard {project_path}`")

    loop_start = time.monotonic()
    prev_sorry = initial_sorry

    for i in range(max_iterations):
        current_stage = read_stage(progress_file, force_stage)

        if is_complete(progress_file, force_stage):
            log.success("PROGRESS.md says COMPLETE. Exiting loop.")
            break

        log.iteration(i + 1, max_iterations, current_stage, project_name)
        if dashboard_url:
            log.step(f"Live view: {dashboard_url}")
        if blueprint_url:
            log.step(f"Blueprint: {blueprint_url}")

        iter_start = time.monotonic()

        # ── Iteration directory setup ───────────────────────────────
        iter_dir: Path | None = None
        iter_meta: Path | None = None
        iter_num_local: int = 0
        if not dry_run:
            iter_num_local = next_iter_num(log_dir)
            iter_dir = log_dir / f"iter-{iter_num_local:03d}"
            iter_meta = iter_dir / "meta.json"
            iter_dir.mkdir(parents=True, exist_ok=True)
            if parallel:
                (iter_dir / "provers").mkdir(exist_ok=True)
            write_meta(
                iter_meta,
                iteration=iter_num_local,
                stage=current_stage,
                mode="parallel" if parallel else "serial",
                startedAt=utcnow_iso(),
            )
            write_meta(iter_meta, **{"plan.status": "running"})
            log.step(f"Log dir: {iter_dir}")

        # ── Phase 1: Plan ──
        log.phase(1, "Plan agent")
        plan_start = time.monotonic()
        plan_prompt = build_plan_prompt(project_name, resolved, state_dir, current_stage)

        if dry_run:
            log.step("[dry-run] Plan prompt:")
            print(plan_prompt)
        else:
            plan_log = iter_dir / "plan"
            ClaudeAgent(model=model, role="plan").run(
                plan_prompt, cwd=resolved, log_base=plan_log, verbose_logs=verbose_logs,
            )

        plan_secs = int(time.monotonic() - plan_start)
        log.info(f"Plan phase finished ({plan_secs}s)")
        if not dry_run:
            write_meta(iter_meta, **{"plan.status": "done", "plan.durationSecs": plan_secs})
            commit_phase(
                resolved, iter_num=iter_num_local, phase="plan",
                summary=f"stage={current_stage} ({plan_secs}s)",
            )

        if is_complete(progress_file, force_stage):
            log.success("PROGRESS.md says COMPLETE. Exiting loop.")
            break

        current_stage = read_stage(progress_file, force_stage)

        # ── Phase 2: Refactor (conditional) ──
        if not no_refactor and not dry_run:
            directive = _read_refactor_directive(state_dir)
            if directive:
                _run_refactor_phase(
                    project_name, resolved, state_dir, directive,
                    iter_dir, iter_meta, verbose_logs, model,
                )

                log.step("Re-running plan agent to verify refactor results...")
                post_refactor_prompt = _build_post_refactor_plan_prompt(
                    project_name, resolved, state_dir, current_stage,
                )
                plan_log2 = iter_dir / "plan-post-refactor"
                ClaudeAgent(model=model, role="plan-post-refactor").run(
                    post_refactor_prompt, cwd=resolved,
                    log_base=plan_log2, verbose_logs=verbose_logs,
                )

                rogue_directive = _read_refactor_directive(state_dir)
                if rogue_directive:
                    log.warn("Post-refactor plan agent wrote another REFACTOR_DIRECTIVE.md — "
                             "clearing it to prevent infinite loop. It will be reconsidered next iteration.")
                    _clear_refactor_directive(state_dir)

                _archive_refactor_report(state_dir, iter_dir)

                sorry_after_refactor = _count_sorries(resolved)
                if sorry_after_refactor is not None:
                    log.info(f"Sorry count after refactor: {sorry_after_refactor}")

                commit_phase(
                    resolved, iter_num=iter_num_local, phase="refactor",
                    summary=(f"sorry={sorry_after_refactor}"
                             if sorry_after_refactor is not None
                             else "refactor complete"),
                )

                if is_complete(progress_file, force_stage):
                    log.success("Plan agent set stage to COMPLETE after refactor. Exiting loop.")
                    break

                current_stage = read_stage(progress_file, force_stage)

        # ── Phase 3: Prover ──
        log.phase(3, f"Prover agent(s) — {'parallel' if parallel else 'serial'}")

        prover_start = time.monotonic()
        if not dry_run:
            write_meta(iter_meta, **{"prover.status": "running"})

        if parallel:
            _run_parallel_provers(
                project_name, resolved, state_dir, current_stage,
                iter_dir, iter_meta, max_parallel, verbose_logs, dry_run, model,
                dashboard_url=dashboard_url,
                blueprint_url=blueprint_url,
            )
        else:
            # Serial mode — no per-file blueprint pointer since we don't know
            # which file gets touched in which order. Plan agent's objectives
            # mention the chapters.
            prover_prompt = build_prover_prompt(project_name, resolved, state_dir, current_stage)
            if dry_run:
                log.step("[dry-run] Prover prompt:")
                print(prover_prompt)
            else:
                archive_task_results(state_dir, iter_dir)

                prover_log = iter_dir / "prover"
                sorry_files = parse_objective_files(progress_file, resolved)
                if sorry_files:
                    for sf in sorry_files:
                        srel = _relpath(sf, resolved)
                        sslug = _file_slug(srel)
                        ssnap = iter_dir / "snapshots" / sslug
                        _snapshot_baseline(sf, ssnap)

                old_env = _set_prover_env(
                    snap_dir=iter_dir / "snapshots",
                    prover_jsonl=Path(str(prover_log) + ".jsonl"),
                    project_path=resolved,
                    serial_mode=True,
                )
                try:
                    ClaudeAgent(model=model, role="prover").run(
                        prover_prompt, cwd=resolved,
                        log_base=prover_log, verbose_logs=verbose_logs,
                    )
                finally:
                    _unset_prover_env(old_env)

        prover_secs = int(time.monotonic() - prover_start)
        log.info(f"Prover phase finished ({prover_secs}s)")
        if dashboard_url:
            log.step(f"Inspect diffs: {dashboard_url}/diffs")
        if not dry_run:
            write_meta(iter_meta, **{"prover.status": "done", "prover.durationSecs": prover_secs})
            mid_sorry = _count_sorries(resolved)
            commit_phase(
                resolved, iter_num=iter_num_local, phase="prover",
                summary=(f"all-provers sorry={mid_sorry} ({prover_secs}s)"
                         if mid_sorry is not None
                         else f"all-provers ({prover_secs}s)"),
            )

        # ── Phase 4: Review ──
        if not no_review and not dry_run:
            log.phase(4, "Review agent")
            review_start = time.monotonic()
            write_meta(iter_meta, **{"review.status": "running"})
            _run_review_phase(
                project_name, resolved, state_dir, current_stage,
                iter_dir, verbose_logs, model,
            )
            review_secs = int(time.monotonic() - review_start)
            log.info(f"Review phase finished ({review_secs}s)")
            if dashboard_url:
                log.step(f"Journal: {dashboard_url}/journal")
            write_meta(iter_meta, **{"review.status": "done", "review.durationSecs": review_secs})
            commit_phase(
                resolved, iter_num=iter_num_local, phase="review",
                summary=f"journal session ({review_secs}s)",
            )

        # ── Post-iteration: sorry count ─────────────────────────────
        sorry_after: int | None = None
        if not dry_run:
            sorry_after = _count_sorries(resolved)
            if sorry_after is not None:
                write_meta(iter_meta, sorry_count=sorry_after)
                if prev_sorry is not None:
                    delta = prev_sorry - sorry_after
                    if delta > 0:
                        log.success(f"Sorry count: {prev_sorry} -> {sorry_after} ({delta} resolved this iteration)")
                    elif delta == 0:
                        log.warn(f"Sorry count unchanged: {sorry_after}")
                    else:
                        log.info(f"Sorry count: {prev_sorry} -> {sorry_after} ({-delta} new — likely from refactoring)")
                else:
                    log.info(f"Sorry count: {sorry_after}")
                prev_sorry = sorry_after

        # ── Phase 5: Finalize ───────────────────────────────────────
        if not dry_run and (do_git or do_lake or do_bp_web):
            _run_finalize_phase(
                resolved,
                iter_num=iter_num_local,
                stage=current_stage,
                sorry_count=sorry_after,
                iter_meta=iter_meta,
                do_git=do_git,
                do_lake_build=do_lake,
                do_blueprint_web=do_bp_web,
            )

            # The finalize step may have just built blueprint/web/ for the
            # first time — try to start the server now if it was deferred.
            if blueprint_server_flag and blueprint_url is None:
                blueprint_url = _maybe_start_deferred_blueprint_server(
                    blueprint_server, blueprint_url,
                )

        iter_secs = int(time.monotonic() - iter_start)
        log.info(f"Iteration {i + 1} complete ({iter_secs}s)")
        if not dry_run:
            write_meta(iter_meta, completedAt=utcnow_iso(), wallTimeSecs=iter_secs)
            data = cost_summary(iter_dir)
            if data:
                log.cost_table(
                    f"Iteration {i + 1}",
                    data.totals_dict(),
                    data.model_rows() or None,
                )

    # ── Loop summary ────────────────────────────────────────────────
    loop_secs = int(time.monotonic() - loop_start)

    if not is_complete(progress_file, force_stage):
        log.warn(f"Reached max iterations ({max_iterations}). Stopping.")

    if not dry_run:
        final_sorry = _count_sorries(resolved)
        if final_sorry is not None and initial_sorry is not None:
            resolved_count = initial_sorry - final_sorry
            log.info(f"Sorries: {initial_sorry} -> {final_sorry} ({resolved_count} resolved)")
        elif final_sorry is not None:
            log.info(f"Final sorry count: {final_sorry}")

    log.info(f"Total wall time: {loop_secs}s")
    data = cost_summary(log_dir)
    if data:
        log.cost_table("Loop totals (Note: This is indicative, it doesn't take into account pro subscriptions for instance)", data.totals_dict(), data.model_rows() or None)

    if dashboard_url:
        log.panel(
            f"Loop finished. The dashboard is still running at [bold cyan]{dashboard_url}[/bold cyan].\n"
            + (f"Blueprint preview: [bold cyan]{blueprint_url}[/bold cyan]\n" if blueprint_url else "")
            + "Inspect results, then stop it with Ctrl-C or by closing this terminal.",
            title="Done",
            style="green",
        )


def _describe_finalize(do_git: bool, do_lake: bool, do_bp: bool) -> str:
    parts = []
    if do_git: parts.append("git")
    if do_lake: parts.append("lake build")
    if do_bp: parts.append("blueprint web")
    return ", ".join(parts) if parts else "disabled"



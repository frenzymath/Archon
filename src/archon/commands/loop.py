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
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
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
from archon.multilane.collect import write_preview_report, write_results_jsonl
from archon.multilane.config import (
    MultiLaneConfig,
    find_multilane_config,
    find_multilane_local_config,
    load_multilane_config,
    multilane_config_from_simple,
)
from archon.multilane.dispatch import (
    build_assignment_prompt,
    execute_assignments_preview_only,
    prepare_lanes_for_preview,
    preview_round,
    write_preview_runtime_artifacts,
)
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


# ── multilane local guards ───────────────────────────────────────────


def _git_diff_files(repo_path: Path) -> list[str]:
    """Return outer-git modified files (best-effort; empty if outer git is absent)."""
    result = subprocess.run(
        ['git', '-C', str(repo_path), 'diff', '--name-only', 'HEAD'],
        capture_output=True,
        text=True,
    )
    return sorted({line.strip() for line in (result.stdout or '').splitlines() if line.strip()})


def _non_archon_dirty_files(repo_path: Path) -> list[str]:
    return [path for path in _git_diff_files(repo_path) if not path.startswith('.archon/')]


def _restore_repo_paths(repo_path: Path, paths: list[str]) -> None:
    if not paths:
        return
    subprocess.run(
        ['git', '-C', str(repo_path), 'checkout', '--', *sorted(set(paths))],
        capture_output=True,
        text=True,
    )


def _record_assignment_file(
    *,
    raw_path: str,
    lane_root: Path,
    project_root: Path,
    changed_files: set[str],
    escaped_files: set[str],
) -> None:
    rel = str(raw_path or '').strip()
    if not rel.endswith('.lean'):
        return
    resolved = (lane_root / rel).resolve() if not os.path.isabs(rel) else Path(rel).resolve()
    try:
        changed_files.add(str(resolved.relative_to(lane_root)))
        return
    except ValueError:
        pass
    try:
        escaped_files.add(str(resolved.relative_to(project_root)))
    except ValueError:
        escaped_files.add(str(resolved))


def _assignment_code_snapshot_files(
    log_path: Path, lane_path: Path, project_path: Path,
) -> tuple[list[str], list[str], str]:
    lane_root = lane_path.resolve()
    project_root = project_path.resolve()
    changed_files: set[str] = set()
    escaped_files: set[str] = set()
    source = 'none'
    if not log_path.exists():
        return [], [], source

    fallback_tool_paths: list[str] = []
    for raw_line in log_path.read_text(encoding='utf-8', errors='ignore').splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        event = row.get('event')
        if event == 'code_snapshot':
            _record_assignment_file(
                raw_path=str(row.get('file') or ''),
                lane_root=lane_root,
                project_root=project_root,
                changed_files=changed_files,
                escaped_files=escaped_files,
            )
            if changed_files or escaped_files:
                source = 'code_snapshot'
        elif event == 'tool_call' and row.get('tool') in {'Edit', 'Write'}:
            file_path = str((row.get('input') or {}).get('file_path') or '').strip()
            if file_path.endswith('.lean'):
                fallback_tool_paths.append(file_path)

    if not changed_files and not escaped_files and fallback_tool_paths:
        for file_path in fallback_tool_paths:
            _record_assignment_file(
                raw_path=file_path,
                lane_root=lane_root,
                project_root=project_root,
                changed_files=changed_files,
                escaped_files=escaped_files,
            )
        if changed_files or escaped_files:
            source = 'tool_call'

    return sorted(changed_files), sorted(escaped_files), source


def _assert_multilane_clean_baseline(project_path: Path) -> None:
    dirty = _non_archon_dirty_files(project_path)
    if dirty:
        log.error('Multi-lane execute requires a clean main project tree (outside .archon).')
        for path in dirty:
            log.error(f'  dirty: {path}')
        raise typer.Exit(1)


def _multilane_lock_path(state_dir: Path) -> Path:
    return state_dir / 'multilane' / 'execute.lock.json'


def _acquire_multilane_lock(state_dir: Path) -> Path:
    lock_path = _multilane_lock_path(state_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            payload = json.loads(lock_path.read_text(encoding='utf-8'))
        except Exception:
            payload = {}
        pid = payload.get('pid')
        if isinstance(pid, int) and pid > 0:
            try:
                os.kill(pid, 0)
            except OSError:
                pass
            else:
                log.error(f'Multi-lane execute already running for this project (pid {pid}).')
                raise typer.Exit(1)
        lock_path.unlink(missing_ok=True)
    lock_path.write_text(
        json.dumps({'pid': os.getpid(), 'startedAt': utcnow_iso()}) + '\n',
        encoding='utf-8',
    )
    return lock_path


def _release_multilane_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    try:
        if lock_path.exists():
            lock_path.unlink()
    except OSError:
        pass


def _preferred_writeback_lane(config) -> str | None:
    for lane in config.enabled_lanes():
        if lane.provider == 'anthropic':
            return lane.lane_id
    enabled = config.enabled_lanes()
    return enabled[0].lane_id if enabled else None


def _select_writeback_rows(
    results: list[dict[str, object]],
    *,
    preferred_lane_id: str | None,
    limit: int = 1,
) -> list[dict[str, object]]:
    if not preferred_lane_id or limit <= 0:
        return []
    candidates: list[dict[str, object]] = []
    for row in sorted(results, key=lambda r: str(r.get('assignment_id', ''))):
        if row.get('lane_id') != preferred_lane_id:
            continue
        if not row.get('success'):
            continue
        if not row.get('assigned_file_only') or not row.get('verification_passed'):
            continue
        if not row.get('assigned_file') or not row.get('worktree_path'):
            continue
        candidates.append(row)
        if len(candidates) >= limit:
            break
    return candidates


def _group_writeback_candidates_by_file(
    results: list[dict[str, object]],
) -> dict[str, list[dict[str, object]]]:
    """Group successful, eligible result rows by their assigned file.

    A row is eligible iff it succeeded, did not escape its worktree,
    actually changed its assigned file, and has a worktree we can read
    from. The grouping preserves the order in which lanes finished
    (first-clean-wins) by sorting on assignment_id, so the merger can
    be told which candidate is the "first" if it falls back.
    """
    groups: dict[str, list[dict[str, object]]] = {}
    for row in sorted(results, key=lambda r: str(r.get('assignment_id', ''))):
        if not row.get('success'):
            continue
        if not row.get('assigned_file_only') or not row.get('verification_passed'):
            continue
        rel = str(row.get('assigned_file') or '')
        worktree_path = str(row.get('worktree_path') or '')
        if not rel or not worktree_path:
            continue
        groups.setdefault(rel, []).append(row)
    return groups


def _git_commit_paths(repo_path: Path, paths: list[str], message: str) -> str | None:
    """Commit specific paths in the outer git repo. Returns the commit SHA, or
    None if there was nothing to commit OR the outer git is unavailable. Any
    git failure (e.g. not initialized) is swallowed — first-clean-wins
    semantics work whether or not an outer git is present.
    """
    unique_paths = sorted({path for path in paths if path})
    if not unique_paths:
        return None
    try:
        subprocess.run(
            ['git', '-C', str(repo_path), 'add', '--', *unique_paths],
            check=True, capture_output=True, text=True,
        )
        diff = subprocess.run(
            ['git', '-C', str(repo_path), 'diff', '--cached', '--quiet', '--', *unique_paths],
            capture_output=True,
            text=True,
        )
        if diff.returncode == 0:
            return None
        subprocess.run(
            ['git', '-C', str(repo_path), 'commit', '-m', message],
            check=True, capture_output=True, text=True,
        )
        head = subprocess.run(
            ['git', '-C', str(repo_path), 'rev-parse', 'HEAD'],
            check=True, capture_output=True, text=True,
        )
        return (head.stdout or '').strip() or None
    except subprocess.CalledProcessError:
        return None


def _promote_writeback_rows(
    *,
    project_path: Path,
    rows: list[dict[str, object]],
    iteration: int,
) -> dict[str, object]:
    promoted_files: list[str] = []
    for row in rows:
        rel = str(row.get('assigned_file') or '')
        worktree_path = str(row.get('worktree_path') or '')
        if not rel or not worktree_path:
            row['promoted_to_main'] = False
            continue
        src = Path(worktree_path) / rel
        dst = project_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        promoted_files.append(rel)
        row['promoted_to_main'] = True

    commit_sha = None
    if promoted_files:
        lane = rows[0].get('lane_id', 'lane') if rows else 'lane'
        message = f"multilane promote({lane}): iter-{iteration:03d} " + ', '.join(promoted_files)
        commit_sha = _git_commit_paths(project_path, promoted_files, message)
    for row in rows:
        row['promotion_commit'] = commit_sha
    return {'promoted_files': promoted_files, 'promotion_commit': commit_sha}


def _merge_and_promote_writeback(
    *,
    project_path: Path,
    state_dir: Path,
    groups: dict[str, list[dict[str, object]]],
    iteration: int,
    model: str,
    verbose_logs: bool,
) -> dict[str, object]:
    """Per-file merge across all successful lanes, then commit the result.

    For files with one successful lane, this collapses to the same
    behavior as ``_promote_writeback_rows`` (verbatim copy). For files
    with multiple successful lanes, the merge agent picks the best
    proof per declaration. Either way, every row in ``groups`` gets
    its ``promoted_to_main`` flag set so the results JSONL stays
    correct.
    """
    from archon.multilane.merge_agent import LaneCandidate, merge_file_versions

    promoted_files: list[str] = []
    merge_records: list[dict[str, object]] = []

    for rel, rows in sorted(groups.items()):
        candidates: list[LaneCandidate] = []
        for row in rows:
            worktree_path = str(row.get('worktree_path') or '')
            if not worktree_path:
                continue
            candidates.append(LaneCandidate(
                lane_id=str(row.get('lane_id') or 'unknown'),
                source_path=Path(worktree_path) / rel,
            ))
        if not candidates:
            for row in rows:
                row['promoted_to_main'] = False
            continue

        outcome = merge_file_versions(
            project_path=project_path,
            state_dir=state_dir,
            target_rel=rel,
            candidates=candidates,
            iteration=iteration,
            model=model,
            verbose_logs=verbose_logs,
        )
        promoted_files.append(rel)
        for row in rows:
            row['promoted_to_main'] = True
            row['merge_outcome'] = 'merged' if outcome.merged else 'copied'
            row['merge_chosen_lane'] = outcome.chosen_lane
        merge_records.append({
            'file': rel,
            'merged': outcome.merged,
            'chosen_lane': outcome.chosen_lane,
            'lane_count': len(candidates),
            'lanes': [c.lane_id for c in candidates],
        })

    commit_sha = None
    if promoted_files:
        message = (
            f"multilane merge: iter-{iteration:03d} "
            + ', '.join(promoted_files)
        )
        commit_sha = _git_commit_paths(project_path, promoted_files, message)
    for rows in groups.values():
        for row in rows:
            row.setdefault('promotion_commit', commit_sha)
            if 'promotion_commit' in row and row['promotion_commit'] is None:
                row['promotion_commit'] = commit_sha

    return {
        'promoted_files': promoted_files,
        'promotion_commit': commit_sha,
        'merges': merge_records,
    }


def _assignment_success(
    *,
    ok: bool,
    assigned_file: str,
    changed_files: list[str],
    escaped_files: list[str],
    summary_path: str | None,
    assigned_file_path: str | None = None,
) -> tuple[bool, str | None]:
    if not ok:
        return False, 'runner_failed'
    if escaped_files:
        return False, 'escaped_worktree'
    if summary_path is None:
        return False, 'missing_summary'
    if not changed_files:
        return False, 'no_file_change'
    changed = set(changed_files)
    if changed != {assigned_file}:
        return False, 'cross_file_change'
    if assigned_file_path is not None:
        try:
            text = Path(assigned_file_path).read_text(encoding='utf-8', errors='ignore')
        except OSError:
            return False, 'missing_assigned_file'
        if 'sorry' in text:
            return False, 'placeholder_remaining'
    return True, None


def _prover_env(
    snap_dir: Path | str,
    prover_jsonl: Path | str,
    project_path: Path | str,
    serial_mode: bool = False,
) -> dict[str, str]:
    """Return the env-var dict for prover runs without mutating os.environ.

    Used by the multi-lane runner, which feeds env overrides directly to
    the Claude agent rather than mutating the global environment (since
    multiple lanes run concurrently in threads).
    """
    env_vars = {
        "ARCHON_SNAPSHOT_DIR": str(snap_dir),
        "ARCHON_PROVER_JSONL": str(prover_jsonl),
        "ARCHON_PROJECT_PATH": str(project_path),
    }
    if serial_mode:
        env_vars["ARCHON_SERIAL_MODE"] = "true"
    return env_vars


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


# ── multilane runners ─────────────────────────────────────────────────


def _run_multilane_assignment(
    *,
    project_name: str,
    project_path: Path,
    state_dir: Path,
    stage: str,
    assignment,
    verbose_logs: bool,
    model: str,
) -> dict[str, object]:
    lane_path = Path(assignment.worktree_path)
    slug = _file_slug(assignment.assigned_file)
    snap_dir = Path(assignment.log_path).parent.parent / 'snapshots' / slug
    raw_log_path = str(Path(str(assignment.log_path) + '.jsonl'))
    target_file = lane_path / assignment.assigned_file
    if target_file.exists():
        _snapshot_baseline(target_file, snap_dir)

    prompt = build_assignment_prompt(
        project_name=project_name,
        lane_project_path=lane_path,
        state_dir=state_dir,
        stage=stage,
        assignment=assignment,
    )
    before_files = set(_git_diff_files(lane_path))
    ok = ClaudeAgent(model=model, role="prover").run(
        prompt,
        cwd=lane_path,
        log_base=Path(assignment.log_path),
        verbose_logs=verbose_logs,
        env_overrides=_prover_env(
            snap_dir=snap_dir,
            prover_jsonl=Path(raw_log_path),
            project_path=lane_path,
        ),
    )
    after_files = set(_git_diff_files(lane_path))
    lane_dirty_files = sorted(after_files - before_files)
    changed_files, escaped_files, attribution_source = _assignment_code_snapshot_files(
        Path(raw_log_path), lane_path, project_path,
    )

    summary_path = assignment.result_path if Path(assignment.result_path).exists() else None
    assigned_file_only = bool(changed_files) and set(changed_files) == {assignment.assigned_file}
    strict_success, failure_reason = _assignment_success(
        ok=ok,
        assigned_file=assignment.assigned_file,
        changed_files=changed_files,
        escaped_files=escaped_files,
        summary_path=summary_path,
        assigned_file_path=str(target_file),
    )
    return {
        'assignment_id': assignment.assignment_id,
        'lane_id': assignment.lane_id,
        'job_id': assignment.job_id,
        'assigned_file': assignment.assigned_file,
        'worktree_path': assignment.worktree_path,
        'success': strict_success,
        'failure_reason': failure_reason,
        'changed_files': changed_files,
        'escaped_files': escaped_files,
        'attribution_source': attribution_source,
        'lane_dirty_files': lane_dirty_files,
        'assigned_file_only': assigned_file_only,
        'verification_passed': ok,
        'summary_path': summary_path,
        'raw_log_path': raw_log_path,
        'promote_readiness': 'manual-only',
    }


def _autogen_lane_settings(
    state_dir: Path,
    config: MultiLaneConfig,
) -> tuple[MultiLaneConfig, list]:
    """Materialize per-lane Claude settings files for non-Anthropic providers.

    Two paths:

    - **Direct-API providers** (kimi, deepseek): the lane settings
      file points at the provider's own Anthropic-compatible endpoint
      with the user's auth token.
    - **Proxy-mediated providers** (openai, gemini): we spawn the
      bundled ``archon.proxy`` server on a free port, the lane points
      at ``http://127.0.0.1:<port>``, and the proxy translates each
      request to LiteLLM under the hood.

    Returns ``(config, cleanups)`` where each cleanup is a zero-arg
    callable the caller MUST run after the multilane round finishes
    (typically in a ``finally`` block) — they kill the spawned proxy
    subprocesses.

    Lanes whose API key is missing are disabled in-place with a warning
    so a single missing credential doesn't take down the whole round.
    """
    from archon.commands.tooling.env_loader import (
        PROXY_PROVIDERS,
        lane_proxy_settings,
        provider_env,
        proxy_spawn_env,
    )

    lanes_dir = state_dir / 'multilane' / 'lanes'
    lanes_dir.mkdir(parents=True, exist_ok=True)

    cleanups: list = []

    for lane in config.lanes:
        if lane.provider == 'anthropic':
            continue
        if lane.claude_settings_path:
            # Respect a lane that brought its own pre-baked settings file.
            continue

        if lane.provider in PROXY_PROVIDERS:
            spawn_env = proxy_spawn_env(lane.provider)
            if spawn_env is None:
                log.warn(
                    f"Lane '{lane.lane_id}': missing API key for proxy provider "
                    f"'{lane.provider}' — disabling this lane."
                )
                lane.enabled = False
                continue
            try:
                from archon.proxy import find_free_port, start_proxy, stop_proxy, wait_for_proxy_ready
            except ImportError as e:
                log.error(
                    f"Lane '{lane.lane_id}': provider '{lane.provider}' needs the "
                    f"bundled proxy. Install with `pip install archon[proxy]`. ({e})"
                )
                lane.enabled = False
                continue
            port = find_free_port()
            proxy_log = lanes_dir / f'{lane.lane_id}-proxy.log'
            log.step(f"  starting {lane.provider} proxy for lane {lane.lane_id} on port {port}")
            proc = start_proxy(port=port, env=spawn_env, log_path=proxy_log)
            cleanups.append(lambda p=proc: stop_proxy(p))
            if not wait_for_proxy_ready(port, timeout=20.0):
                log.warn(
                    f"Lane '{lane.lane_id}': proxy on port {port} did not become "
                    f"ready in 20s — disabling this lane (see {proxy_log})."
                )
                stop_proxy(proc)
                lane.enabled = False
                continue
            settings_dict = lane_proxy_settings(port=port)
        else:
            settings_dict = provider_env(lane.provider)
            if settings_dict is None:
                log.warn(
                    f"Lane '{lane.lane_id}': no credentials found for provider "
                    f"'{lane.provider}' in environment / .archon/.env — disabling this lane."
                )
                lane.enabled = False
                continue

        settings_file = lanes_dir / f'{lane.lane_id}-settings.json'
        settings_file.write_text(json.dumps({'env': settings_dict}, indent=2) + '\n', encoding='utf-8')
        lane.claude_settings_path = str(settings_file)

    return config, cleanups


def _run_multilane_execution(
    project_name: str,
    project_path: Path,
    state_dir: Path,
    progress_file: Path,
    stage: str,
    iteration: int,
    verbose_logs: bool,
    model: str,
    config: 'MultiLaneConfig | None' = None,
) -> dict[str, object] | None:
    # If the caller passed a pre-built config (the new path: typed off
    # .archon/config.json's ``multilane`` section) use it. Otherwise
    # fall back to colleague's file-based discovery
    # (.archon/multilane/config.{json,yaml,toml}) for backwards compat.
    if config is None:
        config_path = find_multilane_config(state_dir)
        if config_path is None:
            log.warn('Multi-lane execution requested but no config provided and no .archon/multilane/config.* found')
            return None
        local_path = find_multilane_local_config(state_dir)
        config = load_multilane_config(config_path, local_path)

    _assert_multilane_clean_baseline(project_path)
    if not config.enabled:
        log.warn('Multi-lane config exists but is disabled')
        return None

    # For lanes whose provider needs external API credentials (kimi,
    # deepseek, …), auto-generate the {ANTHROPIC_BASE_URL, …}-shaped
    # settings file from the project .env. Proxy-mediated providers
    # (openai, gemini) get their bundled proxy spawned here too —
    # ``proxy_cleanups`` are run in the ``finally`` below so the
    # subprocesses go away even if execution raises.
    config, proxy_cleanups = _autogen_lane_settings(state_dir, config)

    summary, assignments = preview_round(
        config=config,
        progress_file=progress_file,
        project_path=project_path,
        state_dir=state_dir,
        iteration=iteration,
        stage=stage,
    )
    prepared = prepare_lanes_for_preview(config=config, project_path=project_path)
    runtime_info = write_preview_runtime_artifacts(
        state_dir=state_dir,
        iteration=iteration,
        assignments=assignments,
        prepared=prepared,
    )

    results: list[dict[str, object]] = []
    failures = 0
    restored_main_paths: set[str] = set()
    results_path = state_dir / 'multilane' / 'runtime' / f'iter-{iteration:03d}-results.jsonl'
    lock_path = _acquire_multilane_lock(state_dir)
    try:
        assignment_count = len(assignments)
        if assignment_count == 0:
            write_results_jsonl(results_path, results)
        else:
            log.info(f'Launching {assignment_count} multi-lane assignment(s) concurrently')
            with ThreadPoolExecutor(max_workers=assignment_count) as pool:
                futures = {
                    pool.submit(
                        _run_multilane_assignment,
                        project_name=project_name,
                        project_path=project_path,
                        state_dir=state_dir,
                        stage=stage,
                        assignment=assignment,
                        verbose_logs=verbose_logs,
                        model=model,
                    ): assignment
                    for assignment in assignments
                }
                for future in as_completed(futures):
                    assignment = futures[future]
                    try:
                        row = future.result()
                    except Exception as exc:
                        failures += 1
                        row = {
                            'assignment_id': assignment.assignment_id,
                            'lane_id': assignment.lane_id,
                            'job_id': assignment.job_id,
                            'assigned_file': assignment.assigned_file,
                            'worktree_path': assignment.worktree_path,
                            'success': False,
                            'failure_reason': 'exception',
                            'changed_files': [],
                            'assigned_file_only': False,
                            'verification_passed': False,
                            'summary_path': None,
                            'raw_log_path': str(Path(str(assignment.log_path) + '.jsonl')),
                            'promote_readiness': 'manual-only',
                            'error': str(exc),
                        }
                        log.warn(
                            f"  Lane {assignment.lane_id} :: {assignment.assigned_file} -> exception: {exc}"
                        )
                    else:
                        escaped_files = [str(path) for path in row.get('escaped_files', [])]
                        if escaped_files:
                            _restore_repo_paths(project_path, escaped_files)
                            restored_main_paths.update(escaped_files)
                        if not row['success']:
                            failures += 1
                        status = 'ok' if row['success'] else f"error ({row.get('failure_reason')})"
                        log.info(
                            f"  Lane {assignment.lane_id} :: {assignment.assigned_file} -> {status}"
                        )
                    results.append(row)
                    write_results_jsonl(results_path, results)
    finally:
        _release_multilane_lock(lock_path)
        for cleanup in proxy_cleanups:
            try:
                cleanup()
            except Exception as e:
                log.warn(f"proxy cleanup failed: {e}")

    contamination = _non_archon_dirty_files(project_path)
    if contamination:
        failures += 1
        log.warn('Main project tree was modified during multi-lane execution; marking run contaminated.')
        for path in contamination:
            log.warn(f'  contaminated: {path}')

    promotion_info: dict[str, object] = {
        'promoted_files': [],
        'promotion_commit': None,
        'preferred_lane_id': None,
        'merges': [],
    }
    if not contamination:
        # Preferred-lane id is still recorded for the report, but the
        # merge agent now considers every successful lane per file.
        promotion_info['preferred_lane_id'] = _preferred_writeback_lane(config)
        groups = _group_writeback_candidates_by_file(results)
        if groups:
            promotion_info.update(
                _merge_and_promote_writeback(
                    project_path=project_path,
                    state_dir=state_dir,
                    groups=groups,
                    iteration=iteration,
                    model=model,
                    verbose_logs=verbose_logs,
                )
            )

    results.sort(key=lambda row: str(row.get('assignment_id', '')))
    write_results_jsonl(results_path, results)

    report_path = state_dir / 'multilane' / 'reports' / f'iter-{iteration:03d}-execution.md'
    lines = [
        '# Multi-lane execution summary',
        '',
        f"- lanes: {summary.get('lane_count', 0)}",
        f"- jobs: {summary.get('job_count', 0)}",
        f"- assignments: {summary.get('assignment_count', 0)}",
        f"- failures: {failures}",
    ]
    if promotion_info.get('promoted_files'):
        lines.extend(['', '## Promoted to main', *[f'- {path}' for path in promotion_info['promoted_files']]])
        if promotion_info.get('promotion_commit'):
            lines.append(f"- commit: {promotion_info['promotion_commit']}")
    merges = promotion_info.get('merges') or []
    if merges:
        lines.extend(['', '## Per-file merges'])
        for entry in merges:
            shape = 'merged' if entry.get('merged') else f"copied from lane={entry.get('chosen_lane')}"
            lanes = ', '.join(entry.get('lanes') or [])
            lines.append(f"- {entry['file']}: {shape} (lanes: {lanes})")
    if restored_main_paths:
        lines.extend([
            '', '## Escaped main-checkout edits reverted to HEAD',
            *[f'- {path}' for path in sorted(restored_main_paths)],
        ])
    if contamination:
        lines.extend(['', '## Main-tree contamination', *[f'- {path}' for path in contamination]])
    lines.extend(['', '## Results'])
    for row in results:
        lines.append(
            f"- {row['lane_id']} :: {row['job_id']} :: success={row['success']} "
            f"reason={row.get('failure_reason')} changed={row['changed_files']}"
        )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

    log.panel(
        f"Executed multi-lane prover round.\n"
        f"Lanes: {summary.get('lane_count', 0)}\n"
        f"Jobs: {summary.get('job_count', 0)}\n"
        f"Assignments: {summary.get('assignment_count', 0)}\n"
        f"Failures: {failures}\n"
        f"Report: {report_path}",
        title='Multi-lane execution',
        style='green' if failures == 0 else 'yellow',
    )
    return {
        'summary': summary,
        'prepared': prepared,
        'assignments': assignments,
        'results': results,
        'results_path': str(results_path),
        'report_path': str(report_path),
        'promoted_files': promotion_info.get('promoted_files', []),
        'promotion_commit': promotion_info.get('promotion_commit'),
        **runtime_info,
    }


def _run_multilane_preview(
    project_path: Path,
    state_dir: Path,
    progress_file: Path,
    stage: str,
    iteration: int,
) -> dict[str, object] | None:
    config_path = find_multilane_config(state_dir)
    if config_path is None:
        log.warn('Multi-lane preview requested but no .archon/multilane/config.* was found')
        return None

    local_path = find_multilane_local_config(state_dir)
    config = load_multilane_config(config_path, local_path)
    if not config.enabled:
        log.warn('Multi-lane config exists but is disabled')
        return None

    summary, assignments = preview_round(
        config=config,
        progress_file=progress_file,
        project_path=project_path,
        state_dir=state_dir,
        iteration=iteration,
        stage=stage,
    )
    prepared = prepare_lanes_for_preview(config=config, project_path=project_path)
    runtime_info = write_preview_runtime_artifacts(
        state_dir=state_dir,
        iteration=iteration,
        assignments=assignments,
        prepared=prepared,
    )

    report_path = state_dir / 'multilane' / 'reports' / f'iter-{iteration:03d}-preview.md'
    write_preview_report(report_path=report_path, summary=summary, prepared=prepared)
    results = execute_assignments_preview_only(assignments)
    results_path = state_dir / 'multilane' / 'runtime' / f'iter-{iteration:03d}-results.jsonl'
    write_results_jsonl(results_path, results)

    log.panel(
        f"Preview only — no prover launched.\n"
        f"Lanes: {summary.get('lane_count', 0)}\n"
        f"Jobs: {summary.get('job_count', 0)}\n"
        f"Assignments: {summary.get('assignment_count', 0)}\n"
        f"Report: {report_path}",
        title='Multi-lane preview',
        style='cyan',
    )
    return {
        'summary': summary,
        'prepared': prepared,
        'assignments': assignments,
        'results_path': str(results_path),
        'report_path': str(report_path),
        **runtime_info,
    }


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
    max_iterations: Optional[int] = typer.Option(
        None, "--max-iterations", "-m",
        help="Max plan→prover→review cycles. (default from .archon/config.json or 10)",
    ),
    max_parallel: Optional[int] = typer.Option(
        None, "--max-parallel",
        help="Max concurrent provers in parallel mode. (default from config or 4)",
    ),
    stage: Optional[Stage] = typer.Option(
        None, "--stage", "-s",
        help="Force a stage instead of reading from PROGRESS.md.",
    ),
    parallel: Optional[bool] = typer.Option(
        None, "--parallel/--serial",
        help="Run provers in parallel (one per file) or serially. (default from config or parallel)",
    ),
    verbose_logs: Optional[bool] = typer.Option(
        None, "--verbose-logs/--no-verbose-logs",
        help="Save raw Claude stream events to .raw.jsonl. (default from config or off)",
    ),
    no_review: Optional[bool] = typer.Option(
        None, "--no-review/--review",
        help="Skip review phase after each iteration. (default from config or off)",
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
    model: Optional[str] = typer.Option(
        None, "--model", "-M",
        help="Claude model alias (e.g. 'opus', 'sonnet') or full id used for "
             "every plan / refactor / prover / review phase in the loop. "
             "(default from .archon/config.json or 'opus')",
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

    # ── resolve CLI options against .archon/config.json ──────────────
    # Precedence: CLI > config.json > built-in defaults. CLI options
    # default to None as a sentinel for "user didn't set this", so we
    # can distinguish an explicit --no-review from an unset --no-review.
    from archon.commands.tooling.project_config import load_project_config, resolve as _resolve
    from archon.commands.tooling.env_loader import load_env_file as _load_env

    project_config = load_project_config(resolved)
    loop_cfg = project_config.loop_section()
    multilane_cfg = project_config.multilane_section()

    # Project-local .env wins over global cwd .env (which cli.py loaded).
    _load_env(resolved)

    max_iterations = _resolve(max_iterations, section=loop_cfg, key='max_iterations', default=10)
    max_parallel   = _resolve(max_parallel,   section=loop_cfg, key='max_parallel',   default=4)
    parallel       = _resolve(parallel,       section=loop_cfg, key='parallel',       default=True)
    verbose_logs   = _resolve(verbose_logs,   section=loop_cfg, key='verbose_logs',   default=False)
    no_review      = _resolve(no_review,      section=loop_cfg, key='no_review',      default=False)
    model          = _resolve(model,          section=loop_cfg, key='model',          default=DEFAULT_MODEL)

    # Multi-lane execution fires automatically when config.json has it
    # enabled with at least one lane defined. The old --multilane-execute
    # / --multilane-preview flags are gone — the user controls this
    # purely through .archon/config.json.
    multilane_lanes = multilane_cfg.get('lanes') or []
    multilane_execute = bool(multilane_cfg.get('enabled')) and len(multilane_lanes) >= 1
    multilane_preview = False  # legacy variable; kept False so existing dispatch falls through

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
        "Multi-lane": (
            f"enabled ({len(multilane_lanes)} lane{'s' if len(multilane_lanes) != 1 else ''}: "
            f"{', '.join(str(l.get('lane_id', l.get('provider', '?'))) for l in multilane_lanes)})"
            if multilane_execute else "disabled"
        ),
    }
    if dry_run:
        config["Mode"] = "[yellow]DRY RUN[/yellow]"

    if multilane_execute and not no_review:
        log.warn(
            "Multi-lane execution MVP currently works best with --no-review; "
            "review will be skipped for this run."
        )
        no_review = True

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
        plan_prompt = build_plan_prompt(
            project_name, resolved, state_dir, current_stage,
            ignore_multilane=multilane_preview or multilane_execute,
        )

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

        if multilane_preview:
            preview_info = _run_multilane_preview(
                project_path=resolved,
                state_dir=state_dir,
                progress_file=progress_file,
                stage=current_stage,
                iteration=iter_num_local,
            )
            if not dry_run and preview_info is not None:
                write_meta(
                    iter_meta,
                    **{
                        "prover.mode": "multilane-preview",
                        "prover.multilanePreview": True,
                        "prover.multilaneReport": preview_info["report_path"],
                        "prover.multilaneAssignments": preview_info["summary"].get("assignment_count", 0),
                        "prover.multilaneAssignmentsJsonl": preview_info["assignments_path"],
                        "prover.multilanePreparedJsonl": preview_info["prepared_path"],
                        "prover.multilaneResultsJsonl": preview_info["results_path"],
                    },
                )
        elif multilane_execute:
            # Build the lane config from the project-level config.json
            # multilane section. This is the new path; the older
            # .archon/multilane/config.* file is no longer required.
            lane_config = multilane_config_from_simple(multilane_cfg)
            execution_info = _run_multilane_execution(
                project_name=project_name,
                project_path=resolved,
                state_dir=state_dir,
                progress_file=progress_file,
                stage=current_stage,
                iteration=iter_num_local,
                verbose_logs=verbose_logs,
                model=model,
                config=lane_config,
            )
            if not dry_run and execution_info is not None:
                write_meta(
                    iter_meta,
                    **{
                        "prover.mode": "multilane-execute",
                        "prover.multilaneExecute": True,
                        "prover.multilaneReport": execution_info["report_path"],
                        "prover.multilaneAssignments": execution_info["summary"].get("assignment_count", 0),
                        "prover.multilaneAssignmentsJsonl": execution_info["assignments_path"],
                        "prover.multilanePreparedJsonl": execution_info["prepared_path"],
                        "prover.multilaneResultsJsonl": execution_info["results_path"],
                        "prover.promotedFiles": execution_info.get("promoted_files", []),
                        "prover.promotionCommit": execution_info.get("promotion_commit"),
                    },
                )
        elif parallel:
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



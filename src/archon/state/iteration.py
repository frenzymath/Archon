"""Iteration / session directory helpers + meta.json writer."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_iter_num(log_dir: Path) -> int:
    """Return the next iteration number (1-based)."""
    max_n = 0
    if log_dir.exists():
        for d in log_dir.iterdir():
            if d.is_dir() and d.name.startswith("iter-"):
                try:
                    n = int(d.name.split("iter-")[1])
                    max_n = max(max_n, n)
                except ValueError:
                    pass
    return max_n + 1


def next_session_num(state_dir: Path) -> int:
    """Deprecated. Session numbers now match the iteration number; the
    review phase passes ``ctx.iter_num`` directly. Kept as a thin shim
    for legacy callers and tests — returns max+1 over existing dirs.
    """
    journal_dir = state_dir / "proof-journal" / "sessions"
    max_n = 0
    if journal_dir.exists():
        for d in journal_dir.iterdir():
            if d.is_dir() and d.name.startswith("session_"):
                try:
                    n = int(d.name.split("session_")[1])
                    max_n = max(max_n, n)
                except ValueError:
                    pass
    return max_n + 1


@dataclass
class SessionRename:
    """Planned session-directory rename.

    ``old`` and ``new`` are the bare ``session_NNN`` basenames; the
    migration code resolves them against ``proof-journal/sessions/``.
    ``delta_secs`` is the time gap between the session's content
    mtime and the matching ``archon[NNN/review]`` inner-git commit
    — load-bearing for the audit log.
    """
    old: str
    new: str
    iter_num: int
    delta_secs: int


def plan_session_renames(
    state_dir: Path, project_path: Path,
) -> tuple[list[SessionRename], list[str]]:
    """Match each non-empty ``session_*/`` to the closest review commit.

    Heuristic: the review phase commits the inner git immediately
    after the agent finishes writing summary/milestones/recommendations,
    so the session's content-mtime is within minutes of the
    ``archon[NNN/review]`` commit. We pick, per session, the review
    commit whose timestamp is closest to that mtime.

    Returns ``(planned_renames, orphans)`` where ``orphans`` is the
    list of session dirs whose mtime didn't fall within an hour of
    any review commit (probably from review runs whose commit was
    later reset away — the migration leaves these untouched).

    Caller is responsible for executing the renames; this function
    is pure and re-runnable.
    """
    sessions_dir = state_dir / "proof-journal" / "sessions"
    git_dir = state_dir / "git-dir"
    if not sessions_dir.is_dir() or not git_dir.is_dir():
        return [], []

    # Map iter_num → review commit timestamp (latest if multilane
    # produced two commits per iter; the actual commit content
    # already covers both).
    iter_time: dict[int, datetime] = {}
    try:
        out = subprocess.check_output(
            ["git", f"--git-dir={git_dir}", f"--work-tree={project_path}",
             "log", "--all", "--format=%aI|%s"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return [], []
    for line in out.splitlines():
        ai, _, subj = line.partition("|")
        m = re.match(r"archon\[(\d+)/review\]", subj)
        if not m:
            continue
        iter_num = int(m.group(1))
        try:
            t = datetime.fromisoformat(ai.strip())
        except ValueError:
            continue
        if iter_num not in iter_time or t > iter_time[iter_num]:
            iter_time[iter_num] = t

    if not iter_time:
        return [], []

    # Walk session dirs in numeric order; mtime = latest non-empty
    # file inside (writes happen in a tight cluster).
    sessions: list[tuple[str, datetime]] = []
    for d in sessions_dir.iterdir():
        if not d.is_dir() or not d.name.startswith("session_"):
            continue
        try:
            n = int(d.name.split("session_")[1])
        except ValueError:
            continue
        files = [
            f for f in d.iterdir()
            if f.is_file() and f.stat().st_size > 0
        ]
        if not files:
            continue
        mtime = max(f.stat().st_mtime for f in files)
        sessions.append((
            d.name,
            datetime.fromtimestamp(mtime, tz=timezone.utc),
        ))
    sessions.sort(key=lambda x: int(x[0].split("_")[1]))

    # Match each session to the nearest review commit.
    HOUR = 3600
    planned: list[SessionRename] = []
    orphans: list[str] = []
    claimed: dict[int, tuple[str, int]] = {}  # iter -> (session, delta)
    for name, mt in sessions:
        best_iter, best_delta = None, None
        for iter_num, ct in iter_time.items():
            d_secs = abs((mt - ct).total_seconds())
            if best_delta is None or d_secs < best_delta:
                best_iter, best_delta = iter_num, d_secs
        if best_iter is None or best_delta is None or best_delta > HOUR:
            orphans.append(name)
            continue
        # If two sessions claim the same iter, the closer match wins;
        # the loser becomes an orphan (manual decision required).
        prior = claimed.get(best_iter)
        if prior is None or best_delta < prior[1]:
            if prior is not None:
                orphans.append(prior[0])
            claimed[best_iter] = (name, int(best_delta))
        else:
            orphans.append(name)

    for iter_num, (name, delta) in sorted(claimed.items()):
        new = f"session_{iter_num}"
        if new == name:
            continue  # already correctly named
        planned.append(SessionRename(
            old=name, new=new, iter_num=iter_num, delta_secs=delta,
        ))
    return planned, sorted(orphans)


def apply_session_renames(
    state_dir: Path, planned: list[SessionRename],
) -> list[SessionRename]:
    """Execute a rename plan.

    Two-pass move (``session_X`` → ``.session_X.tmp`` → ``session_<iter>``)
    so a target name that's already taken by a soon-to-be-renamed dir
    doesn't cause a clobber.

    Returns the subset of renames that actually completed (each as
    the original ``SessionRename``); failures are silently dropped
    so callers can re-run.
    """
    sessions_dir = state_dir / "proof-journal" / "sessions"
    if not sessions_dir.is_dir():
        return []

    completed: list[SessionRename] = []

    # Pass 1: move each old name to a unique temp name keyed by
    # iter_num so the second pass can rename predictably.
    moved: list[tuple[Path, Path, SessionRename]] = []
    for r in planned:
        src = sessions_dir / r.old
        if not src.is_dir():
            continue
        tmp = sessions_dir / f".__rename__{r.iter_num}.tmp"
        if tmp.exists():
            continue  # something earlier failed; skip this one
        try:
            src.rename(tmp)
            moved.append((tmp, sessions_dir / r.new, r))
        except OSError:
            pass

    # Pass 2: temp → final.
    for tmp, dst, r in moved:
        if dst.exists():
            # A non-planned dir is already at the target. Try to
            # restore the original to avoid leaving the project in a
            # half-renamed state.
            try:
                tmp.rename(sessions_dir / r.old)
            except OSError:
                pass
            continue
        try:
            tmp.rename(dst)
            completed.append(r)
        except OSError:
            try:
                tmp.rename(sessions_dir / r.old)
            except OSError:
                pass

    return completed


def cleanup_empty_sessions(state_dir: Path) -> int:
    """Remove ``proof-journal/sessions/session_*`` directories that
    contain no review output.

    "Empty" means the directory has no files at all, OR every file in
    it is zero bytes. Conservative on purpose: anything with real
    content (the canonical milestones.jsonl / summary.md /
    recommendations.md, OR anything else the user might have left) is
    untouched.

    These accumulate when a review phase is interrupted before the
    agent writes any output — most often after a ``git reset --hard``
    that walks back past iterations whose review dirs already existed.
    Returns the count of directories removed.
    """
    journal_dir = state_dir / "proof-journal" / "sessions"
    if not journal_dir.is_dir():
        return 0
    removed = 0
    for d in journal_dir.iterdir():
        if not d.is_dir() or not d.name.startswith("session_"):
            continue
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        if entries and any(
            e.is_file() and e.stat().st_size > 0 for e in entries
        ):
            continue  # has at least one non-empty file → keep
        # Empty dir, or dir with only zero-byte placeholders. Wipe it.
        try:
            for e in entries:
                if e.is_file():
                    e.unlink()
            d.rmdir()
            removed += 1
        except OSError:
            pass  # best-effort
    return removed


def write_meta(meta_file: Path, **kwargs: object) -> None:
    """Write/update key-value pairs in an iteration meta.json.

    Supports dotted keys like ``provers.file_slug.status=running``.
    """
    data: dict = {}
    if meta_file.exists():
        try:
            data = json.loads(meta_file.read_text())
        except Exception:
            pass

    for key, value in kwargs.items():
        keys = key.split(".")
        d = data
        for part in keys[:-1]:
            if part not in d or not isinstance(d[part], dict):
                d[part] = {}
            d = d[part]
        d[keys[-1]] = value

    meta_file.write_text(json.dumps(data, indent=2))


def read_meta(meta_file: Path, key: str) -> object | None:
    """Look up a dotted key in an iteration meta.json. Returns None if
    the file is missing, unreadable, or the key path doesn't resolve to
    a value. Used by --resume to fetch stored session ids.
    """
    if not meta_file.exists():
        return None
    try:
        data: object = json.loads(meta_file.read_text())
    except Exception:
        return None
    for part in key.split("."):
        if not isinstance(data, dict):
            return None
        if part not in data:
            return None
        data = data[part]
    return data


def extract_session_id(jsonl_path: Path) -> str | None:
    """Return the most recent ``session_end.session_id`` in a phase JSONL.

    Walks the file from the end so a phase that crashed-and-restarted
    (two session_end events) yields the latest session — that's the one
    the next ``--resume`` invocation should continue.
    """
    if not jsonl_path.exists():
        return None
    try:
        text = jsonl_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("event") == "session_end":
            sid = obj.get("session_id") or ""
            if sid:
                return sid
    return None


def archive_task_results(state_dir: Path, dest_dir: Path) -> None:
    """Move existing task_results/*.md to an archive directory.

    If `dest_dir` is an iteration directory (name matches `iter-*`),
    archives land in `{dest_dir}/task_results-archive/` (single subdir
    per iteration). Otherwise — for backwards compatibility with code
    that passes `logs/` — archives land in
    `{dest_dir}/task_results-TIMESTAMP/`.
    """
    results_dir = state_dir / "task_results"
    if not results_dir.exists():
        return
    md_files = list(results_dir.glob("*.md"))
    if not md_files:
        return

    if dest_dir.name.startswith("iter-"):
        archive = dest_dir / "task_results-archive"
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archive = dest_dir / f"task_results-{stamp}"

    archive.mkdir(parents=True, exist_ok=True)

    # When archiving into an iteration directory, a later archive call
    # within the same iteration would clobber earlier files. Guard by
    # renaming if a file with the same name already exists.
    for f in md_files:
        target = archive / f.name
        if target.exists():
            stamp = datetime.now().strftime("%H%M%S")
            target = archive / f"{f.stem}.{stamp}{f.suffix}"
        f.rename(target)

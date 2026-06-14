"""Deterministic project duplication — the sandbox `archon extract` works in.

The extract/merge session NEVER touches the original project(s): the first
step duplicates the parent into ``--dest`` and everything after happens
there. Duplication is duplicate-then-carve (default = keep): the session
deletes what the carve plan says is out of cone, so a missed dependency
shows up as a loud build/graph failure in the sandbox — never as silent
loss, and never as damage to the parent.

What is duplicated:

- All project sources (``.lean``, ``blueprint/``, ``references/``, lakefile,
  toolchain, manifest, ...) — verbatim, so the sandbox compiles with the
  identical Mathlib pin.
- ``.lake`` — by default hardlink-cloned (``cp -al``): instant and ~0 extra
  disk on the same filesystem. Lake replaces build products rather than
  editing them in place, so hardlink sharing is safe; a plain symlink is NOT
  (two Lake instances writing one build dir corrupt both projects).
- ``.archon`` — selectively: the knowledge files the session adapts
  (PROGRESS/STRATEGY/TO_USER/ARCHON_MEMORY/...), prompts, subagents and
  config carry over; execution state (iter logs, task queues, task_results,
  proof-journal, inner git history) does not. The sandbox gets a FRESH inner
  git with a single baseline commit — clean history by construction, and
  free undo for the carve session.

An ``extract-manifest.json`` is written into the sandbox's ``.archon/``
recording the parent's path + inner/outer git heads and content hashes of
every duplicated ``.lean``/``.tex`` source — the provenance that later makes
``archon merge``-back a three-way merge instead of archaeology.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from archon import log
from archon.commands.tooling.inner_git import InnerGit
from archon.state import utcnow_iso

MANIFEST_NAME = "extract-manifest.json"

# Top-level entries never copied verbatim (handled specially or rebuilt).
_ROOT_SKIP = {".git", ".lake", ".leandag", ".archon"}

# .archon entries that carry over: knowledge to adapt + configuration.
_STATE_KEEP_FILES = (
    "AGENTS.md",
    "config.json",
    "VERSION",
    "ARCHON_MEMORY.md",
    "PROGRESS.md",
    "STRATEGY.md",
    "PROJECT_STATUS.md",
    "TO_USER.md",
    "USER_HINTS.md",
    "MULTILANE.md",
)
_STATE_KEEP_DIRS = ("prompts", "subagents", "prover-modes", "analogies")
# Everything else under .archon (git-dir, iter/, logs/, task queues,
# task_results/, proof-journal/, multilane/, lanes/, DAG_STATUS.md, sync
# state, ...) is execution state of the PARENT's run and stays behind.

# File suffixes whose content hashes go into the manifest (merge-back
# provenance). Hashing .lake or references blobs would be pointless bulk.
_HASH_SUFFIXES = {".lean", ".tex", ".toml", ".json"}
_HASH_NAMES = {"lean-toolchain", "lakefile.lean"}


@dataclass
class DuplicateReport:
    dest: Path
    files_copied: int = 0
    lake_mode: str = "hardlink"
    lake_linked: bool = False
    manifest_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


def _sha256_short(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _outer_head(project: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:
        return None


def _copy_tree(src: Path, dst: Path) -> int:
    """Copy ``src`` dir/file into ``dst``; returns number of files copied."""
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return 1
    count = 0
    for p in src.rglob("*"):
        if p.is_symlink():
            continue  # state symlinks (e.g. log links) must not escape the sandbox
        if p.is_file():
            rel = p.relative_to(src)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            count += 1
    return count


def _link_lake(parent: Path, dest: Path, mode: str, report: DuplicateReport) -> None:
    """Materialize ``dest/.lake`` per ``mode``: hardlink | copy | none."""
    src = parent / ".lake"
    if mode == "none" or not src.is_dir():
        if mode != "none":
            report.warnings.append(
                "parent has no .lake/ — run `lake exe cache get` in the sandbox"
            )
        return
    if mode == "copy":
        shutil.copytree(src, dest / ".lake", symlinks=True)
        report.lake_linked = True
        return
    # hardlink (default): cp -al = full directory tree, files shared by inode.
    r = subprocess.run(
        ["cp", "-al", str(src), str(dest / ".lake")],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        report.lake_linked = True
    else:
        report.warnings.append(
            f"hardlink clone of .lake failed ({r.stderr.strip() or r.returncode}); "
            f"run `lake exe cache get` in the sandbox, or retry with --lake copy"
        )


def _manifest_files(dest: Path) -> dict[str, str]:
    """Hash the carried-over source files (relative path → sha256[:16])."""
    hashes: dict[str, str] = {}
    for p in dest.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(dest)
        if rel.parts and rel.parts[0] in (".lake", ".archon", ".git", ".leandag"):
            continue
        if p.suffix in _HASH_SUFFIXES or p.name in _HASH_NAMES:
            hashes[rel.as_posix()] = _sha256_short(p)
    return hashes


def duplicate_project(
    parent: Path,
    dest: Path,
    *,
    lake_mode: str = "hardlink",
    mode: str = "extract",
    merge_source: Path | None = None,
    union: bool = False,
    prefer: str = "source",
) -> DuplicateReport:
    """Duplicate ``parent`` into ``dest`` (which must not exist yet).

    Pure file mechanics — no agent involved. Raises on a non-empty ``dest``
    or a missing parent; degrades with warnings on .lake/link issues.
    """
    parent = parent.resolve()
    dest = dest.resolve()
    if not (parent / ".archon").is_dir():
        raise FileNotFoundError(f"{parent} is not an archon project (no .archon/)")
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"destination {dest} exists and is not empty")
    if parent in dest.parents or dest == parent:
        raise ValueError("destination must not be inside the parent project")

    report = DuplicateReport(dest=dest, lake_mode=lake_mode)
    dest.mkdir(parents=True, exist_ok=True)

    # ── 1. Project sources, verbatim ───────────────────────────────────
    for entry in sorted(parent.iterdir()):
        if entry.name in _ROOT_SKIP:
            continue
        report.files_copied += _copy_tree(entry, dest / entry.name)

    # ── 2. .lake (shared toolchain/build products) ─────────────────────
    _link_lake(parent, dest, lake_mode, report)

    # ── 3. .archon — knowledge and config only ─────────────────────────
    state_src = parent / ".archon"
    state_dst = dest / ".archon"
    state_dst.mkdir(parents=True, exist_ok=True)
    for name in _STATE_KEEP_FILES:
        if (state_src / name).is_file():
            report.files_copied += _copy_tree(state_src / name, state_dst / name)
    for name in _STATE_KEEP_DIRS:
        if (state_src / name).is_dir():
            report.files_copied += _copy_tree(state_src / name, state_dst / name)

    # ── 4. Fresh inner git — clean history + free undo for the carve ───
    if InnerGit.available():
        inner = InnerGit(dest)
        inner.init()
        inner.ensure_initial_commit(
            f"archon[extract]: sandbox duplicated from {parent.name}"
        )
    else:
        report.warnings.append("`git` not on PATH — sandbox has no inner git (no undo)")

    # ── 5. Provenance manifest ─────────────────────────────────────────
    manifest = {
        "version": 1,
        "mode": mode,
        "created_at": utcnow_iso(),
        "parent": str(parent),
        "parent_inner_head": (
            InnerGit(parent).head_sha(short=False) if InnerGit.available() else None
        ),
        "parent_outer_head": _outer_head(parent),
        "merge_source": str(merge_source) if merge_source else None,
        "union": union,       # merge: full union vs enrich-target-only
        "prefer": prefer,     # merge: which side's proof wins an overlap
        "lake_mode": lake_mode,
        "seeds": [],        # filled by the session once scope is agreed
        "closure": [],      # filled by the session from the carve plan
        "overlaps": [],     # merge: filled by the session — per-decl winner
        "files": _manifest_files(dest),
    }
    report.manifest_path = state_dst / MANIFEST_NAME
    report.manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    for w in report.warnings:
        log.warn(w)
    return report

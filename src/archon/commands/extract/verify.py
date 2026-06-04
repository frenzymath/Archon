"""Deterministic verify gate for an extract/merge sandbox.

Runs after the interactive carve session ends. The session is agentic; the
gate is not — it re-derives everything from the sandbox on disk:

1. The blueprint DAG parses (`leandag` build via the shared helper).
2. Zero broken ``\\uses{}`` among the remaining nodes.
3. Every seed recorded in the manifest is present.
4. Every closure label recorded in the manifest is still present — the
   "lost no dependency" check: carving may only remove material OUTSIDE
   the agreed cone.
5. Optionally, ``lake build`` compiles the carved sources.

A failed gate leaves the sandbox untouched (the inner git holds the
session's commits, so fixing up means re-entering with --resume), and the
command reports exactly which check failed.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from archon.commands.dag.leandag_gaps import compute_gaps

from .duplicate import MANIFEST_NAME


@dataclass
class VerifyResult:
    ok: bool = True
    problems: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.problems.append(msg)


def read_manifest(dest: Path) -> dict | None:
    path = dest / ".archon" / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def verify_sandbox(dest: Path, *, build: bool = False,
                   build_timeout: int = 3600) -> VerifyResult:
    """Run the deterministic gate over the carved sandbox at ``dest``."""
    res = VerifyResult()
    manifest = read_manifest(dest)
    if manifest is None:
        res.fail(f".archon/{MANIFEST_NAME} missing or unreadable")
        return res

    seeds: list[str] = manifest.get("seeds") or []
    closure: list[str] = manifest.get("closure") or []
    if not seeds:
        res.fail(
            "manifest records no seeds — the session never wrote the agreed "
            "scope into the manifest (re-enter with --resume and finish Phase A)"
        )

    report = compute_gaps(dest)
    if report.error:
        res.fail(f"DAG does not build: {report.error}")
        return res

    res.stats = {
        "blueprint_decls": report.total_blueprint_decls,
        "lean_decls": report.total_lean_decls,
        "broken_uses": len(report.broken_uses),
        "isolated_blueprint": len(report.isolated_blueprint),
    }

    if report.broken_uses:
        shown = ", ".join(f"{a}→{b}" for a, b in report.broken_uses[:10])
        res.fail(
            f"{len(report.broken_uses)} broken \\uses{{}} after the carve "
            f"(the carve cut INTO the cone): {shown}"
        )

    # Presence of seeds + full agreed closure: the lost-no-dependency check.
    # compute_gaps has no id list, so re-derive from the graph itself.
    try:
        from archon.commands.dag.leandag_gaps import _build_dag
        dag, _e, _l, _b = _build_dag(dest)
        present = {n.id for n in dag.nodes}
    except Exception as e:
        res.fail(f"could not re-derive node set: {e}")
        return res

    missing_seeds = [s for s in seeds if s not in present]
    if missing_seeds:
        res.fail(f"seed(s) missing from sandbox graph: {', '.join(missing_seeds)}")
    missing_closure = [c for c in closure if c not in present]
    if missing_closure:
        shown = ", ".join(missing_closure[:10])
        more = f", … and {len(missing_closure) - 10} more" if len(missing_closure) > 10 else ""
        res.fail(
            f"{len(missing_closure)} closure node(s) lost in the carve: {shown}{more}"
        )

    if build:
        try:
            r = subprocess.run(
                ["lake", "build"], cwd=dest,
                capture_output=True, text=True, timeout=build_timeout,
            )
            if r.returncode != 0:
                tail = "\n".join((r.stdout + r.stderr).splitlines()[-15:])
                res.fail(f"`lake build` failed:\n{tail}")
        except FileNotFoundError:
            res.fail("`lake` not on PATH — cannot run the build check")
        except subprocess.TimeoutExpired:
            res.fail(f"`lake build` exceeded {build_timeout}s")

    return res

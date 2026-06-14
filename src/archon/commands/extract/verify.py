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

from archon.commands.dag.leandag_gaps import build_graph_at_commit, compute_gaps

from .duplicate import MANIFEST_NAME


@dataclass
class VerifyResult:
    ok: bool = True
    problems: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.problems.append(msg)


def _name_index(nodes, *, is_dict: bool) -> dict:
    """Map each Lean declaration name → its node (parent dicts or child objs).

    Keys on every component of a (possibly ``", "``-joined) ``\\lean{}`` name so
    a node can be matched across the blueprint→``lean_aux`` degradation that
    dropping a chapter causes (the id changes ``thm:foo`` → ``lean:Mod.foo`` but
    the Lean name is stable).
    """
    idx: dict = {}
    for n in nodes:
        ln = n.get("lean_name") if is_dict else getattr(n, "lean_name", None)
        if not ln:
            continue
        for part in (p.strip() for p in ln.split(",")):
            if part:
                idx.setdefault(part, n)
    return idx


def _parent_regressions(dest: Path, manifest: dict, child_nodes) -> tuple[list[str], bool]:
    """Flag nodes that were healthy in the parent but degraded in the sandbox.

    Re-derives the parent DAG at the commit the manifest recorded and compares,
    by Lean name, every node that survived into the sandbox. A node that was a
    real, finite-effort blueprint node upstream but now ① degraded to a bare
    ``lean_aux`` (its chapter was dropped while its sorried Lean stayed), ②
    regressed to ∞ effort, or ③ gained a sorry, is a quality regression the
    closure check cannot see. Names listed in the manifest ``riders`` array are
    intentional and skipped. Best-effort: returns ``([], False)`` (no parent
    available) rather than raising. The second value reports whether a real
    comparison ran.

    Only the agreed scope (seeds + their recorded blueprint closure) is judged.
    Out-of-scope dependency declarations that a carve keeps as compile-time
    Lean while dropping their chapters are EXPECTED to degrade to ``lean_aux``
    and are not flagged — judging them flooded the gate on every subproject
    extract. In-scope chapter loss is still caught by the closure-presence
    check (the node's id flips ``thm:`` -> ``lean:``).
    """
    parent = manifest.get("parent")
    sha = manifest.get("parent_inner_head")
    if not parent or not sha:
        return [], False
    data = build_graph_at_commit(Path(parent), sha)
    if data.get("error"):
        return [], False

    riders = set(manifest.get("riders") or [])
    parent_nodes = data.get("nodes", [])

    # The gate judges only the AGREED SCOPE — the seeds and their recorded
    # blueprint closure. Carving a focused subproject intentionally keeps
    # out-of-scope dependency Lean (with its sorries) so the subproject still
    # compiles, while dropping those decls' blueprint chapters; such a node
    # degrading to a bare lean_aux is the EXPECTED outcome of an extract, NOT a
    # regression. Without this restriction every kept Lean-import rider trips
    # the gate — the recurring false-positive flood on subproject extracts.
    # In-scope chapter loss is still caught: the node's id flips thm:->lean: so
    # the closure-presence check fails on it independently. Scope ids are
    # resolved to Lean names through the PARENT graph (the manifest records
    # ids). When no scope was recorded we judge nothing here (the seed/closure
    # presence checks already gate that) rather than flag every upstream node.
    in_scope_ids = set(manifest.get("seeds") or []) | set(manifest.get("closure") or [])
    in_scope_names: set[str] = set()
    for pn in parent_nodes:
        if pn.get("id") not in in_scope_ids:
            continue
        ln = pn.get("lean_name")
        if not ln:
            continue
        for part in (p.strip() for p in ln.split(",")):
            if part:
                in_scope_names.add(part)

    p_idx = _name_index(parent_nodes, is_dict=True)
    c_idx = _name_index(child_nodes, is_dict=False)

    problems: list[str] = []
    for name, pn in p_idx.items():
        if name in riders:
            continue
        # Out-of-scope dependency kept only as compile-time Lean — dropping its
        # chapter (and so its blueprint status) is by design, not a regression.
        if name not in in_scope_names:
            continue
        # Only judge nodes that were healthy upstream: a real blueprint node
        # (not lean_aux) carrying a finite local effort.
        if pn.get("type") == "lean_aux" or pn.get("effort_local") is None:
            continue
        cn = c_idx.get(name)
        if cn is None:
            continue  # gone entirely — the closure check governs deletions
        c_type = getattr(cn, "type", None)
        c_eff = getattr(cn, "effort_local", None)
        c_sorry = getattr(cn, "has_sorry", False)
        if c_type == "lean_aux":
            problems.append(f"{name}: blueprint node lost its chapter "
                            f"(degraded to a bare lean_aux — kept the sorried "
                            f"Lean, dropped the LaTeX)")
        elif c_eff is None:
            problems.append(f"{name}: effort regressed to ∞ "
                            f"(its informal proof / closure was cut)")
        elif c_sorry and not pn.get("has_sorry"):
            problems.append(f"{name}: gained a sorry it did not have upstream")
    return problems, True


def read_manifest(dest: Path) -> dict | None:
    path = dest / ".archon" / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def verify_sandbox(dest: Path, *, build: bool = False, mode: str = "extract",
                   build_timeout: int = 3600) -> VerifyResult:
    """Run the deterministic gate over the sandbox at ``dest``.

    Shared by ``extract`` (carve) and ``merge`` (import). The checks are the
    same — DAG rebuilds, zero broken ``\\uses{}``, every recorded seed and
    closure node present, optional ``lake build`` — only the phrasing of a
    failure differs by ``mode``.
    """
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
        cause = ("imported material left \\uses{} edges unresolved"
                 if mode == "merge" else "the carve cut INTO the cone")
        res.fail(
            f"{len(report.broken_uses)} broken \\uses{{}} ({cause}): {shown}"
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
        how = "missing after import" if mode == "merge" else "lost in the carve"
        res.fail(
            f"{len(missing_closure)} closure node(s) {how}: {shown}{more}"
        )

    # Quality regression vs the parent: a node the closure check still finds
    # "present" can nonetheless have degraded (chapter dropped → sorried
    # lean_aux → ∞ effort). Best-effort — needs the parent's inner git.
    try:
        regressions, compared = _parent_regressions(dest, manifest, dag.nodes)
    except Exception:
        regressions, compared = [], False
    res.stats["parent_compared"] = compared
    res.stats["regressions"] = len(regressions)
    if regressions:
        shown = "; ".join(regressions[:8])
        more = f"; … and {len(regressions) - 8} more" if len(regressions) > 8 else ""
        res.fail(
            "quality regression(s) vs parent — a kept node degraded "
            "(orphaned / ∞-effort / newly sorried). If intentional, whitelist "
            f"its Lean name in the manifest 'riders' array: {shown}{more}"
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

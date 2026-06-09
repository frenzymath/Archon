"""Blueprint-coverage gaps computed from the leandag DAG model.

One source of truth shared by:

* the DAG prompt inject (``_leandag_block`` in :mod:`archon.prompts`),
  which gives the elaboration agent an upfront picture of what the
  blueprint is missing; and
* the ``archon dag-gaps`` CLI subcommand, which the installed
  ``.claude/tools/archon-leandag.py`` wrapper calls so the dag agent
  and its blueprint-writers can re-query mid-session after editing
  chapters ("are there still broken deps / uncovered Lean decls?").

The single most useful signal for the blueprint phase is **coverage**:
Lean declarations that have no blueprint entry. leandag already models
these as ``lean_aux`` nodes (Lean decls not referenced by any blueprint
``\\lean{}``), so we surface them directly.

Everything is defensive: any failure (leandag missing, unparseable
blueprint, scan error) is captured into ``GapReport.error`` and the
callers degrade to a no-op rather than breaking the dag prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Mirror leandag's own entry auto-detection order.
_ENTRY_CANDIDATES = (
    "blueprint/src/web.tex",
    "blueprint/src/print.tex",
    "blueprint/src/content.tex",
)

# Cap list sizes so the injected prompt block stays bounded on large projects.
_MAX_LIST = 50


@dataclass
class GapReport:
    """Coverage/dependency gaps in the blueprint DAG."""

    has_blueprint: bool = False
    entry: str | None = None
    total_lean_decls: int = 0
    total_blueprint_decls: int = 0
    # Lean decls with no blueprint entry (leandag ``lean_aux`` nodes).
    uncovered: list[str] = field(default_factory=list)
    # (node_id, missing_label) — a ``\\uses{}`` pointing at an unknown label.
    broken_uses: list[tuple[str, str]] = field(default_factory=list)
    # Blueprint nodes with no edges in or out — their dependencies were never
    # transcribed into ``\\uses{}`` (or nothing was wired to use them). These
    # keep the graph disconnected from the goal cone.
    isolated_blueprint: list[str] = field(default_factory=list)
    # Blueprint nodes not yet marked ``\\leanok``.
    unproved: list[str] = field(default_factory=list)
    # Unproved nodes whose every direct dependency is already proved.
    ready: list[str] = field(default_factory=list)
    # Blueprint nodes with effort = ∞ at the source (no informal proof AND no
    # sorry-free Lean) — the roadmap gaps to close, ordered root-first.
    infinity_sources: list[str] = field(default_factory=list)
    # How many blueprint declarations have an ∞ total effort (some ancestor is
    # an infinity source) — i.e. their proof closure is incomplete.
    infinity_total: int = 0
    # Blueprint nodes already proved sorry-free in Lean but with an empty
    # informal proof body — should carry a short "proved directly in Lean" note.
    lean_only: list[str] = field(default_factory=list)
    error: str | None = None


def _detect_entry(project_path: Path) -> Path | None:
    for rel in _ENTRY_CANDIDATES:
        p = project_path / rel
        if p.exists():
            return p
    return None


def _build_dag(project_path: Path):
    """Build the leandag DAG for ``project_path``.

    Returns ``(dag, entry, lean_count, blueprint_count)``. Raises on any
    leandag/parse failure; callers decide how to degrade.
    """
    from leandag import DAG, BlueprintParser, LeanScanner

    lean_decls = LeanScanner().scan(project_path)
    entry = _detect_entry(project_path)
    macros: dict = {}
    if entry is not None:
        parser = BlueprintParser(entry)
        bp_decls, proofs = parser.parse()
        # Blueprint \newcommand/\def macros — fed to KaTeX so the DAG page
        # renders the project's notation (and baked into graph.html).
        macros = getattr(parser, "macros", {}) or {}
    else:
        bp_decls, proofs = [], {}
    dag = DAG.from_sources(bp_decls, proofs, lean_decls, macros=macros)
    return dag, entry, len(lean_decls), len(bp_decls)


def _serialize_graph(dag, entry: Path | None, lean_n: int, bp_n: int,
                     project_path: Path) -> dict:
    """Turn a built leandag DAG into the dashboard's JSON-able graph dict.

    Shape mirrors leandag's own ``JSONExporter`` plus the archon-specific
    ``meta`` the DAG page reads: ``{nodes, edges, meta, error}``.
    """
    # Dedupe nodes by id. A duplicate id means the blueprint declares the same
    # \label{} twice — an authoring bug that crashes graph renderers (vis-network
    # DataSets reject duplicate ids → blank canvas). Keep the first occurrence,
    # report the rest under meta.duplicate_ids so the UI can flag it.
    seen: set[str] = set()
    uniq_nodes: list[dict] = []
    duplicate_ids: list[str] = []
    for n in dag.nodes:
        nd = n.to_dict()
        nid = nd.get("id")
        if nid in seen:
            duplicate_ids.append(nid)
            continue
        seen.add(nid)
        uniq_nodes.append(nd)

    return {
        "nodes": uniq_nodes,
        "edges": [{"from": e.source, "to": e.target} for e in dag.edges],
        "meta": {
            "num_nodes": len(uniq_nodes),
            "num_edges": len(dag.edges),
            "axioms": [n.id for n in dag.axioms],
            "leaves": [n.id for n in dag.leaves],
            "entry": (str(entry.relative_to(project_path)) if entry else None),
            "has_blueprint": entry is not None,
            "total_lean_decls": lean_n,
            "total_blueprint_decls": bp_n,
            "duplicate_ids": sorted(set(duplicate_ids)),
            "macros": getattr(dag, "macros", {}) or {},
        },
        "error": None,
    }


def _write_json_cache(project_path: Path, data: dict) -> None:
    """Best-effort write of the graph dict to ``.leandag/dag.json``."""
    try:
        import json
        cache_dir = project_path / ".leandag"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "dag.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # serving fresh data is what matters; cache is best-effort


def build_graph(project_path: Path, *, write_cache: bool = True) -> dict:
    """Build the full DAG as a JSON-able dict for the dashboard DAG page.

    Shape matches leandag's own ``JSONExporter`` (``.leandag/dag.json``):
    ``{nodes: [node.to_dict()], edges: [{from, to}], meta: {...}}`` plus an
    ``error`` key (``None`` on success). When ``write_cache`` is set the
    result is also written to ``.leandag/dag.json`` so it interoperates
    with leandag's own tooling (``leandag html`` etc.).
    """
    try:
        dag, entry, lean_n, bp_n = _build_dag(project_path)
    except Exception as e:
        return {"nodes": [], "edges": [], "meta": {}, "error": str(e)}

    data = _serialize_graph(dag, entry, lean_n, bp_n, project_path)
    if write_cache:
        _write_json_cache(project_path, data)
    return data


def build_artifacts(project_path: Path) -> tuple[bool, str | None]:
    """Build the DAG once and write both ``.leandag/dag.json`` and ``graph.html``.

    This is the deterministic-loop artifact step: it refreshes the cached
    graph the dashboard reads (``dag.json``) and regenerates leandag's own
    interactive ``graph.html`` (the exact rendering leandag ships), using
    leandag's ``HTMLExporter`` so the two stay in lockstep.

    Returns ``(ok, error)`` — ``ok`` is ``False`` with a message when the
    DAG could not be built (e.g. leandag missing, unparseable blueprint).
    """
    try:
        from leandag.exporters import HTMLExporter
    except Exception as e:  # leandag not importable
        return False, f"leandag not available: {e}"

    try:
        dag, entry, lean_n, bp_n = _build_dag(project_path)
    except Exception as e:
        return False, f"DAG build failed: {e}"

    cache_dir = project_path / ".leandag"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # dag.json (archon-shaped, for the dashboard's /api/dag fallback cache).
    _write_json_cache(project_path, _serialize_graph(dag, entry, lean_n, bp_n, project_path))

    # graph.html (leandag's own interactive navigator — identical rendering).
    try:
        HTMLExporter().export(dag, cache_dir / "graph.html")
    except Exception as e:
        return False, f"HTML export failed: {e}"

    return True, None


def build_graph_at_commit(
    project_path: Path, sha: str, git_dir: Path | None = None
) -> dict:
    """Build the DAG as it was at an inner-git commit, **without caching**.

    Archon snapshots the project into an inner git repo at
    ``.archon/git-dir`` (work-tree = the project). To render the blueprint
    DAG at a historical commit we materialize that commit's tree into a
    throwaway temp dir and run leandag there, then discard it.

    Crucially this **never writes ``.leandag/dag.json`` or ``graph.html``**:
    the live loop reads those, and overwriting them with a historical graph
    would feed it contradictory state. The result is returned in-memory only,
    with ``meta.commit`` set so the UI knows which commit it is showing.

    Returns the same ``{nodes, edges, meta, error}`` shape as
    :func:`build_graph`; ``error`` is set on any failure.
    """
    import io
    import shutil
    import subprocess
    import tarfile
    import tempfile

    git_dir = git_dir or (project_path / ".archon" / "git-dir")
    if not git_dir.exists():
        return {"nodes": [], "edges": [], "meta": {}, "error": f"no inner git at {git_dir}"}

    try:
        archived = subprocess.run(
            ["git", f"--git-dir={git_dir}", "archive", "--format=tar", sha],
            capture_output=True, timeout=30,
        )
    except Exception as e:
        return {"nodes": [], "edges": [], "meta": {}, "error": f"git archive failed: {e}"}
    if archived.returncode != 0:
        msg = (archived.stderr or b"").decode("utf-8", "replace").strip()
        return {"nodes": [], "edges": [], "meta": {}, "error": f"git archive {sha}: {msg}"}

    tmp = Path(tempfile.mkdtemp(prefix="leandag-commit-"))
    try:
        # leandag only needs the .lean sources and blueprint/*.tex. The inner
        # git also tracks archon state under .archon/ — and the prover logs
        # there are symlinks to absolute paths, which both bloat the extract
        # and trip tar's safety filter ("symlink to an absolute path"). So
        # extract only regular files/dirs that live outside the state dirs,
        # skipping symlinks/hardlinks/devices entirely.
        excluded = {".archon", ".lake", ".git", "node_modules", "lake-packages"}
        base = tmp.resolve()
        with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as tf:
            members = []
            for m in tf.getmembers():
                if not (m.isfile() or m.isdir()):
                    continue  # drop symlinks/hardlinks/devices
                top = m.name.lstrip("./").split("/", 1)[0]
                if top in excluded:
                    continue
                dest = (tmp / m.name).resolve()
                if dest != base and base not in dest.parents:
                    continue  # path-traversal guard
                members.append(m)
            try:
                tf.extractall(tmp, members=members, filter="data")
            except TypeError:
                tf.extractall(tmp, members=members)  # older Pythons: no filter kwarg
        data = build_graph(tmp, write_cache=False)
        data.setdefault("meta", {})["commit"] = sha
        return data
    except Exception as e:
        return {"nodes": [], "edges": [], "meta": {}, "error": f"DAG build at {sha} failed: {e}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def compute_gaps(project_path: Path) -> GapReport:
    """Build the leandag DAG for ``project_path`` and report its gaps.

    Works before any blueprint exists: with no entry ``.tex`` the
    blueprint side is empty and every scanned Lean declaration shows up
    as ``uncovered`` — exactly the "nothing is blueprinted yet" picture
    the elaboration agent needs on iteration one.
    """
    try:
        from leandag import Queries
    except Exception as e:  # leandag not importable
        return GapReport(error=f"leandag not available: {e}")

    try:
        dag, entry, lean_n, bp_n = _build_dag(project_path)
        nodes = dag.nodes
        known = {n.id for n in nodes}

        uncovered = [n.id for n in nodes if n.type == "lean_aux"]
        broken: list[tuple[str, str]] = []
        for n in nodes:
            for dep in n.uses:
                if dep not in known:
                    broken.append((n.id, dep))
        isolated_bp = [n.id for n in dag.isolated if n.type != "lean_aux"]

        q = Queries(dag)
        unproved = [n.id for n in q.unproved() if not getattr(n, "mathlib_ok", False)]
        ready = [n.id for n in q.ready_to_prove() if not (n.proved or getattr(n, "mathlib_ok", False))]

        # ── Infinity analysis (blueprint nodes only; lean_aux is the separate
        # "uncovered" track). effort_local is None ⇔ no sorry-free Lean AND no
        # informal proof — the genuine roadmap holes. Order sources root-first
        # by ancestor count so the agent fixes the ones closest to the axioms.
        bp_nodes = [n for n in nodes if n.type != "lean_aux"]
        inf_src = [n for n in bp_nodes if getattr(n, "effort_local", None) is None]
        inf_src.sort(key=lambda n: len(dag.ancestors(n.id)))
        infinity_sources = [n.id for n in inf_src]
        infinity_total = sum(
            1 for n in bp_nodes if getattr(n, "effort_total", None) is None
        )
        lean_only = [
            n.id for n in bp_nodes
            if getattr(n, "proof_size_lean", None) is not None
            and not (getattr(n, "proof_tex", "") or "").strip()
        ]

        return GapReport(
            has_blueprint=entry is not None,
            entry=(str(entry.relative_to(project_path)) if entry else None),
            total_lean_decls=lean_n,
            total_blueprint_decls=bp_n,
            uncovered=uncovered,
            broken_uses=broken,
            isolated_blueprint=isolated_bp,
            unproved=unproved,
            ready=ready,
            infinity_sources=infinity_sources,
            infinity_total=infinity_total,
            lean_only=lean_only,
        )
    except Exception as e:
        return GapReport(error=f"leandag gap analysis failed: {e}")


def _trunc(items: list, n: int = _MAX_LIST) -> tuple[list, int]:
    """Return (first n items, count dropped)."""
    if len(items) <= n:
        return items, 0
    return items[:n], len(items) - n


# ── Read-only navigation queries (back ``archon dag-query``) ─────────────────
# A focused query surface over the leandag graph so the plan/review agents and
# the graph subagents (walker / auditor / effort-breaker) can ask specific
# questions — the frontier, the ∞ holes, a node's dependency closure — without
# dumping the whole graph (dag-graph) or re-parsing the blueprint.

# Compact per-node fields surfaced in a query result: enough to navigate and
# decide, without the full statement/proof bodies.
_QUERY_NODE_FIELDS = (
    "id", "type", "title", "chapter", "lean_name",
    "proved", "mathlib_ok", "has_sorry",
    "dep_count", "rdep_count", "descendant_count",
    "effort_local", "effort_total",
)

# verb → one-line description (also the source of the valid-verb list).
QUERY_VERBS: dict[str, str] = {
    "frontier": "ready to prove — unproved, every \\uses dep done",
    "leaves": "nothing depends on them (rdep_count 0)",
    "roots": "depend on nothing (dep_count 0)",
    "isolated": "no edges at all — possibly dead",
    "unproved": "blueprint nodes without \\leanok or \\mathlibok",
    "sorry": "Lean proof contains sorry/admit",
    "gaps": "∞ effort — statement with no informal proof (roadmap holes)",
    "needs-leanok": "sorry-free in Lean but not marked \\leanok",
    "needs-lean": "blueprint node with no \\lean{} link",
    "unmatched": "lean_aux — Lean decls with NO blueprint entry (1-to-1 coverage debt)",
    "ancestors": "the dependency closure of --node (everything it transitively uses)",
    "cone": "closure of --node seed(s) (comma-separated; seeds included); --complement inverts",
    "interface": "direct \\uses deps of --node seed(s) (depth-1; natural sub-seeds / cut points)",
    "overlap": "shared closure of two seed sets: cone(--node) ∩ cone(--vs) (e.g. vs a sibling extract)",
    "node": "a single node by --node id",
    "all": "every node",
}

_SORT_KEYS = {"effort", "deps", "impact"}


def _node_brief(n) -> dict:
    return {k: getattr(n, k, None) for k in _QUERY_NODE_FIELDS}


def run_query(
    project_path: Path,
    verb: str,
    *,
    node: str | None = None,
    vs: str | None = None,
    limit: int | None = 50,
    sort: str | None = None,
    complement: bool = False,
) -> dict:
    """Run a read-only navigation query over the leandag graph.

    Returns ``{verb, count, total, nodes: [brief...], error}`` where ``total``
    is the pre-limit match count and ``nodes`` is the (optionally sorted and)
    limited briefs. ``ancestors``/``node``/``cone``/``interface``/``overlap``
    require ``node`` (a comma-separated seed list for the closure verbs);
    ``cone`` with ``complement`` returns every node NOT in the closure;
    ``overlap`` also requires ``vs`` (a second seed list) and returns
    ``cone(node) ∩ cone(vs)``. Never raises — failures land in ``error``.
    """
    if verb not in QUERY_VERBS:
        return {"verb": verb, "count": 0, "total": 0, "nodes": [],
                "error": f"unknown verb {verb!r}; valid: {', '.join(QUERY_VERBS)}"}
    if verb in ("ancestors", "node", "cone", "interface", "overlap") and not node:
        return {"verb": verb, "count": 0, "total": 0, "nodes": [],
                "error": f"verb {verb!r} requires --node <id>"}
    if verb == "overlap" and not vs:
        return {"verb": verb, "count": 0, "total": 0, "nodes": [],
                "error": "verb 'overlap' requires --vs <id>[,<id>…]"}
    if vs and verb != "overlap":
        return {"verb": verb, "count": 0, "total": 0, "nodes": [],
                "error": "--vs is only valid with the `overlap` verb"}
    if complement and verb != "cone":
        return {"verb": verb, "count": 0, "total": 0, "nodes": [],
                "error": "--complement is only valid with the `cone` verb"}
    if sort and sort not in _SORT_KEYS:
        return {"verb": verb, "count": 0, "total": 0, "nodes": [],
                "error": f"unknown --sort {sort!r}; valid: {', '.join(_SORT_KEYS)}"}

    try:
        from leandag import Queries
        dag, _entry, _ln, _bn = _build_dag(project_path)
    except Exception as e:
        return {"verb": verb, "count": 0, "total": 0, "nodes": [],
                "error": f"leandag unavailable: {e}"}

    try:
        q = Queries(dag)
        known = {n.id for n in dag.nodes}
        if verb in ("ancestors", "node") and node not in known:
            return {"verb": verb, "count": 0, "total": 0, "nodes": [],
                    "error": f"node {node!r} not found in the graph"}
        if verb in ("cone", "interface", "overlap"):
            seeds = [s.strip() for s in node.split(",") if s.strip()]
            missing = [s for s in seeds if s not in known]
            if missing:
                return {"verb": verb, "count": 0, "total": 0, "nodes": [],
                        "error": f"seed node(s) not found: {', '.join(missing)}"}
        if verb == "overlap":
            vs_seeds = [s.strip() for s in vs.split(",") if s.strip()]
            vs_missing = [s for s in vs_seeds if s not in known]
            if vs_missing:
                return {"verb": verb, "count": 0, "total": 0, "nodes": [],
                        "error": f"--vs node(s) not found: {', '.join(vs_missing)}"}

        if verb in ("frontier",):
            sel = [n for n in q.ready_to_prove() if not (n.proved or getattr(n, "mathlib_ok", False))]
        elif verb == "leaves":
            sel = dag.leaves
        elif verb == "roots":
            sel = dag.axioms
        elif verb == "isolated":
            sel = dag.isolated
        elif verb == "unproved":
            sel = [n for n in q.unproved() if not getattr(n, "mathlib_ok", False)]
        elif verb == "sorry":
            sel = q.with_sorry()
        elif verb == "gaps":
            sel = [n for n in dag.nodes if n.type != "lean_aux"
                   and getattr(n, "effort_local", None) is None]
        elif verb == "needs-leanok":
            sel = q.needs_leanok()
        elif verb == "needs-lean":
            sel = q.needs_lean_statement()
        elif verb == "unmatched":
            sel = [n for n in dag.nodes if n.type == "lean_aux"]
        elif verb == "node":
            sel = [dag.node(node)]
        elif verb == "ancestors":
            # The pure dependency closure (exclude the node itself).
            sel = [dag.node(i) for i in dag.ancestors(node) if i != node and i in known]
        elif verb == "cone":
            closure: set[str] = set(seeds)
            for s in seeds:
                closure.update(i for i in dag.ancestors(s) if i in known)
            if complement:
                sel = [n for n in dag.nodes if n.id not in closure]
            else:
                sel = [dag.node(i) for i in sorted(closure)]
        elif verb == "interface":
            # Depth-1 \uses predecessors of the seeds (the immediate
            # dependencies), excluding the seeds — natural sub-seeds / cut
            # points when splitting a project into work packages.
            direct: set[str] = set()
            for s in seeds:
                direct.update(getattr(dag.node(s), "uses", []) or [])
            direct -= set(seeds)
            sel = [dag.node(i) for i in sorted(direct) if i in known]
        elif verb == "overlap":
            def _closure(ss: list[str]) -> set[str]:
                c: set[str] = set(ss)
                for s in ss:
                    c.update(i for i in dag.ancestors(s) if i in known)
                return c
            shared = _closure(seeds) & _closure(vs_seeds)
            sel = [dag.node(i) for i in sorted(shared)]
        else:  # all
            sel = list(dag.nodes)

        if sort == "effort":
            sel = Queries.sort_by_effort(sel, exclude_proved=False)
        elif sort == "deps":
            sel = Queries.sort_by_deps(sel)
        elif sort == "impact":
            sel = Queries.sort_by_impact(sel)

        total = len(sel)
        if limit and limit > 0:
            sel = sel[:limit]
        # `truncated` is an explicit signal so callers never mistake a capped
        # result for the whole set. A truncated `cone`/`ancestors` once made a
        # carve session report the wrong sorry count (17 of a 24-node cone)
        # because the cap was silent; surface it loudly instead.
        return {"verb": verb, "count": len(sel), "total": total,
                "truncated": total > len(sel),
                "nodes": [_node_brief(n) for n in sel], "error": None}
    except Exception as e:
        return {"verb": verb, "count": 0, "total": 0, "nodes": [],
                "error": f"leandag query failed: {e}"}


def format_query_text(res: dict) -> str:
    """Compact human/agent-readable rendering of a ``run_query`` result."""
    if res.get("error"):
        return f"_dag-query error: {res['error']}_"
    verb = res["verb"]
    desc = QUERY_VERBS.get(verb, "")
    head = f"{verb} — {desc}  ({res['count']} of {res['total']})"
    lines = [head]
    for n in res["nodes"]:
        eff = n.get("effort_local")
        eff_s = "∞" if eff is None else str(eff)
        tags = []
        if n.get("proved"):
            tags.append("leanok")
        if n.get("mathlib_ok"):
            tags.append("mathlib")
        if n.get("has_sorry"):
            tags.append("sorry")
        lean = f" \\lean{{{n['lean_name']}}}" if n.get("lean_name") else ""
        tag_s = f" [{', '.join(tags)}]" if tags else ""
        lines.append(
            f"- {n['id']}{lean}  (effort {eff_s}, "
            f"deps {n.get('dep_count')}, used-by {n.get('rdep_count')}){tag_s}"
        )
    if res.get("truncated") or res["total"] > res["count"]:
        lines.append(
            f"⚠️  TRUNCATED: showing {res['count']} of {res['total']} — "
            f"{res['total'] - res['count']} hidden. Raise --limit (or use "
            f"--limit 0 / --json for the full set) before counting anything."
        )
    return "\n".join(lines)


# ── Carve plan (backs ``archon dag-carve-plan``) ─────────────────────────────
# The deterministic core of `archon extract`: given seed declarations, compute
# the dependency closure and roll it up per .lean / .tex file so the extract
# session executes a computed plan instead of freehanding deletions.

_IMPORT_RE = None  # compiled lazily in _lean_imports


def _lean_imports(text: str) -> list[str]:
    """Module names imported by a .lean file (header ``import`` lines only)."""
    global _IMPORT_RE
    if _IMPORT_RE is None:
        import re
        _IMPORT_RE = re.compile(r"^import\s+([\w.«»]+)", re.MULTILINE)
    return _IMPORT_RE.findall(text)


def _module_of(rel_path: str) -> str:
    """``Foo/Bar/Baz.lean`` → ``Foo.Bar.Baz`` (leandag stores posix-relative paths)."""
    return rel_path[:-len(".lean")].replace("/", ".") if rel_path.endswith(".lean") else rel_path


def _all_lean_files(project_path: Path) -> list[str]:
    """Every tracked ``.lean`` source under ``project_path`` (posix-relative).

    Includes files leandag never made a node for — re-export barrels, import
    shims, the root library file — which the blueprint-derived rollup is blind
    to. Skips build/vcs/state dirs (anything dotted, e.g. ``.lake`` ``.git``
    ``.archon``).
    """
    out: list[str] = []
    for p in project_path.rglob("*.lean"):
        rel = p.relative_to(project_path)
        if any(part.startswith(".") for part in rel.parts):
            continue
        out.append(rel.as_posix())
    return out


def _build_lean_ref_graph(nodes) -> dict[str, set[str]]:
    """Approximate Lean symbol dependencies: ``id → {ids it references}``.

    leandag stores no symbol-level edges (only blueprint ``\\uses{}``), but it
    keeps each decl's ``lean_source``. We name-match: for every node, scan its
    body for word-bounded mentions of any other decl's ``\\lean{}`` name — both
    the fully-qualified name and its final component, since references inside a
    ``namespace`` are usually unqualified. Shared short names over-approximate
    (a match maps to *all* decls with that name), which keeps more code — the
    build-safe direction. Good enough to (a) never carve a decl a kept proof
    calls and (b) flag obviously-dead riders; ``lake build`` is the backstop.
    """
    import re
    name_to_ids: dict[str, set[str]] = {}
    for n in nodes:
        ln = getattr(n, "lean_name", None)
        if not ln:
            continue
        for full in (p.strip() for p in ln.split(",")):
            if not full:
                continue
            name_to_ids.setdefault(full, set()).add(n.id)
            short = full.rsplit(".", 1)[-1]
            if short and short != full:
                name_to_ids.setdefault(short, set()).add(n.id)
    if not name_to_ids:
        return {}
    # Longest-first alternation so `MyLib.a1` wins over a bare `a1`; both map
    # to the same id set anyway, but it keeps the scan honest.
    names = sorted(name_to_ids, key=len, reverse=True)
    pat = re.compile(r"\b(?:" + "|".join(re.escape(x) for x in names) + r")\b")
    refs: dict[str, set[str]] = {}
    for n in nodes:
        src = getattr(n, "lean_source", "") or ""
        if not src:
            continue
        hit: set[str] = set()
        for m in pat.findall(src):
            hit.update(name_to_ids.get(m, ()))
        hit.discard(n.id)  # self-mention is not a dependency
        if hit:
            refs[n.id] = hit
    return refs


def _lean_reachable(seeds: set[str], refs: dict[str, set[str]]) -> set[str]:
    """Transitive closure of ``seeds`` over the Lean symbol-reference graph."""
    seen = set(seeds)
    stack = list(seeds)
    while stack:
        x = stack.pop()
        for y in refs.get(x, ()):
            if y not in seen:
                seen.add(y)
                stack.append(y)
    return seen


def compute_carve_plan(project_path: Path, seeds: list[str]) -> dict:
    """Compute the deterministic carve plan for extracting ``seeds``' cone.

    Returns::

        {seeds, closure, closure_size, complement_size,
         lean_closure_size,
         lean_files: [{file, status, total, in_cone, out_blueprint: [...],
                       rider_decls: [...], dead_riders: [...],
                       out_aux: N, imported_by: [...]}],
         tex_files:  [{file, status, total, in_cone, out_blueprint: [...]}],
         other_lean_files: [{file, status, imported_by: [...]}],
         error}

    File status semantics:

    - ``keep``     — every blueprint node in the file is in the closure
                     (out-of-cone ``lean_aux`` helpers ride along).
    - ``mixed``    — some blueprint nodes in, some out: the file needs surgery.
                     ``out_blueprint`` is the true carve list (block + Lean decl
                     both removed); ``rider_decls`` are out-of-cone blueprint
                     decls whose *Lean code* a kept proof still calls — drop
                     their blueprint block but KEEP the Lean declaration.
    - ``imported`` — no node in the closure, but a keep/mixed file transitively
                     ``import``s it, so deleting it breaks compilation. Lean
                     import edges are NOT ``\\uses{}`` edges — this catches
                     dependencies the blueprint graph cannot see.
    - ``drop``     — no node in the closure and nothing kept imports it.

    Declaration granularity (the ``rider_decls`` / ``dead_riders`` split) comes
    from a name-level Lean reference graph (:func:`_build_lean_ref_graph`): the
    file-level ``imported`` policy keeps a file *whole* even when only some of
    its decls are reachable from the kept cone. ``dead_riders`` are the unused
    ones — surgery candidates to trim, not whole-file drops (the file stays so
    the ``import`` still resolves).

    ``other_lean_files`` lists every on-disk ``.lean`` leandag made no node for
    (barrels, shims, the root library file) so the plan classifies *all* files,
    never leaving silent freehand judgment: ``imported`` (kept code imports it),
    ``aggregator`` (imports kept files but nothing kept imports it — likely the
    root/barrel; regenerate, don't blind-drop), ``drop`` (imports only dropped
    or external modules).

    Never raises — failures land in ``error``.
    """
    try:
        dag, _entry, _ln, _bn = _build_dag(project_path)
    except Exception as e:
        return {"seeds": seeds, "closure": [], "closure_size": 0,
                "complement_size": 0, "lean_files": [], "tex_files": [],
                "error": f"leandag unavailable: {e}"}

    try:
        known = {n.id for n in dag.nodes}
        missing = [s for s in seeds if s not in known]
        if missing:
            return {"seeds": seeds, "closure": [], "closure_size": 0,
                    "complement_size": 0, "lean_files": [], "tex_files": [],
                    "error": f"seed node(s) not found: {', '.join(missing)}"}

        closure: set[str] = set(seeds)
        for s in seeds:
            closure.update(i for i in dag.ancestors(s) if i in known)

        # ── Declaration-level Lean closure ──────────────────────────────
        # The blueprint closure tells us which *blueprint* decls to keep; the
        # Lean reference graph tells us which *Lean code* the kept decls call.
        # A file-level "keep this import whole" policy drags along decls no kept
        # proof uses (dead riders) and — worse — can mark a decl "carve" that a
        # kept proof actually calls. lean_closure separates the two.
        refs = _build_lean_ref_graph(dag.nodes)
        lean_closure = _lean_reachable(closure, refs)

        # ── Roll nodes up by source file ────────────────────────────────
        by_lean: dict[str, list] = {}
        by_tex: dict[str, list] = {}
        for n in dag.nodes:
            if n.lean_file:
                by_lean.setdefault(n.lean_file, []).append(n)
            if n.tex_file:
                by_tex.setdefault(n.tex_file, []).append(n)

        def _rollup(groups: dict[str, list], *, is_lean: bool) -> dict[str, dict]:
            out: dict[str, dict] = {}
            for f, ns in groups.items():
                in_cone = [n for n in ns if n.id in closure]
                out_bp_nodes = [n for n in ns
                                if n.id not in closure and n.type != "lean_aux"]
                out_aux = sum(1 for n in ns
                              if n.id not in closure and n.type == "lean_aux")
                if not in_cone:
                    status = "drop"
                elif not out_bp_nodes:
                    status = "keep"
                else:
                    status = "mixed"
                rec = {"file": f, "status": status, "total": len(ns),
                       "in_cone": len(in_cone), "out_aux": out_aux}
                if is_lean:
                    # Split out-of-cone blueprint decls: a `rider-decl` is one
                    # whose Lean code a kept proof calls (keep the code, drop
                    # only its blueprint block); the rest are true carves.
                    carve = [n.id for n in out_bp_nodes if n.id not in lean_closure]
                    rider = [n.id for n in out_bp_nodes if n.id in lean_closure]
                    # Dead riders: lean_aux helpers in this file no kept decl
                    # references — safe to trim out (surgery), file stays.
                    dead = [n.id for n in ns
                            if n.type == "lean_aux" and n.id not in lean_closure]
                    rec.update(out_blueprint=sorted(carve),
                               rider_decls=sorted(rider),
                               dead_riders=sorted(dead))
                else:
                    rec["out_blueprint"] = sorted(n.id for n in out_bp_nodes)
                out[f] = rec
            return out

        lean_files = _rollup(by_lean, is_lean=True)
        tex_files = _rollup(by_tex, is_lean=False)

        # ── Lean import closure over ALL on-disk files ──────────────────
        # \uses{} edges don't model Lean imports; a file a kept file imports
        # would break `lake build` if dropped. Walk imports from keep/mixed
        # files. The universe is every .lean on disk (not just node-bearing
        # ones) so the walk reaches no-node barrels/shims and stays transitive
        # through them.
        all_lean = set(_all_lean_files(project_path)) | set(by_lean)
        module_to_file = {_module_of(f): f for f in all_lean}
        _imports_cache: dict[str, list[str]] = {}

        def _in_project_imports(f: str) -> list[str]:
            if f not in _imports_cache:
                try:
                    text = (project_path / f).read_text(encoding="utf-8")
                except OSError:
                    text = ""
                _imports_cache[f] = [module_to_file[m] for m in _lean_imports(text)
                                     if m in module_to_file]
            return _imports_cache[f]

        importers: dict[str, list[str]] = {}
        frontier = [f for f, d in lean_files.items() if d["status"] in ("keep", "mixed")]
        reached: set[str] = set(frontier)
        while frontier:
            f = frontier.pop()
            for tgt in _in_project_imports(f):
                importers.setdefault(tgt, []).append(f)
                if tgt not in reached:
                    reached.add(tgt)
                    if tgt in lean_files and lean_files[tgt]["status"] == "drop":
                        lean_files[tgt]["status"] = "imported"
                    frontier.append(tgt)
        for f, d in lean_files.items():
            d["imported_by"] = sorted(set(importers.get(f, [])))

        # ── Files leandag made no node for (barrels, shims, root lib) ────
        kept_files = {f for f, d in lean_files.items()
                      if d["status"] in ("keep", "mixed", "imported")}
        other: list[dict] = []
        for f in sorted(all_lean - set(by_lean)):
            if f in reached:
                status = "imported"
            elif any(t in kept_files or t in reached
                     for t in _in_project_imports(f)):
                # Pulls in kept material but nothing kept pulls it in — the
                # root library file or a barrel. Don't blind-drop; regenerate.
                status = "aggregator"
            else:
                status = "drop"
            other.append({"file": f, "status": status,
                          "imported_by": sorted(set(importers.get(f, [])))})

        order = {"keep": 0, "mixed": 1, "imported": 2, "drop": 3}
        other_order = {"imported": 0, "aggregator": 1, "drop": 2}
        return {
            "seeds": seeds,
            "closure": sorted(closure),
            "closure_size": len(closure),
            "lean_closure_size": len(lean_closure),
            "complement_size": len(known) - len(closure),
            "lean_files": sorted(lean_files.values(),
                                 key=lambda d: (order[d["status"]], d["file"])),
            "tex_files": sorted(tex_files.values(),
                                key=lambda d: (order[d["status"]], d["file"])),
            "other_lean_files": sorted(
                other, key=lambda d: (other_order[d["status"]], d["file"])),
            "error": None,
        }
    except Exception as e:
        return {"seeds": seeds, "closure": [], "closure_size": 0,
                "complement_size": 0, "lean_files": [], "tex_files": [],
                "error": f"carve-plan failed: {e}"}


def format_carve_plan_text(plan: dict) -> str:
    """Compact human/agent-readable rendering of a carve plan."""
    if plan.get("error"):
        return f"_carve-plan error: {plan['error']}_"
    lc = plan.get("lean_closure_size")
    lc_s = f" · lean-needed: {lc} decl(s)" if lc is not None else ""
    lines = [
        f"carve plan — seeds: {', '.join(plan['seeds'])}",
        f"closure: {plan['closure_size']} node(s) kept{lc_s} · "
        f"complement: {plan['complement_size']} node(s) out of cone",
    ]
    for key, label in (("lean_files", "Lean files"), ("tex_files", "Blueprint chapters")):
        lines.append("")
        lines.append(f"## {label}")
        for d in plan[key]:
            extra = ""
            if d["status"] == "mixed":
                shown, dropped = _trunc(d["out_blueprint"], 10)
                extra = " — carve out: " + (", ".join(shown) if shown else "(none)")
                if dropped:
                    extra += f", … and {dropped} more"
                riders = d.get("rider_decls") or []
                if riders:
                    rshown, rmore = _trunc(riders, 6)
                    extra += (" · KEEP code, drop block (kept proof calls): "
                              + ", ".join(rshown)
                              + (f", … +{rmore}" if rmore else ""))
            elif d["status"] == "imported":
                extra = " — imported by: " + ", ".join(d.get("imported_by", [])[:5])
            dead = d.get("dead_riders") or []
            if dead:
                dshown, dmore = _trunc(dead, 6)
                extra += (" · dead riders (trim, unused): " + ", ".join(dshown)
                          + (f", … +{dmore}" if dmore else ""))
            lines.append(
                f"- [{d['status']:8s}] {d['file']}  "
                f"({d['in_cone']}/{d['total']} in cone){extra}"
            )
    others = plan.get("other_lean_files") or []
    if others:
        lines.append("")
        lines.append("## Other Lean files (no blueprint node)")
        for d in others:
            imp = d.get("imported_by") or []
            tail = (" — imported by: " + ", ".join(imp[:5])) if imp else ""
            lines.append(f"- [{d['status']:10s}] {d['file']}{tail}")
    return "\n".join(lines)


def format_markdown(report: GapReport) -> str:
    """Human/agent-readable summary for prompt injection or terminal."""
    if report.error:
        return f"_leandag gap analysis unavailable: {report.error}_"

    lines: list[str] = []
    if report.has_blueprint:
        lines.append(
            f"Blueprint entry `{report.entry}` — "
            f"{report.total_blueprint_decls} blueprint declaration(s), "
            f"{report.total_lean_decls} Lean declaration(s)."
        )
    else:
        lines.append(
            f"No blueprint entry found yet ({report.total_lean_decls} Lean "
            f"declaration(s) scanned). Every Lean decl below is uncovered."
        )

    def _section(title: str, items: list, render=lambda x: f"`{x}`") -> None:
        shown, dropped = _trunc(items)
        lines.append("")
        lines.append(f"**{title}** ({len(items)}):")
        if not items:
            lines.append("- none")
            return
        for it in shown:
            lines.append(f"- {render(it)}")
        if dropped:
            lines.append(f"- … and {dropped} more")

    if report.infinity_total:
        lines.append("")
        lines.append(
            f"**Roadmap blocked:** {report.infinity_total} blueprint "
            f"declaration(s) have effort = ∞ (their proof closure has a hole). "
            f"Eliminate every ∞ by writing the missing informal proofs, "
            f"starting from the sources below (closest to the root first)."
        )

    if report.isolated_blueprint:
        lines.append("")
        lines.append(
            f"**Graph disconnected:** {len(report.isolated_blueprint)} blueprint "
            f"declaration(s) have no `\\uses{{}}` edges in or out — their "
            f"dependencies were never transcribed, so they are not wired into "
            f"the goal's cone. Dispatch `dag-walker`s to transcribe the missing "
            f"edges (completeness criterion 8)."
        )

    _section("Uncovered Lean declarations (no blueprint entry)", report.uncovered)
    _section(
        "Broken \\uses{} references", report.broken_uses,
        render=lambda t: f"`{t[0]}` → `{t[1]}` (undefined label)",
    )
    _section(
        "Isolated blueprint declarations (no edges — transcribe their "
        "dependencies)", report.isolated_blueprint,
    )
    _section(
        "Infinity sources — write these informal proofs first (root-first)",
        report.infinity_sources,
    )
    _section(
        "Proved in Lean but no informal proof — add a brief \"proved directly "
        "in Lean\" note", report.lean_only,
    )
    _section("Unproved blueprint declarations (no \\leanok or \\mathlibok)", report.unproved)
    _section("Ready to prove (deps all proved)", report.ready)
    return "\n".join(lines)


def format_json(report: GapReport) -> str:
    import json

    return json.dumps(
        {
            "has_blueprint": report.has_blueprint,
            "entry": report.entry,
            "total_lean_decls": report.total_lean_decls,
            "total_blueprint_decls": report.total_blueprint_decls,
            "uncovered": report.uncovered,
            "broken_uses": [list(t) for t in report.broken_uses],
            "isolated_blueprint": report.isolated_blueprint,
            "infinity_sources": report.infinity_sources,
            "infinity_total": report.infinity_total,
            "lean_only": report.lean_only,
            "unproved": report.unproved,
            "ready": report.ready,
            "error": report.error,
        },
        indent=2,
    )

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

        q = Queries(dag)
        unproved = [n.id for n in q.unproved()]
        ready = [n.id for n in q.ready_to_prove()]

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

    _section("Uncovered Lean declarations (no blueprint entry)", report.uncovered)
    _section(
        "Broken \\uses{} references", report.broken_uses,
        render=lambda t: f"`{t[0]}` → `{t[1]}` (undefined label)",
    )
    _section(
        "Infinity sources — write these informal proofs first (root-first)",
        report.infinity_sources,
    )
    _section(
        "Proved in Lean but no informal proof — add a brief \"proved directly "
        "in Lean\" note", report.lean_only,
    )
    _section("Unproved blueprint declarations (no \\leanok)", report.unproved)
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
            "infinity_sources": report.infinity_sources,
            "infinity_total": report.infinity_total,
            "lean_only": report.lean_only,
            "unproved": report.unproved,
            "ready": report.ready,
            "error": report.error,
        },
        indent=2,
    )

"""Detect objectives that can't be worked on because their imports don't compile.

Christian Merten's reported failure mode (the same iter that fanned out 27
provers, addressed by the dispatch cap): some of those provers were assigned
to files that imported a *blocked* file — a file the previous ``lake build``
failed on. The downstream files can't even be loaded; a prover dispatch on
them burns API time for nothing.

This module catches the case deterministically:

1. **Blocked set**: parse the most recent ``lake build`` error log
   (``.archon/last_lake_build.log``) for ``error: <file>.lean:R:C:`` lines.
   These are the files Lean refused to elaborate. Filter to files that
   actually live under the project (Mathlib mirror paths, ``.lake`` deps,
   etc. are not the planner's problem).

2. **Local import graph**: scan every project ``.lean`` file for ``import``
   statements, splitting into local (resolves to a project module) and
   external (Mathlib / Std / ...). Build a ``{file: set(local-imports)}``
   adjacency map.

3. **Transitive walk**: for each candidate objective F, BFS through its
   local imports. If any imported file is in the blocked set, F can't be
   compiled either — drop it.

4. **"Presumed-being-fixed" exception**: a blocked file that's *itself*
   in the planner's objective list is presumed-being-fixed this iter,
   so its appearance in another file's import chain doesn't disqualify
   the downstream file. The planner is allowed to assign F1 and F2
   together when F2 is blocked-and-being-fixed and F1 depends on F2 —
   the prover phase handles them in topological order via the runner's
   dispatch + the file-level locking the loop already does.

The intent is identical to the dispatch cap: warn the planner loudly,
queue the deferred files into ``USER_HINTS.md`` so the next plan agent
re-prioritizes from a clean slate, and slice the dispatch list at the
runner as a deterministic safety net.
"""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path


# Same regex as ``lean-lsp-mcp/utils.py::BUILD_ERROR_FILE_PATTERN`` —
# replicated rather than imported because the lean-lsp-mcp tree is a
# vendored snapshot, not stable API. ``lake build`` errors look like:
#
#     error: src/Project/Algebra/Foo.lean:42:7: unknown identifier 'bar'
#     warning: src/Project/Algebra/Foo.lean:99:1: declaration uses 'sorry'
#
# We only treat ``error:`` lines as blocking — a ``warning:`` doesn't
# stop elaboration (the file's ``.olean`` still gets written).
_BUILD_ERROR_RE = re.compile(
    r"^error:\s*([^\s:]+\.lean):\d+:\d+:",
    re.MULTILINE | re.IGNORECASE,
)

# `import Foo.Bar.Baz` at start of a line. Lean accepts a few odd shapes
# (runtime module strings, ``import all``); in practice every project we
# care about uses dotted module names.
_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z_][A-Za-z0-9_.]*)\b")

# Directory parts pruned wholesale from the project's .lean scan — build
# output and archon state; third-party deps live under .lake / lake-packages.
_SKIP_PARTS = {".git", ".archon", ".lake", "lake-packages"}


def parse_blocked_files_from_log(
    log_path: Path,
    *,
    project_path: Path,
) -> set[Path]:
    """Read the lake-build error log and return project-relative blocked paths.

    Returns an empty set when:

    * the log doesn't exist (no failed build to read from — e.g. the
      previous iter's ``lake build`` succeeded, or finalize was
      skipped);
    * the log exists but has no parseable ``error:`` lines;
    * every error path the log mentions falls outside ``project_path``
      (the build failed on a Mathlib / dep file, not the project — the
      planner can't act on that).

    Each returned ``Path`` is a project-relative path (e.g.
    ``src/Project/Algebra/Foo.lean``), normalized so the caller can
    compare against entries built the same way.
    """
    if not log_path.is_file():
        return set()
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()

    blocked: set[Path] = set()
    proj_resolved = project_path.resolve()
    for raw in set(_BUILD_ERROR_RE.findall(text)):
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (project_path / candidate)
        try:
            resolved = candidate.resolve()
            rel = resolved.relative_to(proj_resolved)
        except (OSError, ValueError):
            # Either the path doesn't exist on disk anymore (file was
            # renamed/deleted between the build and now) or it's
            # outside the project tree (Mathlib error in a vendored
            # mirror). Either way, ignore — the planner can't act on it.
            continue
        # Skip lake/git artifacts even if they somehow ended up under
        # the project tree.
        if any(part in _SKIP_PARTS for part in rel.parts):
            continue
        blocked.add(rel)
    return blocked


def _scan_imports(path: Path) -> list[str]:
    """Parse ``import Foo.Bar`` lines from a single .lean file.

    Stops at the first non-import declaration (``def``, ``theorem``,
    etc.) — Lean requires imports at file top, and an unterminated
    block comment shouldn't be misread as code.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("--"):
            continue
        if not stripped.startswith("import"):
            # Comments and a handful of header-allowed prefixes get a
            # pass; anything else terminates the import block.
            if stripped.startswith("/-") or stripped.startswith("-/"):
                continue
            if stripped.startswith((
                "namespace", "section", "open", "set_option",
                "syntax", "macro",
            )):
                continue
            if any(stripped.startswith(kw) for kw in (
                "def ", "theorem ", "lemma ", "instance ", "example ",
                "structure ", "inductive ", "class ", "abbrev ",
                "noncomputable ", "partial ", "variable ",
                "#check", "#eval", "#print",
            )):
                break
            continue
        m = _IMPORT_RE.match(stripped)
        if m:
            out.append(m.group(1))
    return out


def _module_to_relpath(module: str) -> Path:
    """``Foo.Bar.Baz`` → ``Foo/Bar/Baz.lean`` as a relative Path."""
    return Path(*module.split(".")).with_suffix(".lean")


def build_local_import_graph(
    project_path: Path,
) -> dict[Path, set[Path]]:
    """Map every project .lean to the set of local .lean files it imports.

    External imports (Mathlib, Std, anything that doesn't resolve to a
    .lean file under ``project_path``) are dropped. The returned keys
    and values are project-relative ``Path`` objects, the same shape
    ``parse_blocked_files_from_log`` returns — so set operations
    between the two work directly.
    """
    proj_resolved = project_path.resolve()
    project_modules: dict[str, Path] = {}
    raw_imports: dict[Path, list[str]] = {}

    for lean in proj_resolved.rglob("*.lean"):
        try:
            rel = lean.relative_to(proj_resolved)
        except ValueError:
            continue
        if any(part in _SKIP_PARTS for part in rel.parts):
            continue
        module = ".".join(rel.with_suffix("").parts)
        project_modules[module] = rel
        raw_imports[rel] = _scan_imports(lean)

    graph: dict[Path, set[Path]] = {}
    for rel, imports in raw_imports.items():
        graph[rel] = {
            project_modules[mod]
            for mod in imports
            if mod in project_modules
        }
    return graph


def transitive_blocked_deps(
    target: Path,
    graph: dict[Path, set[Path]],
    blocked: set[Path],
    *,
    presumed_fixed: set[Path] | None = None,
) -> set[Path]:
    """BFS ``target``'s local imports; return the subset that's blocked.

    ``presumed_fixed`` is removed from the blocked set BEFORE the walk:
    when the planner assigns a blocked file F2 in the same iter as a
    downstream file F1 that imports it, F2 is "being fixed this iter"
    and shouldn't block F1. The dispatch + file-locking machinery
    handles the ordering at runtime; here we just need to not drop F1
    pre-emptively.
    """
    effective_blocked = blocked - (presumed_fixed or set())
    if not effective_blocked:
        return set()

    seen: set[Path] = set()
    queue: deque[Path] = deque([target])
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        for imp in graph.get(node, set()):
            if imp not in seen:
                queue.append(imp)
    # Don't count ``target`` itself as a blocked dep of itself even if
    # it's blocked — that case is "F is blocked AND being assigned this
    # iter to fix", which is fine and handled by the caller via
    # ``presumed_fixed``.
    seen.discard(target)
    return seen & effective_blocked


def filter_objectives_for_blocked_deps(
    objectives: list[Path],
    *,
    blocked: set[Path],
    graph: dict[Path, set[Path]],
    project_path: Path,
) -> tuple[list[Path], list[tuple[Path, list[Path]]]]:
    """Split objectives into (keep, drop) based on blocked transitive deps.

    ``objectives`` is the parsed-and-resolved list from
    ``parse_objective_files`` — absolute or project-relative paths
    pointing at .lean files. They get normalized to project-relative
    for graph lookup.

    ``drop`` is ``[(dropped_file, [blocked_deps...])]`` so the caller
    can render a useful USER_HINTS message ("Foo.lean dropped because
    it imports Bar.lean which doesn't compile").

    A blocked file that's *itself* an objective is presumed-being-fixed
    and is kept; its presence in another objective's import chain
    doesn't disqualify the downstream objective either.
    """
    proj_resolved = project_path.resolve()

    def _to_rel(p: Path) -> Path:
        try:
            return p.resolve().relative_to(proj_resolved)
        except (OSError, ValueError):
            return p

    objective_rels = [_to_rel(p) for p in objectives]
    objective_rel_set = set(objective_rels)
    # Files the planner is targeting this iter are presumed-being-fixed,
    # so their failures don't propagate down.
    presumed_fixed = objective_rel_set & blocked

    keep: list[Path] = []
    drop: list[tuple[Path, list[Path]]] = []
    for absolute, rel in zip(objectives, objective_rels):
        # A blocked-file-being-fixed itself is always kept.
        if rel in presumed_fixed:
            keep.append(absolute)
            continue
        bad = transitive_blocked_deps(
            rel, graph, blocked, presumed_fixed=presumed_fixed,
        )
        if bad:
            drop.append((absolute, sorted(bad)))
        else:
            keep.append(absolute)
    return keep, drop

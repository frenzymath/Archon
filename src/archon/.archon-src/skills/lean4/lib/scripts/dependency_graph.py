#!/usr/bin/env python3
"""
dependency_graph.py - Extract a Lean-project dependency graph as JSON.

Two layers:

  1. Lean imports.  Every .lean file's `import Foo.Bar.Baz` lines are
     parsed.  Imports are split into "local" (resolves to a file in the
     project) and "external" (Mathlib, Std, …).
  2. Blueprint declarations.  Every blueprint/src/chapters/*.tex file
     is scanned for leanblueprint macros: \\lean{name}, \\uses{a,b,c},
     \\proves{thm}, and \\leanok.  Output: which informal lemma uses
     which other lemma, and whether each has been formalised.

The planner agent should call this once per iteration instead of
hand-rolling the same graph by reading every file — saves tokens and
avoids drift.

Usage:
    dependency_graph.py [project-path] [--format=json|dot|summary|frontier|frontier-summary]
                        [--out FILE] [--include-deps]

Defaults:
    project-path: current working directory
    --format:     json (machine-readable; the planner prompt expects this)
    --out:        stdout

Format notes:
    frontier        JSON with frontier (all deps leanok), near-frontier,
                    blocked, and broken \\uses{} refs.
    frontier-summary  Compact text; auto-injected into the plan prompt each
                    iteration so the planner knows what is ready to prove.

Examples:
    dependency_graph.py
    dependency_graph.py /path/to/project --format=summary
    dependency_graph.py . --format=dot --out deps.dot
    dependency_graph.py . --format=frontier-summary
    dependency_graph.py . --include-deps      # include .lake/ subprojects
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ── Lean imports ──────────────────────────────────────────────────────


# `import Foo.Bar.Baz` at start of a line, ignoring leading whitespace.
# Lean accepts a runtime module string after `import`, but in practice
# every project we care about uses dotted module names.
_IMPORT_RE = re.compile(r'^\s*import\s+([A-Za-z_][A-Za-z0-9_.]*)\b')


def _module_name(project_path: Path, lean_file: Path) -> str:
    """Lean module name for a path, e.g. Algebra/WLocal.lean -> Algebra.WLocal."""
    rel = lean_file.relative_to(project_path)
    return '.'.join(rel.with_suffix('').parts)


def _scan_lean_file(path: Path) -> list[str]:
    imports: list[str] = []
    try:
        text = path.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return imports
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith('--'):
            continue
        # imports must come before any non-import declaration; once we see
        # a `def`/`theorem`/etc., stop scanning.
        if not stripped.startswith('import'):
            # Allow comments and whitespace; bail on real code.
            if stripped.startswith('/-') or stripped.startswith('-/'):
                continue
            if stripped.startswith(('namespace', 'section', 'open', 'set_option', 'syntax', 'macro')):
                continue
            if any(stripped.startswith(kw) for kw in (
                'def ', 'theorem ', 'lemma ', 'instance ', 'example ',
                'structure ', 'inductive ', 'class ', 'abbrev ', 'noncomputable ',
                'partial ', 'variable ', '#check', '#eval', '#print',
            )):
                break
            continue
        match = _IMPORT_RE.match(stripped)
        if match:
            imports.append(match.group(1))
    return imports


@dataclass
class Module:
    path: str             # repo-relative, e.g. "Algebra/WLocal.lean"
    module: str           # dotted module name, e.g. "Algebra.WLocal"
    imports: list[str]    # raw imports as written
    local_imports: list[str] = field(default_factory=list)


def collect_modules(project_path: Path, *, include_deps: bool) -> list[Module]:
    skip_parts = {'.git', '.archon'}
    if not include_deps:
        skip_parts |= {'.lake', 'lake-packages'}

    out: list[Module] = []
    project_modules: set[str] = set()
    pending: list[tuple[Path, list[str]]] = []
    for path in sorted(project_path.rglob('*.lean')):
        if any(part in skip_parts for part in path.relative_to(project_path).parts):
            continue
        imports = _scan_lean_file(path)
        module = _module_name(project_path, path)
        project_modules.add(module)
        pending.append((path, imports))
        out.append(Module(
            path=str(path.relative_to(project_path)),
            module=module,
            imports=imports,
        ))

    for mod in out:
        mod.local_imports = sorted(i for i in mod.imports if i in project_modules)
    return out


# ── blueprint declarations ────────────────────────────────────────────


# leanblueprint environments — \begin{theorem}, \begin{lemma}, etc.
_BEGIN_RE = re.compile(r'\\begin\s*\{(definition|theorem|lemma|proposition|corollary|remark|example)\s*\}', re.IGNORECASE)
_END_RE = re.compile(r'\\end\s*\{(definition|theorem|lemma|proposition|corollary|remark|example)\s*\}', re.IGNORECASE)
_LEAN_RE = re.compile(r'\\lean\s*\{\s*([^{}]*)\s*\}')
_USES_RE = re.compile(r'\\uses\s*\{\s*([^{}]*)\s*\}')
_PROVES_RE = re.compile(r'\\proves\s*\{\s*([^{}]*)\s*\}')
_LABEL_RE = re.compile(r'\\label\s*\{\s*([^{}]*)\s*\}')
_LEANOK_RE = re.compile(r'\\leanok\b')
_NOTREADY_RE = re.compile(r'\\notready\b')


def _split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(',') if x.strip()]


@dataclass
class BlueprintDecl:
    kind: str             # theorem / lemma / definition / …
    label: str | None
    name: str | None      # value of \lean{…} (the Lean identifier)
    uses: list[str] = field(default_factory=list)
    proves: list[str] = field(default_factory=list)
    leanok: bool = False
    notready: bool = False


@dataclass
class BlueprintFile:
    chapter_file: str     # repo-relative, e.g. "blueprint/src/chapters/Algebra_WLocal.tex"
    lean_file_guess: str | None
    declarations: list[BlueprintDecl] = field(default_factory=list)


def _slug_to_lean_path(stem: str) -> str:
    """Algebra_WLocal -> Algebra/WLocal.lean (the inverse of the slug rule)."""
    return stem.replace('_', '/') + '.lean'


def _parse_chapter(text: str) -> list[BlueprintDecl]:
    decls: list[BlueprintDecl] = []
    pos = 0
    while True:
        begin = _BEGIN_RE.search(text, pos)
        if not begin:
            break
        end = _END_RE.search(text, begin.end())
        body = text[begin.end():end.start()] if end else text[begin.end():]
        labels = _LABEL_RE.findall(body)
        leans = _LEAN_RE.findall(body)
        uses = _USES_RE.findall(body)
        proves = _PROVES_RE.findall(body)
        decls.append(BlueprintDecl(
            kind=begin.group(1).lower(),
            label=labels[0] if labels else None,
            name=leans[0].strip() if leans else None,
            uses=sorted({u for chunk in uses for u in _split_csv(chunk)}),
            proves=sorted({p for chunk in proves for p in _split_csv(chunk)}),
            leanok=bool(_LEANOK_RE.search(body)),
            notready=bool(_NOTREADY_RE.search(body)),
        ))
        pos = end.end() if end else len(text)
    return decls


def collect_blueprint(project_path: Path) -> list[BlueprintFile]:
    chapters_dir = project_path / 'blueprint' / 'src' / 'chapters'
    if not chapters_dir.is_dir():
        return []
    out: list[BlueprintFile] = []
    for tex in sorted(chapters_dir.glob('*.tex')):
        try:
            text = tex.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        guess = _slug_to_lean_path(tex.stem)
        guess_full = project_path / guess
        out.append(BlueprintFile(
            chapter_file=str(tex.relative_to(project_path)),
            lean_file_guess=guess if guess_full.exists() else None,
            declarations=_parse_chapter(text),
        ))
    return out


# ── output formatters ─────────────────────────────────────────────────


def render_json(modules: list[Module], blueprint: list[BlueprintFile]) -> str:
    return json.dumps({
        'modules': [asdict(m) for m in modules],
        'blueprint': [asdict(b) for b in blueprint],
    }, indent=2) + '\n'


def render_summary(modules: list[Module], blueprint: list[BlueprintFile]) -> str:
    lines: list[str] = []
    lines.append(f"Lean modules: {len(modules)}")
    local_total = sum(len(m.local_imports) for m in modules)
    extern_total = sum(len(m.imports) - len(m.local_imports) for m in modules)
    lines.append(f"  local imports: {local_total}    external imports: {extern_total}")
    if modules:
        lines.append("")
        lines.append("Per-module local-import fan-out (top 10):")
        ranked = sorted(modules, key=lambda m: -len(m.local_imports))[:10]
        for m in ranked:
            lines.append(f"  {m.module:40s} -> {len(m.local_imports)} local")
    if blueprint:
        decl_count = sum(len(b.declarations) for b in blueprint)
        ok = sum(1 for b in blueprint for d in b.declarations if d.leanok)
        nr = sum(1 for b in blueprint for d in b.declarations if d.notready)
        lines.append("")
        lines.append(f"Blueprint chapters: {len(blueprint)}")
        lines.append(f"  declarations: {decl_count}    \\leanok: {ok}    \\notready: {nr}")
    return '\n'.join(lines) + '\n'


def render_dot(modules: list[Module], blueprint: list[BlueprintFile]) -> str:
    lines = ['digraph deps {', '  rankdir=LR;', '  node [shape=box,fontsize=10];']
    for m in modules:
        lines.append(f'  "{m.module}";')
    for m in modules:
        for dep in m.local_imports:
            lines.append(f'  "{m.module}" -> "{dep}";')
    if blueprint:
        lines.append('  // blueprint \\uses edges')
        for b in blueprint:
            for d in b.declarations:
                if not d.name:
                    continue
                for u in d.uses:
                    lines.append(f'  "bp:{u}" -> "bp:{d.name}" [style=dashed,color=gray];')
    lines.append('}')
    return '\n'.join(lines) + '\n'


# ── Frontier computation ──────────────────────────────────────────────
#
# "Frontier" = declarations that are:
#   - not yet \leanok  (still needs a proof)
#   - not \notready    (not explicitly parked)
#   - all \uses{} deps are \leanok  (every prerequisite is done)
#
# "Near-frontier" = not leanok, every blocking dep is itself on the frontier.
# "Blocked"       = not leanok, at least one dep is neither leanok nor frontier.
# "Broken \uses{}" = \uses{label} where label has no \label{} in any chapter.


@dataclass
class FrontierEntry:
    label: str | None
    lean_name: str | None       # from \lean{}, None if missing
    chapter_file: str           # repo-relative .tex path
    lean_file: str | None       # guessed .lean path (None if file absent)
    kind: str                   # theorem / lemma / definition / …
    uses: list[str]             # all \uses{} labels
    depth: int                  # topological depth (0 = no deps)


def _topo_depths(label_to_uses: dict[str, list[str]]) -> dict[str, int]:
    """Compute topological depth for each label (iterative BFS / Kahn's)."""
    # in-degree for each label that appears as a target
    in_deg: dict[str, int] = {lbl: 0 for lbl in label_to_uses}
    children: dict[str, list[str]] = {lbl: [] for lbl in label_to_uses}
    for lbl, deps in label_to_uses.items():
        for dep in deps:
            if dep in label_to_uses:
                in_deg[lbl] = in_deg.get(lbl, 0)  # ensure key exists
                children.setdefault(dep, []).append(lbl)

    depths: dict[str, int] = {}
    queue = [lbl for lbl, d in in_deg.items() if d == 0]
    for lbl in queue:
        depths.setdefault(lbl, 0)

    while queue:
        nxt: list[str] = []
        for lbl in queue:
            for child in children.get(lbl, []):
                new_depth = depths.get(lbl, 0) + 1
                if new_depth > depths.get(child, 0):
                    depths[child] = new_depth
                nxt.append(child)
        queue = nxt

    # Nodes not reached (cycles or disconnected) get depth 0.
    for lbl in label_to_uses:
        depths.setdefault(lbl, 0)
    return depths


def compute_frontier(blueprint: list[BlueprintFile]) -> dict:
    """Return a dict with keys: frontier, near_frontier, blocked, broken_uses, stats."""
    # Global label → (decl, chapter_file, lean_file_guess)
    label_map: dict[str, tuple[BlueprintDecl, str, str | None]] = {}
    for bf in blueprint:
        for d in bf.declarations:
            if d.label:
                label_map[d.label] = (d, bf.chapter_file, bf.lean_file_guess)

    leanok_labels: set[str] = {lbl for lbl, (d, _, __) in label_map.items() if d.leanok}

    # Topological depth — only over declared labels
    label_to_uses = {
        lbl: [u for u in d.uses if u in label_map]
        for lbl, (d, _, __) in label_map.items()
    }
    depths = _topo_depths(label_to_uses)

    frontier: list[FrontierEntry] = []
    pre_blocked: list[tuple[FrontierEntry, list[str]]] = []  # (entry, missing_leanok_labels)
    broken_uses: list[dict] = []  # {chapter, label, missing_labels}

    for bf in blueprint:
        for d in bf.declarations:
            if d.leanok or d.notready:
                continue

            # Separate broken refs from valid (but possibly unproved) deps
            unknown = [u for u in d.uses if u not in label_map]
            missing = [u for u in d.uses if u in label_map and u not in leanok_labels]

            if unknown:
                broken_uses.append({
                    'chapter': bf.chapter_file,
                    'label': d.label,
                    'missing_labels': unknown,
                })

            entry = FrontierEntry(
                label=d.label,
                lean_name=d.name,
                chapter_file=bf.chapter_file,
                lean_file=bf.lean_file_guess,
                kind=d.kind,
                uses=d.uses,
                depth=depths.get(d.label, 0) if d.label else 0,
            )
            if not missing:
                frontier.append(entry)
            else:
                pre_blocked.append((entry, missing))

    # Separate near-frontier from hard-blocked
    frontier_labels = {e.label for e in frontier if e.label}
    near_frontier: list[tuple[FrontierEntry, list[str]]] = []
    blocked: list[tuple[FrontierEntry, list[str]]] = []
    for entry, missing in pre_blocked:
        if all(m in frontier_labels for m in missing):
            near_frontier.append((entry, missing))
        else:
            blocked.append((entry, missing))

    # Sort frontier: depth asc, then uses-count asc (simpler proofs first)
    frontier.sort(key=lambda e: (e.depth, len(e.uses), e.lean_name or e.label or ""))

    total = sum(len(bf.declarations) for bf in blueprint)
    return {
        'frontier': [asdict(e) for e in frontier],
        'near_frontier': [
            {'entry': asdict(e), 'waiting_for': miss}
            for e, miss in near_frontier
        ],
        'blocked': [
            {'entry': asdict(e), 'waiting_for': miss}
            for e, miss in blocked
        ],
        'broken_uses': broken_uses,
        'stats': {
            'total': total,
            'leanok': len(leanok_labels),
            'frontier': len(frontier),
            'near_frontier': len(near_frontier),
            'blocked': len(blocked),
            'broken_uses': len(broken_uses),
        },
    }


def render_frontier_json(result: dict) -> str:
    return json.dumps(result, indent=2) + '\n'


def render_frontier_summary(result: dict, *, max_frontier: int = 30) -> str:
    s = result['stats']
    frontier_all = result['frontier']

    # Split into prover-dispatchable (has real \lean{} name) vs. blueprint-only.
    # Filter out "..." placeholders that appear when the author writes \lean{...}
    # as a stub before filling in the actual name.
    def _is_real_lean_name(name: str | None) -> bool:
        return bool(name) and name.strip('.').strip() != ""

    frontier_lean = [e for e in frontier_all if _is_real_lean_name(e['lean_name'])]
    frontier_informal = [e for e in frontier_all if not _is_real_lean_name(e['lean_name'])]

    lines: list[str] = []
    lines.append(
        f"Total: {s['total']} declarations — "
        f"{s['leanok']} ✓ leanok, "
        f"{len(frontier_lean)} ready (Lean), "
        f"{len(frontier_informal)} ready (informal/no \\lean{{}}), "
        f"{s['near_frontier']} near-frontier, "
        f"{s['blocked']} blocked"
    )

    if frontier_lean:
        lines.append("")
        lines.append("## Ready to prove — prover-dispatchable (all \\uses{} deps \\leanok, has \\lean{}):")
        for e in frontier_lean[:max_frontier]:
            file_hint = f"  [{e['lean_file']}]" if e['lean_file'] else ""
            uses_str = (
                "uses: " + ", ".join(e['uses'])
                if e['uses'] else "no \\uses{} deps"
            )
            lines.append(f"  {e['lean_name']}{file_hint}  ({uses_str})")
        if len(frontier_lean) > max_frontier:
            lines.append(f"  … and {len(frontier_lean) - max_frontier} more")
    else:
        lines.append("")
        lines.append("## Ready to prove: (none with \\lean{} — either all leanok or all have unmet deps)")

    if frontier_informal:
        # Only show those that at least have a \label{} so we can identify them.
        informal_labeled = [e for e in frontier_informal if e['label']]
        informal_unlabeled = len(frontier_informal) - len(informal_labeled)
        lines.append("")
        lines.append(
            f"## Ready but missing \\lean{{}} ({len(frontier_informal)} decls) — "
            f"blueprint-writer should add \\lean{{Name}} before dispatching:"
        )
        for e in informal_labeled[:10]:
            chapter = e['chapter_file'].split('/')[-1].replace('.tex', '')
            lines.append(f"  [{chapter}] \\label{{{e['label']}}}  (no \\lean{{}} name)")
        if len(informal_labeled) > 10:
            lines.append(f"  … and {len(informal_labeled) - 10} more labeled")
        if informal_unlabeled:
            lines.append(f"  + {informal_unlabeled} with neither \\label{{}} nor \\lean{{}}")

    near = result['near_frontier']
    if near:
        lines.append("")
        lines.append("## Near-frontier (blocked only by ready nodes above):")
        for item in near[:15]:
            e = item['entry']
            lean = e['lean_name'] or f"[{e['label'] or '?'}]"
            waiting = ", ".join(item['waiting_for'])
            lines.append(f"  {lean}  →  waiting for: {waiting}")
        if len(near) > 15:
            lines.append(f"  … and {len(near) - 15} more")

    broken = result['broken_uses']
    if broken:
        lines.append("")
        lines.append("## Broken \\uses{} (label not in any blueprint chapter — add the blueprint or fix the ref):")
        for item in broken[:10]:
            chapter = item['chapter'].split('/')[-1].replace('.tex', '')
            lbl = item['label'] or '?'
            miss = ", ".join(item['missing_labels'])
            lines.append(f"  [{chapter}] {lbl}  →  unknown: {miss}")
        if len(broken) > 10:
            lines.append(f"  … and {len(broken) - 10} more")

    return '\n'.join(lines) + '\n'


# ── CLI ───────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    project = Path('.').resolve()
    fmt = 'json'
    out_path: Path | None = None
    include_deps = False
    max_frontier = 30

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ('-h', '--help'):
            print(__doc__)
            return 0
        if arg.startswith('--format='):
            fmt = arg.split('=', 1)[1]
        elif arg == '--format':
            fmt = argv[i + 1]; i += 1
        elif arg.startswith('--out='):
            out_path = Path(arg.split('=', 1)[1])
        elif arg == '--out':
            out_path = Path(argv[i + 1]); i += 1
        elif arg == '--include-deps':
            include_deps = True
        elif arg.startswith('--max-frontier='):
            try:
                max_frontier = int(arg.split('=', 1)[1])
            except ValueError:
                print(f"--max-frontier requires an integer", file=sys.stderr)
                return 2
        elif not arg.startswith('-'):
            project = Path(arg).resolve()
        else:
            print(f"Unknown flag: {arg}", file=sys.stderr)
            return 2
        i += 1

    if not project.is_dir():
        print(f"Not a directory: {project}", file=sys.stderr)
        return 2

    modules = collect_modules(project, include_deps=include_deps)
    blueprint = collect_blueprint(project)

    if fmt == 'json':
        out = render_json(modules, blueprint)
    elif fmt == 'summary':
        out = render_summary(modules, blueprint)
    elif fmt == 'dot':
        out = render_dot(modules, blueprint)
    elif fmt == 'frontier':
        out = render_frontier_json(compute_frontier(blueprint))
    elif fmt == 'frontier-summary':
        out = render_frontier_summary(compute_frontier(blueprint), max_frontier=max_frontier)
    else:
        print(f"Unknown format: {fmt}", file=sys.stderr)
        return 2

    if out_path:
        out_path.write_text(out, encoding='utf-8')
    else:
        sys.stdout.write(out)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

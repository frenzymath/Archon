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
    dependency_graph.py [project-path] [--format=json|dot|summary] [--out FILE]
                        [--include-deps]

Defaults:
    project-path: current working directory
    --format:     json (machine-readable; the planner prompt expects this)
    --out:        stdout

Examples:
    dependency_graph.py
    dependency_graph.py /path/to/project --format=summary
    dependency_graph.py . --format=dot --out deps.dot
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


# ── CLI ───────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    project = Path('.').resolve()
    fmt = 'json'
    out_path: Path | None = None
    include_deps = False

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

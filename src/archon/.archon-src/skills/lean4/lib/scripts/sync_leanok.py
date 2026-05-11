#!/usr/bin/env python3
"""sync_leanok.py — deterministic blueprint marker sync.

Replaces the review agent's mechanical marker placement with a script
that walks every ``blueprint/src/chapters/*.tex`` and updates each
declaration block's ``\\leanok`` markers based on the actual state of
the Lean source.

Algorithm (per chapter file):

  for each declaration block (theorem/lemma/definition/...) in the chapter:
      if the block carries `\\mathlibok` or `\\notready`: skip — those
          are semantic decisions only the review agent / mathematician
          makes
      otherwise:
          look up `\\lean{name}` in the Lean source
          if name not found:                     remove statement \\leanok if present
          else if file does not compile cleanly: remove statement \\leanok if present
          else:                                  ensure statement \\leanok is present
      for each `\\begin{proof}...\\end{proof}` immediately following:
          if the corresponding Lean decl has 0 sorries AND file compiles cleanly:
              ensure proof \\leanok is present
          else:
              remove proof \\leanok if present

The script never touches any other marker (`\\mathlibok`, `\\notready`,
`% NOTE: ...` comments) and never rewrites prose.

Usage:
    sync_leanok.py [project-path] [--dry-run] [--verbose]

Exit code is 0 unless something genuinely broke (project not found,
Lean source unreadable). A successful run with no changes also exits
0; the dry-run mode prints what *would* change without writing.

The companion phase ``commands/loop/phases/sync_leanok.py`` runs this
between the prover and review phases. The review agent then writes
prose summaries and `\\mathlibok` markers, but doesn't have to do
mechanical \\leanok bookkeeping anymore.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


# ── chapter parsing ───────────────────────────────────────────────────


_BEGIN_RE = re.compile(
    r'\\begin\s*\{(definition|theorem|lemma|proposition|corollary|remark|example)\s*\}',
)
_END_RE = re.compile(
    r'\\end\s*\{(definition|theorem|lemma|proposition|corollary|remark|example)\s*\}',
)
_PROOF_BEGIN_RE = re.compile(r'\\begin\s*\{proof\s*\}')
_PROOF_END_RE = re.compile(r'\\end\s*\{proof\s*\}')
_LEAN_RE = re.compile(r'\\lean\s*\{\s*([^{}]*)\s*\}')
_LEANOK_RE = re.compile(r'\\leanok\b')
_MATHLIBOK_RE = re.compile(r'\\mathlibok\b')
_NOTREADY_RE = re.compile(r'\\notready\b')


@dataclass
class Block:
    """One statement (or proof) span inside a chapter file."""
    start: int           # index into the file content (inclusive)
    end: int             # index into the file content (exclusive)
    kind: str            # 'theorem' | 'lemma' | ... | 'proof'
    lean_name: str | None  # for statement blocks; the \lean{...} value
    has_leanok: bool
    has_mathlibok: bool
    has_notready: bool


def _parse_chapter_blocks(text: str) -> list[Block]:
    """Walk the chapter linearly; emit one Block per environment span.

    Statement and proof blocks are emitted in source order. Proof
    blocks immediately following a statement block share the
    statement's ``lean_name`` for downstream verification.
    """
    blocks: list[Block] = []
    pos = 0
    last_lean_name: str | None = None
    while pos < len(text):
        # Find the next theorem-like begin OR proof begin, whichever is
        # earlier.
        m_stmt = _BEGIN_RE.search(text, pos)
        m_proof = _PROOF_BEGIN_RE.search(text, pos)
        if m_stmt is None and m_proof is None:
            break
        if m_stmt and (m_proof is None or m_stmt.start() < m_proof.start()):
            kind = m_stmt.group(1).lower()
            end_re = _END_RE
            begin_match = m_stmt
        else:
            kind = 'proof'
            end_re = _PROOF_END_RE
            begin_match = m_proof  # type: ignore[assignment]

        end_match = end_re.search(text, begin_match.end())
        if end_match is None:
            break
        body = text[begin_match.end():end_match.start()]
        if kind == 'proof':
            lean_name = last_lean_name
        else:
            m = _LEAN_RE.search(body)
            lean_name = m.group(1).strip() if m else None
            last_lean_name = lean_name

        blocks.append(Block(
            start=begin_match.start(),
            end=end_match.end(),
            kind=kind,
            lean_name=lean_name,
            has_leanok=bool(_LEANOK_RE.search(body)),
            has_mathlibok=bool(_MATHLIBOK_RE.search(body)),
            has_notready=bool(_NOTREADY_RE.search(body)),
        ))
        pos = end_match.end()
    return blocks


# ── Lean source parsing ───────────────────────────────────────────────


# Lean identifiers admit `'`, unicode subscripts (₀-₉), Greek letters
# (δ, σ, …), primes, dots (for namespaced names), and french quotes.
# Match anything that isn't whitespace, structural punctuation, or
# something that obviously starts an attribute / signature. This is
# deliberately liberal — false positives get filtered out by the
# blueprint lookup (we only ask "is this name in our index?").
_IDENT = r"[^\s:={}()\[\],|;]+"
_DECL_RE = re.compile(
    r'^\s*(?:@\[[^\]]*\]\s*)*'
    r'(?:noncomputable\s+|private\s+|protected\s+|partial\s+|nonrec\s+'
    r'|unsafe\s+|opaque\s+|abbrev\s+)*'
    r'(?:theorem|lemma|def|abbrev|instance|example|class|structure|inductive)'
    r'\s+(' + _IDENT + r')',
    re.MULTILINE,
)


def _scan_lean_decls(project_path: Path) -> dict[str, Path]:
    """Map fully-qualified Lean decl names → file path.

    The decl extractor is deliberately conservative: it only catches
    declarations whose name follows the keyword on the same line. That
    misses heavily-formatted decls but covers the common case the
    blueprint references.

    For namespaced decls, we record both the bare name and the
    namespace-qualified form. The blueprint typically uses the
    qualified form; the bare form helps when chapter authors only
    write the suffix.
    """
    out: dict[str, Path] = {}
    for lean in project_path.rglob('*.lean'):
        # Skip dependencies and build artefacts.
        parts = lean.parts
        if any(p in {'.lake', 'lake-packages', '_target', 'build'} for p in parts):
            continue
        try:
            text = lean.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        # Track namespace nesting for qualified-name reconstruction.
        ns_stack: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('namespace '):
                ns_stack.append(stripped.split(None, 1)[1].split()[0])
            elif stripped == 'end' or stripped.startswith('end '):
                tail = stripped[3:].strip() if stripped != 'end' else ''
                if ns_stack and (not tail or ns_stack[-1].endswith(tail)):
                    ns_stack.pop()
            else:
                m = _DECL_RE.match(line)
                if m:
                    bare = m.group(1)
                    qual = '.'.join(ns_stack + [bare]) if ns_stack else bare
                    # First definition wins — duplicates are unusual but
                    # if they exist, the earlier file is more likely the
                    # canonical home.
                    out.setdefault(bare, lean)
                    out.setdefault(qual, lean)
    return out


def _decl_has_sorry(lean_file: Path, decl_name: str) -> bool | None:
    """Return True/False if we can decide; None if we can't.

    Uses the bundled ``sorry_analyzer.py`` (JSON output, with the
    decl-name attribution it already does). When sorry_analyzer can't
    run (missing, error), return None — the caller treats it as "don't
    change the marker."
    """
    here = Path(__file__).resolve().parent
    analyzer = here / 'sorry_analyzer.py'
    if not analyzer.exists():
        return None
    try:
        r = subprocess.run(
            [sys.executable, str(analyzer), str(lean_file),
             '--format=json', '--report-only'],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 and not r.stdout:
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    sorries = data.get('sorries') if isinstance(data, dict) else data
    if not isinstance(sorries, list):
        return None

    bare = decl_name.rsplit('.', 1)[-1]
    for s in sorries:
        in_decl = s.get('in_declaration') if isinstance(s, dict) else None
        if not in_decl:
            continue
        if in_decl == decl_name or in_decl == bare:
            return True
        if in_decl.endswith('.' + bare) or decl_name.endswith('.' + in_decl):
            return True
    return False


def _file_compiles(lean_file: Path, project_path: Path) -> bool | None:
    """Best-effort compile check. Returns None if we can't decide."""
    try:
        r = subprocess.run(
            ['lake', 'env', 'lean', str(lean_file.relative_to(project_path))],
            capture_output=True, text=True, timeout=180,
            cwd=project_path,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0


# ── marker rewriting ──────────────────────────────────────────────────


def _add_leanok(body: str) -> str:
    """Insert ``\\leanok`` near the top of the block body.

    Convention: place \\leanok on its own line right after the opening
    metadata cluster (\\label, \\lean, \\uses) and before the prose.
    Falls back to inserting at the very start when no metadata is
    found.
    """
    if _LEANOK_RE.search(body):
        return body
    # Find the last metadata line at the top of the block.
    lines = body.splitlines(keepends=True)
    insert_at = 0
    metadata_re = re.compile(r'^\s*\\(label|lean|uses|proves)\s*\{')
    leading = 0
    for i, line in enumerate(lines):
        if line.strip() == '':
            leading = i + 1
            continue
        if metadata_re.match(line):
            leading = i + 1
            continue
        break
    insert_at = leading
    indent = ''
    if 0 < insert_at <= len(lines):
        # Match the indentation of the metadata line above.
        prev = lines[insert_at - 1] if insert_at > 0 else ''
        m = re.match(r'(\s*)', prev)
        if m:
            indent = m.group(1)
    new_line = f'{indent}\\leanok\n'
    return ''.join(lines[:insert_at]) + new_line + ''.join(lines[insert_at:])


_LEANOK_LINE_RE = re.compile(r'^[ \t]*\\leanok\b[^\n]*\n?', re.MULTILINE)


def _remove_leanok(body: str) -> str:
    """Drop ``\\leanok`` and any inline trailing whitespace it sat on."""
    new = _LEANOK_LINE_RE.sub('', body)
    if new != body:
        return new
    # Inline form (rare): just strip the macro itself.
    return _LEANOK_RE.sub('', body)


# ── per-chapter sync ──────────────────────────────────────────────────


@dataclass
class Change:
    chapter: Path
    block_kind: str
    lean_name: str | None
    action: str  # 'add' | 'remove' | 'noop'
    reason: str


def _sync_chapter(
    chapter_path: Path,
    project_path: Path,
    decl_index: dict[str, Path],
    *,
    dry_run: bool,
    verbose: bool,
    compile_cache: dict[Path, bool | None],
) -> list[Change]:
    """Update one chapter's `\\leanok` markers in place. Returns the
    list of changes (or would-be changes for ``--dry-run``)."""
    try:
        text = chapter_path.read_text(encoding='utf-8')
    except OSError:
        return []
    blocks = _parse_chapter_blocks(text)
    changes: list[Change] = []

    # Walk blocks in REVERSE source order so byte-offset edits to
    # later blocks don't shift earlier offsets.
    new_text = text
    for blk in reversed(blocks):
        if blk.has_mathlibok or blk.has_notready:
            # Out of scope — only the review agent / mathematician
            # touches these.
            continue
        if blk.lean_name is None:
            # No \lean{...} → can't verify; leave alone.
            continue

        lean_file = decl_index.get(blk.lean_name)
        if lean_file is None:
            # Decl not found in the project — remove leanok if present.
            should_have = False
            reason = f"decl {blk.lean_name!r} not found in project"
        else:
            compiles = compile_cache.get(lean_file)
            if compiles is None and lean_file not in compile_cache:
                compiles = _file_compiles(lean_file, project_path)
                compile_cache[lean_file] = compiles
            if compiles is None:
                # Couldn't decide — don't change anything.
                continue
            if blk.kind == 'proof':
                if not compiles:
                    should_have = False
                    reason = f"{lean_file.name} does not compile"
                else:
                    has_sorry = _decl_has_sorry(lean_file, blk.lean_name)
                    if has_sorry is None:
                        continue  # undecided
                    should_have = not has_sorry
                    reason = (
                        f"0 sorries + compiles" if should_have
                        else f"sorry found in {blk.lean_name}"
                    )
            else:
                # Statement block — \leanok iff decl exists and file
                # compiles. Proof body's sorry status is irrelevant.
                should_have = bool(compiles)
                reason = (
                    "decl exists + compiles" if should_have
                    else f"{lean_file.name} does not compile"
                )

        if should_have == blk.has_leanok:
            if verbose:
                changes.append(Change(
                    chapter=chapter_path, block_kind=blk.kind,
                    lean_name=blk.lean_name, action='noop', reason=reason,
                ))
            continue

        action = 'add' if should_have else 'remove'
        changes.append(Change(
            chapter=chapter_path, block_kind=blk.kind,
            lean_name=blk.lean_name, action=action, reason=reason,
        ))

        if dry_run:
            continue

        # Apply edit. Body is everything between begin and end markers.
        # Locate them inside the [start, end] span.
        block_text = new_text[blk.start:blk.end]
        if blk.kind == 'proof':
            begin_re, end_re = _PROOF_BEGIN_RE, _PROOF_END_RE
        else:
            begin_re, end_re = _BEGIN_RE, _END_RE
        bm = begin_re.search(block_text)
        em = end_re.search(block_text)
        if bm is None or em is None:
            continue
        body_start = bm.end()
        body_end = em.start()
        body = block_text[body_start:body_end]
        new_body = _add_leanok(body) if action == 'add' else _remove_leanok(body)
        if new_body == body:
            continue
        new_block = block_text[:body_start] + new_body + block_text[body_end:]
        new_text = new_text[:blk.start] + new_block + new_text[blk.end:]

    if not dry_run and new_text != text:
        chapter_path.write_text(new_text, encoding='utf-8')
    return changes


# ── CLI ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_path', nargs='?', default='.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print would-be changes; do not write.')
    parser.add_argument('--verbose', action='store_true',
                        help='Include no-op decisions in the output.')
    parser.add_argument('--format', choices=['text', 'json'], default='text')
    args = parser.parse_args(argv)

    project_path = Path(args.project_path).resolve()
    chapters_dir = project_path / 'blueprint' / 'src' / 'chapters'
    if not chapters_dir.is_dir():
        print(f'No blueprint chapters at {chapters_dir}', file=sys.stderr)
        return 0  # not a failure — just nothing to do

    decl_index = _scan_lean_decls(project_path)
    compile_cache: dict[Path, bool | None] = {}
    all_changes: list[Change] = []
    for tex in sorted(chapters_dir.glob('*.tex')):
        all_changes.extend(_sync_chapter(
            tex, project_path, decl_index,
            dry_run=args.dry_run, verbose=args.verbose,
            compile_cache=compile_cache,
        ))

    if args.format == 'json':
        payload = [{
            'chapter': str(c.chapter.relative_to(project_path)),
            'block_kind': c.block_kind,
            'lean_name': c.lean_name,
            'action': c.action,
            'reason': c.reason,
        } for c in all_changes]
        print(json.dumps(payload, indent=2))
    else:
        added = sum(1 for c in all_changes if c.action == 'add')
        removed = sum(1 for c in all_changes if c.action == 'remove')
        prefix = '[dry-run] ' if args.dry_run else ''
        print(f'{prefix}{added} \\leanok additions, {removed} removals')
        if args.verbose or args.dry_run:
            for c in all_changes:
                rel = c.chapter.relative_to(project_path)
                print(f'  {c.action:6}  {rel}  {c.block_kind:9}  '
                      f'{c.lean_name or "?":<40}  {c.reason}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

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
import os
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
    # sorry_analyzer.extract_declaration_name emits "<keyword> <name>"
    # (e.g. "theorem foo"). Strip the keyword before comparing — otherwise
    # bare/qualified comparisons never match and proof-block \leanok on
    # sorry-bodied decls is wrongly preserved.
    _DECL_KEYWORDS = {
        'theorem', 'lemma', 'def', 'abbrev',
        'instance', 'example', 'structure', 'class',
    }
    saw_unattributed = False
    for s in sorries:
        in_decl = s.get('in_declaration') if isinstance(s, dict) else None
        if not in_decl:
            # A sorry the analyzer found but couldn't pin to a declaration.
            # We can't rule out that it belongs to THIS decl, so we must
            # not return a confident "no sorry" below.
            saw_unattributed = True
            continue
        head, _, tail = in_decl.partition(' ')
        if tail and head in _DECL_KEYWORDS:
            in_decl = tail.strip()
        if in_decl == decl_name or in_decl == bare:
            return True
        if in_decl.endswith('.' + bare) or decl_name.endswith('.' + in_decl):
            return True
    if saw_unattributed:
        return None  # undecidable: an unattributed sorry exists in this file
    return False


def _file_compiles(lean_file: Path, project_path: Path) -> bool | None:
    """Best-effort compile check. Returns None if we can't decide.

    Uses `lake build <module>` rather than `lake env lean <file>` to
    ensure dependencies are built correctly, avoiding spurious
    missing-import failures.
    """
    try:
        # Convert path to module name: Foo/Bar.lean -> Foo.Bar
        rel = lean_file.relative_to(project_path)
        module = str(rel).replace('.lean', '').replace('/', '.')
        r = subprocess.run(
            ['lake', 'build', module],
            capture_output=True, text=True, timeout=300,
            cwd=project_path,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0


# ── tautological-body detection ───────────────────────────────────────


# Patterns that indicate a decl body is structurally a placeholder —
# kernel-clean and sorry-free, but the elaborated term is indistinguishable
# from a trivial witness for downstream rewriting / unfold purposes.
#
# Detected patterns (applied to the decl's textual body between ``:=`` and
# the next top-level declaration):
#
# * ``⟨Iso.refl _⟩`` (any namespace, any underscore arg) — the type is
#   forced to be ``X ≅ X`` (or a ``Nonempty`` of one), so ``\leanok`` on
#   the proof would falsely advertise the substantive iso the prose claims.
# * Top-level ``Classical.choice ⟨...⟩`` — the body discards its explicit
#   witness through the kernel-opaque choice operator; unfold doesn't see
#   it. The blueprint prose typically describes the witness, not "some
#   element of a nonempty set."
# * Top-level ``Nonempty.intro <ident>`` whose ident is a reflexivity
#   witness (``rfl``, ``Iso.refl _``, etc.).
# * Bodies that are exactly ``rfl`` for declarations whose type contains
#   ``Nonempty (... ≅ ...)`` — handled separately because the body alone
#   isn't enough; we'd need the type too. Skipped (too noisy without
#   elaborator help).
_TRIVIAL_BODY_RE = re.compile(
    r'(?:^|[\s:=]):=\s*(?:by\s+)?'
    r'(?:'
        r'⟨\s*[A-Za-z_][A-Za-z0-9_.]*\.refl\s+_?\s*⟩'   # ⟨Iso.refl _⟩
        r'|Classical\.choice\b'                          # Classical.choice ⟨...⟩
        r'|Nonempty\.intro\s+(?:Iso\.refl|rfl|HEq\.refl)\b'
    r')',
    re.MULTILINE,
)


def _decl_body_text(lean_file: Path, decl_name: str) -> str | None:
    """Return the textual body of ``decl_name`` in ``lean_file`` (best-effort).

    Returns the text between ``:=`` and the next top-level declaration (or
    end-of-file), trimmed. None when we can't locate the decl.

    The matcher is conservative: it locates the decl by the same pattern
    used in ``_scan_lean_decls`` and reads forward until the next line that
    looks like another top-level declaration. False negatives (None) are
    fine — they just mean we don't apply the trivial-body suppression.
    """
    try:
        text = lean_file.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return None
    lines = text.splitlines(keepends=False)
    bare = decl_name.rsplit('.', 1)[-1]
    # Find the line where the decl starts.
    decl_re = re.compile(
        r'^\s*(?:@\[[^\]]*\]\s*)*'
        r'(?:noncomputable\s+|private\s+|protected\s+|partial\s+|nonrec\s+'
        r'|unsafe\s+|opaque\s+|abbrev\s+)*'
        r'(?:theorem|lemma|def|abbrev|instance|example)'
        r'\s+' + re.escape(bare) + r'(?:\b|[\s({:.])',
    )
    start = None
    for i, line in enumerate(lines):
        if decl_re.match(line):
            start = i
            break
    if start is None:
        return None
    # Scan forward for the next top-level decl line.
    end = len(lines)
    next_decl_re = re.compile(
        r'^\s*(?:@\[[^\]]*\]\s*)*'
        r'(?:noncomputable\s+|private\s+|protected\s+|partial\s+|nonrec\s+'
        r'|unsafe\s+|opaque\s+|abbrev\s+)*'
        r'(?:theorem|lemma|def|abbrev|instance|example|class|structure|inductive)'
        r'\s+',
    )
    for j in range(start + 1, len(lines)):
        if next_decl_re.match(lines[j]):
            end = j
            break
    return '\n'.join(lines[start:end])


def _decl_body_is_trivial_placeholder(lean_file: Path, decl_name: str) -> bool:
    """True iff the decl's body matches a known trivial-placeholder pattern.

    See ``_TRIVIAL_BODY_RE`` for the patterns. Conservative: unknown shapes
    return False, so the regular ``\\leanok`` logic still fires. Only the
    explicitly-banned tautology patterns get suppressed.
    """
    body = _decl_body_text(lean_file, decl_name)
    if body is None:
        return False
    return bool(_TRIVIAL_BODY_RE.search(body))


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
    depth = 0  # unbalanced-brace depth carried across multi-line metadata
    for i, line in enumerate(lines):
        if depth > 0:
            # Continuation of a multi-line metadata macro (e.g. a `\uses{...}`
            # whose labels span several lines). Consume it whole — inserting
            # `\leanok` inside the brace group corrupts the cross-reference.
            depth += line.count('{') - line.count('}')
            leading = i + 1
            continue
        if line.strip() == '':
            leading = i + 1
            continue
        if metadata_re.match(line):
            depth += line.count('{') - line.count('}')
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
                        # Can't certify the proof is sorry-free (analyzer
                        # unavailable/timed out, or an unattributed sorry
                        # exists in the file). A proof-block \leanok asserts
                        # "proof closed, no sorry" — never let that claim
                        # stand unverified. Fail safe: remove it.
                        should_have = False
                        reason = (
                            f"sorry status undecided for {blk.lean_name}; "
                            f"removing proof \\leanok (fail-safe — a proof-"
                            f"complete claim must be positively verified)"
                        )
                    elif not has_sorry and _decl_body_is_trivial_placeholder(
                        lean_file, blk.lean_name,
                    ):
                        # Kernel-clean but body is structurally vacuous —
                        # one of the banned tautology placeholders (e.g.
                        # ``⟨Iso.refl _⟩`` on a ``Nonempty (X ≅ X)`` type,
                        # or ``Classical.choice ⟨X⟩``). Adding ``\leanok``
                        # here would falsely advertise the substantive
                        # proof the blueprint prose claims; abstain.
                        should_have = False
                        reason = (
                            f"body of {blk.lean_name} matches trivial-"
                            f"placeholder pattern; abstaining from "
                            f"\\leanok (would falsely advertise the "
                            f"substantive proof)"
                        )
                    else:
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


# ── parallel compile-check warm-up ────────────────────────────────────


def _default_jobs() -> int:
    """Worker count for the per-file compile-check sweep.

    Each worker runs one ``lake build <module>`` (a memory-heavy Lean
    process), so cap the default modestly rather than at all cores. Override
    with ``ARCHON_SYNC_LEANOK_JOBS``.
    """
    env = os.environ.get('ARCHON_SYNC_LEANOK_JOBS')
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, min(8, os.cpu_count() or 4))


def _collect_compile_targets(
    chapters: list[Path], decl_index: dict[str, Path],
) -> set[Path]:
    """The set of Lean files the chapters will compile-check.

    Mirrors :func:`_sync_chapter`'s in-scope filter (skip ``\\mathlibok`` /
    ``\\notready`` blocks and blocks with no resolvable ``\\lean{...}``) so
    we warm exactly the files the sequential pass would otherwise compile
    lazily — no more, no less.
    """
    targets: set[Path] = set()
    for tex in chapters:
        try:
            text = tex.read_text(encoding='utf-8')
        except OSError:
            continue
        for blk in _parse_chapter_blocks(text):
            if blk.has_mathlibok or blk.has_notready or blk.lean_name is None:
                continue
            lean_file = decl_index.get(blk.lean_name)
            if lean_file is not None:
                targets.add(lean_file)
    return targets


def _populate_compile_cache(
    files: set[Path], project_path: Path, *, jobs: int,
) -> dict[Path, bool | None]:
    """Compile-check ``files`` sequentially into a cache dict.

    While check individual files are independent, `lake build` uses a
    global lock. Running multiple `lake build` processes concurrently
    would cause lock contention and potential timeouts. We run them
    sequentially here; Lake's internal parallelism will still utilize
    available cores to build dependencies.
    """
    cache: dict[Path, bool | None] = {}
    if not files:
        return cache

    # Run checks sequentially to avoid Lake lock contention.
    # Parallelism is handled internally by Lake.
    for f in sorted(files):
        try:
            cache[f] = _file_compiles(f, project_path)
        except Exception:
            # One file blowing up (transient toolchain error, unexpected
            # raise) must not abort the whole warm-up sweep — record it as
            # undecided (None) and move on. _file_compiles already maps its
            # own OSError/SubprocessError to None; this is the outer backstop.
            cache[f] = None
    return cache


# ── CLI ───────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_path', nargs='?', default='.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print would-be changes; do not write.')
    parser.add_argument('--verbose', action='store_true',
                        help='Include no-op decisions in the output.')
    parser.add_argument('--format', choices=['text', 'json'], default='text')
    parser.add_argument(
        '--jobs', type=int, default=None,
        help='Parallel workers for the per-file compile-check sweep '
             '(default: min(8, cpu count), or $ARCHON_SYNC_LEANOK_JOBS). '
             'Use 1 to force serial.',
    )
    args = parser.parse_args(argv)

    project_path = Path(args.project_path).resolve()
    chapters_dir = project_path / 'blueprint' / 'src' / 'chapters'
    if not chapters_dir.is_dir():
        print(f'No blueprint chapters at {chapters_dir}', file=sys.stderr)
        return 0  # not a failure — just nothing to do

    decl_index = _scan_lean_decls(project_path)

    # Warm the per-file compile-check cache in parallel before the
    # sequential marker pass. On a large blueprint (dozens of chapters,
    # hundreds of referenced files) the serial `lake env lean` sweep is the
    # dominant cost and used to blow the phase timeout; doing the
    # independent checks concurrently cuts wall-clock by ~#workers.
    chapters = sorted(chapters_dir.glob('*.tex'))
    jobs = args.jobs if args.jobs is not None else _default_jobs()
    targets = _collect_compile_targets(chapters, decl_index)
    compile_cache: dict[Path, bool | None] = _populate_compile_cache(
        targets, project_path, jobs=jobs,
    )
    all_changes: list[Change] = []
    for tex in chapters:
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

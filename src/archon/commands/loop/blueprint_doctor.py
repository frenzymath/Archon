"""Blueprint-doctor: deterministic structural lints for the project blueprint.

Four classes of bug that the doctor catches deterministically — all
cheap to detect and dramatically more reliable than asking an LLM to
notice them every iter:

1. **Orphan chapters** — ``.tex`` files under ``blueprint/src/chapters/``
   that are NOT ``\\input`` 'd by ``blueprint/src/content.tex`` (directly
   or transitively). These never appear in the rendered output yet
   continue to consume reviewer attention.

2. **Broken cross-references** — ``\\ref{<label>}`` / ``\\eqref{...}`` /
   ``\\cref{...}`` / ``\\uses{...}`` etc. whose target label is not
   defined in any included chapter. These corrupt the dependency graph
   silently — leanblueprint will draw a broken edge or none at all.

3. **Malformed annotations** — ``\\uses{}`` / ``\\proves{}`` /
   ``\\label{}`` / ``\\ref{}`` with empty argument, plus empty list
   items inside otherwise-valid ``\\uses{a,,b}``. These crash plastex
   (``Label '' could not be resolved`` followed by a depgraph
   RecursionError) without ever reaching the LLM reviewer, so detecting
   them statically is the only way to keep them out of the loop.

4. **New axiom introductions** — ``axiom`` declarations under the
   project's ``.lean`` files (excluding ``.lake``/``.archon``). Archon's
   stance is "no new axioms"; finding any is a red flag that earns a
   review-phase callout.

The doctor is *informational*. It writes a Markdown report to
``.archon/logs/iter-NNN/blueprint-doctor.md`` and a structured JSON to
``.archon/logs/iter-NNN/blueprint-doctor.json``. The loop's phase wrapper
surfaces a one-line summary; nothing is mutated.

Run from the loop (between the prover and review phases, alongside
``sync_leanok``) so the review agent and the next iter's plan agent both
see a fresh report.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


_INPUT_RE = re.compile(r"\\(?:input|include)\s*\{\s*([^{}]+?)\s*\}")
# ``*`` (not ``+``) on the inner class so empty ``\label{}`` matches and
# is routed to ``malformed_refs``. The non-empty case continues to feed
# ``labels_defined`` unchanged.
_LABEL_RE = re.compile(r"\\label\s*\{\s*([^{}]*?)\s*\}")
# An `axiom` declaration at the start of a line (after optional whitespace
# and visibility modifiers). The doctor's caller filters out the audit's
# own snapshot tree, but we also guard against the keyword appearing in
# code comments by stripping single-line ``--`` and Lean block comments
# inside ``_count_axioms``.
_AXIOM_RE = re.compile(
    r'^\s*(?:@\[[^\]]*\]\s*)*'
    r'(?:private\s+|protected\s+|noncomputable\s+)*axiom\s+([\w.\']+)',
    re.MULTILINE,
)
# Directories pruned wholesale from the axiom scan — these mirror
# `sorry_analyzer.py`'s _NEVER_DESCEND plus the dep cache.
_AXIOM_SKIP_DIRS = {".archon", ".git", ".lake", "lake-packages"}


def _strip_tex_comments(text: str) -> str:
    """Strip LaTeX line comments from ``text``.

    A ``%`` begins a comment to end-of-line, UNLESS escaped as ``\\%``.
    LaTeX commenting also drops the trailing newline when the entire
    remainder of the line is a comment, but we keep the newline since
    we only care about which macros appear, not the rendered output.
    """
    out_lines: list[str] = []
    for line in text.splitlines():
        i = 0
        n = len(line)
        result_chars: list[str] = []
        while i < n:
            ch = line[i]
            if ch == "\\" and i + 1 < n:
                # Escaped char: copy both, never a comment starter.
                result_chars.append(ch)
                result_chars.append(line[i + 1])
                i += 2
                continue
            if ch == "%":
                # Rest of line is a comment.
                break
            result_chars.append(ch)
            i += 1
        out_lines.append("".join(result_chars))
    return "\n".join(out_lines)
# References that point at an existing label. ``\uses{a, b}`` may list
# multiple labels separated by commas; we split on commas and trim.
# All inner classes use ``*?`` (not ``+?``) so empty ``\ref{}`` /
# ``\uses{}`` etc. match and are routed to ``malformed_refs`` rather
# than silently dropped. Non-empty matches feed the broken-ref check
# unchanged.
_REF_RES = (
    re.compile(r"\\ref\s*\{\s*([^{}]*?)\s*\}"),
    re.compile(r"\\eqref\s*\{\s*([^{}]*?)\s*\}"),
    re.compile(r"\\cref\s*\{\s*([^{}]*?)\s*\}"),
    re.compile(r"\\Cref\s*\{\s*([^{}]*?)\s*\}"),
    re.compile(r"\\autoref\s*\{\s*([^{}]*?)\s*\}"),
    re.compile(r"\\uses\s*\{\s*([^{}]*?)\s*\}"),
    re.compile(r"\\proves\s*\{\s*([^{}]*?)\s*\}"),
)

# A literal standalone "REF" token in prose ("Definition~REF",
# "Sections REF–REF") — a writer pasted a placeholder instead of a
# resolvable \cref{<label>}. Matched with the optional preceding
# kind-word so the finding reads naturally.
_LITERAL_REF_RE = re.compile(
    r"(?:\b(?:Definition|Lemma|Theorem|Proposition|Corollary|Chapter|"
    r"Section|Remark|Equation|Step|Part)s?[~\s]+)?"
    r"(?<![A-Za-z\\{:_])REF(?![A-Za-z}_:])"
)


@dataclass
class DoctorReport:
    """Aggregate findings the loop summarizes + the phase commits."""
    chapters_dir: Path
    content_tex: Path
    chapters_present: list[Path] = field(default_factory=list)
    chapters_included: list[Path] = field(default_factory=list)
    orphan_chapters: list[Path] = field(default_factory=list)
    labels_defined: set[str] = field(default_factory=set)
    broken_refs: list[tuple[Path, str, str]] = field(default_factory=list)
    # (referencing_chapter, ref_kind, missing_label)
    malformed_refs: list[tuple[Path, str, str]] = field(default_factory=list)
    # (file, ref_kind, reason) — empty-argument annotations
    # (\uses{}/\proves{}/\label{}/\ref{}/...) and empty list items
    # (\uses{a,,b}). Reason is a short human-readable phrase such as
    # "empty argument" or "empty list item".
    axiom_decls: list[tuple[Path, str]] = field(default_factory=list)
    # (lean_file, decl_name) — every `axiom` found under project .lean
    covers_problems: list[tuple[str, str]] = field(default_factory=list)
    # (kind, detail) — `% archon:covers` integrity issues: a covered file
    # that doesn't exist, or a file claimed by more than one chapter.

    @property
    def has_findings(self) -> bool:
        return (
            bool(self.orphan_chapters)
            or bool(self.broken_refs)
            or bool(self.malformed_refs)
            or bool(self.axiom_decls)
            or bool(self.covers_problems)
        )

    def as_dict(self) -> dict:
        return {
            "chapters_dir": str(self.chapters_dir),
            "content_tex": str(self.content_tex),
            "chapters_present": [str(p) for p in sorted(self.chapters_present)],
            "chapters_included": [str(p) for p in sorted(self.chapters_included)],
            "orphan_chapters": [str(p) for p in sorted(self.orphan_chapters)],
            "labels_defined_count": len(self.labels_defined),
            "broken_refs": [
                {"chapter": str(c), "kind": k, "label": lbl}
                for c, k, lbl in sorted(self.broken_refs, key=lambda t: (str(t[0]), t[1], t[2]))
            ],
            "malformed_refs": [
                {"chapter": str(c), "kind": k, "reason": r}
                for c, k, r in sorted(self.malformed_refs, key=lambda t: (str(t[0]), t[1], t[2]))
            ],
            "axiom_decls": [
                {"file": str(f), "name": n}
                for f, n in sorted(self.axiom_decls, key=lambda t: (str(t[0]), t[1]))
            ],
            "covers_problems": [
                {"kind": k, "detail": d}
                for k, d in sorted(self.covers_problems)
            ],
        }


def _resolve_input_target(arg: str, src_dir: Path) -> Path | None:
    """Resolve a ``\\input{...}`` / ``\\include{...}`` argument to a Path.

    LaTeX accepts the argument with or without the ``.tex`` extension and
    interprets it relative to the document's source dir. Returns the
    resolved Path if it points at a file under ``src_dir`` that exists,
    otherwise None.
    """
    arg = arg.strip()
    if not arg:
        return None
    candidate = src_dir / arg
    if candidate.suffix != ".tex":
        candidate = candidate.with_suffix(".tex")
    if candidate.is_file():
        return candidate.resolve()
    # Some authors write ``\input{chapters/Foo}`` — already handled by the
    # relative-to-src_dir construction. Also support absolute-ish refs.
    return None


def _collect_inputs(start: Path, src_dir: Path) -> set[Path]:
    """Walk ``\\input`` / ``\\include`` from ``start`` recursively.

    Returns the set of resolved ``.tex`` files reachable from ``start``
    (including ``start`` itself when it exists). Cycles are tolerated by
    the visited-set.
    """
    visited: set[Path] = set()
    stack: list[Path] = [start.resolve()] if start.is_file() else []
    while stack:
        path = stack.pop()
        if path in visited:
            continue
        visited.add(path)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = _strip_tex_comments(text)
        for m in _INPUT_RE.finditer(text):
            resolved = _resolve_input_target(m.group(1), src_dir)
            if resolved is not None and resolved not in visited:
                stack.append(resolved)
    return visited


def _scan_labels_and_refs(
    tex_files: list[Path],
) -> tuple[set[str], list[tuple[Path, str, str]], list[tuple[Path, str, str]]]:
    """Return (defined-labels, raw-references, malformed) across the .tex files.

    * ``defined-labels``: every non-empty ``\\label{X}`` target. Used by
      the caller to filter ``raw-references`` into broken vs resolved.
    * ``raw-references``: ``(file, ref_kind, label)`` for every non-empty
      ``\\ref{...}`` / ``\\uses{...}`` / etc. ``\\uses{a, b}`` produces
      one entry per comma-separated label.
    * ``malformed``: ``(file, ref_kind, reason)`` for every empty
      annotation (``\\uses{}``, ``\\label{}``, ``\\ref{}``, ...) and
      every empty list item inside an otherwise-non-empty
      ``\\uses{a,,b}``. These never reach plastex as resolvable labels
      and crash its depgraph build; surfacing them here lets the
      reviewer fix them before the next ``leanblueprint web``.
    """
    labels: set[str] = set()
    refs: list[tuple[Path, str, str]] = []
    malformed: list[tuple[Path, str, str]] = []
    for tex in tex_files:
        try:
            text = tex.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = _strip_tex_comments(text)
        # Literal "REF" placeholders — a writer wrote prose like
        # "Definition~REF" instead of a resolvable \cref{<label>}. plasTeX
        # renders them as-is, littering the blueprint with dead references.
        for m in _LITERAL_REF_RE.finditer(text):
            malformed.append((
                tex, "literal-ref",
                f'literal "{m.group(0)}" placeholder — use \\cref{{<label>}}',
            ))
        for m in _LABEL_RE.finditer(text):
            piece = m.group(1).strip()
            if piece:
                labels.add(piece)
            else:
                malformed.append((tex, "label", "empty argument"))
        for ref_re in _REF_RES:
            kind = ref_re.pattern.split("\\\\")[1].split("\\")[0]
            kind = re.match(r"[A-Za-z]+", kind).group(0)  # type: ignore[union-attr]
            for m in ref_re.finditer(text):
                arg = m.group(1).strip()
                if not arg:
                    # \uses{} / \ref{} / etc. — empty body.
                    malformed.append((tex, kind, "empty argument"))
                    continue
                # \uses{a, b, c} → ["a", "b", "c"]; empty pieces from
                # `\uses{a,,b}` or `\uses{a,}` are also malformed.
                for piece in arg.split(","):
                    piece = piece.strip()
                    if piece:
                        refs.append((tex, kind, piece))
                    else:
                        malformed.append((tex, kind, "empty list item"))
    return labels, refs, malformed


def _scan_axiom_decls(project_path: Path) -> list[tuple[Path, str]]:
    """Find all ``axiom`` declarations under the project's .lean files.

    Skips the same directory set that ``sorry_analyzer.py`` excludes
    (``.archon`` / ``.git`` / ``.lake`` / ``lake-packages``). Comments
    are stripped before regex-matching to avoid false positives on
    docstrings or comment-out lines (`-- axiom foo : ...`).

    Returns ``[]`` when no axioms are present anywhere; the doctor
    surfaces axioms as a finding when this list is non-empty.
    """
    import os
    out: list[tuple[Path, str]] = []
    if not project_path.is_dir():
        return out
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in _AXIOM_SKIP_DIRS]
        for filename in files:
            if not filename.endswith(".lean"):
                continue
            lean = Path(root) / filename
            try:
                text = lean.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # Strip Lean line comments (``--`` to end-of-line) and
            # block comments (``/- ... -/``) before matching. The
            # regex doesn't anchor strictly enough to skip those
            # otherwise, and `-- axiom foo` inside a TODO would be a
            # false positive.
            stripped = _strip_lean_comments(text)
            for m in _AXIOM_RE.finditer(stripped):
                out.append((lean, m.group(1)))
    return out


def _strip_lean_comments(text: str) -> str:
    """Remove Lean ``--`` line comments and nested ``/- ... -/`` blocks.

    Conservative: when the parser can't decide (unterminated block, for
    example), errs on the side of keeping the original text. The
    output is used only as input to ``_AXIOM_RE`` so a slight over-
    keep is harmless — false positives still get the axiom name in the
    report, false negatives would silently miss real axioms.
    """
    result: list[str] = []
    i = 0
    n = len(text)
    block_depth = 0
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if block_depth > 0:
            if ch == "/" and nxt == "-":
                block_depth += 1
                i += 2
                continue
            if ch == "-" and nxt == "/":
                block_depth -= 1
                i += 2
                continue
            # Preserve newlines so line-based regexes still anchor.
            if ch == "\n":
                result.append("\n")
            i += 1
            continue
        if ch == "/" and nxt == "-":
            block_depth += 1
            i += 2
            continue
        if ch == "-" and nxt == "-":
            # Skip to end-of-line; keep the newline.
            while i < n and text[i] != "\n":
                i += 1
            continue
        result.append(ch)
        i += 1
    return "".join(result)


def _scan_covers_problems(project_path: Path) -> list[tuple[str, str]]:
    """Integrity check for ``% archon:covers`` declarations.

    Flags (a) a covers entry naming a Lean file that doesn't exist, and
    (b) a Lean file claimed by more than one chapter. Both are silent
    mis-mappings that would make the prover-dispatch gate consult the
    wrong chapter (or two), so they're worth surfacing deterministically.
    """
    from archon.commands.tooling.blueprint import chapter_coverage_map

    coverage = chapter_coverage_map(project_path)
    problems: list[tuple[str, str]] = []
    for slug, files in sorted(coverage.items()):
        for f in files:
            if not (project_path / f).is_file():
                problems.append((
                    "missing_file",
                    f"chapter `{slug}.tex` covers `{f}`, which does not exist",
                ))
    owners: dict[str, list[str]] = {}
    for slug, files in coverage.items():
        for f in files:
            owners.setdefault(f, []).append(slug)
    for f, slugs in sorted(owners.items()):
        if len(slugs) > 1:
            problems.append((
                "double_coverage",
                f"`{f}` is covered by multiple chapters: "
                + ", ".join(f"`{s}.tex`" for s in sorted(slugs)),
            ))
    return problems


def run_blueprint_doctor(project_path: Path) -> DoctorReport | None:
    """Run the blueprint doctor on ``project_path``.

    Returns ``None`` when the project has no blueprint to lint (no
    ``blueprint/src/`` directory) — the caller treats that as a silent
    skip. Returns a ``DoctorReport`` otherwise, possibly empty.

    The axiom scan runs regardless of blueprint presence: even a
    blueprint-less project benefits from "no new axioms" enforcement.
    A project with neither a blueprint nor any ``.lean`` files
    short-circuits to ``None``.
    """
    src_dir = project_path / "blueprint" / "src"
    chapters_dir = src_dir / "chapters"
    content_tex = src_dir / "content.tex"

    has_blueprint = src_dir.is_dir()

    chapters_present = sorted(p.resolve() for p in chapters_dir.glob("*.tex")) \
        if chapters_dir.is_dir() else []

    included = _collect_inputs(content_tex, src_dir) if has_blueprint else set()
    # Restrict the "included chapters" set to files under chapters_dir.
    chapters_included = sorted(
        p for p in included
        if chapters_dir in p.parents
    )
    chapters_included_set = set(chapters_included)
    orphan_chapters = sorted(
        p for p in chapters_present if p not in chapters_included_set
    )

    # For label/ref scanning, take the union of every transitively-included
    # .tex (which includes content.tex itself + any preamble files it
    # \inputs). This way \label{X} defined in a sub-included file is
    # available for cross-references in another chapter.
    label_scan_targets = sorted(included)
    labels, refs, malformed = _scan_labels_and_refs(label_scan_targets)

    broken: list[tuple[Path, str, str]] = []
    for tex, kind, lbl in refs:
        if lbl not in labels:
            broken.append((tex, kind, lbl))

    axiom_decls = _scan_axiom_decls(project_path)
    covers_problems = _scan_covers_problems(project_path) if has_blueprint else []

    if not has_blueprint and not axiom_decls:
        # Pure-Lean project with no blueprint AND no axioms — nothing
        # for the doctor to do. Skip so callers don't get an empty
        # report cluttering logs/iter-NNN/.
        return None

    return DoctorReport(
        chapters_dir=chapters_dir,
        content_tex=content_tex,
        chapters_present=chapters_present,
        chapters_included=chapters_included,
        orphan_chapters=orphan_chapters,
        labels_defined=labels,
        broken_refs=broken,
        malformed_refs=malformed,
        axiom_decls=axiom_decls,
        covers_problems=covers_problems,
    )


def write_reports(
    report: DoctorReport,
    iter_log_dir: Path,
    project_path: Path,
) -> tuple[Path, Path]:
    """Persist the doctor's findings as JSON + Markdown under iter-NNN/.

    Returns (json_path, markdown_path).
    """
    iter_log_dir.mkdir(parents=True, exist_ok=True)
    json_path = iter_log_dir / "blueprint-doctor.json"
    md_path = iter_log_dir / "blueprint-doctor.md"

    json_path.write_text(
        json.dumps(report.as_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(project_path))
        except ValueError:
            return str(p)

    lines: list[str] = ["# Blueprint Doctor", ""]
    if not report.has_findings:
        lines.append(
            "No structural findings: every chapter is `\\input`'d by "
            "`content.tex`, every `\\ref{...}` / `\\uses{...}` resolves to "
            "a defined `\\label{...}`, every annotation has a non-empty "
            "argument, and no `axiom` declarations are present under the "
            "project's `.lean` files."
        )
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return json_path, md_path

    if report.axiom_decls:
        lines.append("## Axiom declarations")
        lines.append("")
        lines.append(
            "Archon's stance is **no new axioms**. The declarations below "
            "appear as `axiom <name> : ...` under the project's `.lean` "
            "files. Resolve each one before the next iter — either remove "
            "the axiom (and supply a real proof), or, if the axiom is the "
            "mathematician's explicit boundary marker, mark it protected "
            "in `archon-protected.yaml` and document the rationale."
        )
        lines.append("")
        for f, n in sorted(report.axiom_decls, key=lambda t: (str(t[0]), t[1])):
            lines.append(f"- `{_rel(f)}` :: `{n}`")
        lines.append("")

    if report.covers_problems:
        lines.append("## Chapter coverage problems (`% archon:covers`)")
        lines.append("")
        lines.append(
            "A chapter's `% archon:covers <file> ...` declaration tells the "
            "prover-dispatch gate which Lean files that chapter blueprints. "
            "The issues below would route the gate to the wrong chapter — "
            "fix the declaration (correct the path, or make exactly one "
            "chapter own each file)."
        )
        lines.append("")
        for _kind, detail in sorted(report.covers_problems):
            lines.append(f"- {detail}")
        lines.append("")

    if report.orphan_chapters:
        lines.append("## Orphan chapters")
        lines.append("")
        lines.append(
            "These `.tex` files exist under `blueprint/src/chapters/` "
            "but are NOT reachable from `content.tex` via `\\input` "
            "(directly or transitively). They contribute nothing to the "
            "rendered blueprint and likely indicate either a forgotten "
            "`\\input{...}` line in `content.tex` or stale chapter "
            "files left behind by a refactor."
        )
        lines.append("")
        for p in report.orphan_chapters:
            lines.append(f"- `{_rel(p)}`")
        lines.append("")

    if report.malformed_refs:
        lines.append("## Malformed annotations")
        lines.append("")
        lines.append(
            "Annotations with an empty argument (`\\uses{}`, `\\proves{}`, "
            "`\\label{}`, `\\ref{}`, ...) or an empty list item "
            "(`\\uses{a,,b}`, `\\uses{a,}`). plastex emits "
            "`Label '' could not be resolved` for each of these and then "
            "the leanblueprint depgraph builder enters infinite recursion "
            "(`RecursionError`), so the blueprint never finishes building. "
            "Fix each one by either filling in the intended label or "
            "deleting the empty annotation. Do NOT defer — the next "
            "`leanblueprint web` run will crash until these are resolved."
        )
        lines.append("")
        # Group by (chapter, kind, reason) for readability.
        by_chapter_m: dict[Path, dict[tuple[str, str], int]] = {}
        for chapter, kind, reason in report.malformed_refs:
            by_chapter_m.setdefault(chapter, {}).setdefault((kind, reason), 0)
            by_chapter_m[chapter][(kind, reason)] += 1
        for chapter in sorted(by_chapter_m):
            lines.append(f"### `{_rel(chapter)}`")
            for (kind, reason), count in sorted(by_chapter_m[chapter].items()):
                suffix = f" ×{count}" if count > 1 else ""
                lines.append(f"- `\\{kind}{{...}}` — {reason}{suffix}")
            lines.append("")

    if report.broken_refs:
        lines.append("## Broken cross-references")
        lines.append("")
        lines.append(
            "These `\\ref{...}` / `\\uses{...}` / `\\cref{...}` (etc.) "
            "calls point at labels that no `\\label{...}` defines anywhere "
            "in the included tex tree. The dependency graph rendered by "
            "leanblueprint will draw a missing edge for each. Common causes: "
            "label typos (case mismatch, plural/singular), labels moved to "
            "an orphan chapter, or copy-paste of `\\uses{...}` lists that "
            "weren't updated when targets renamed."
        )
        lines.append("")
        # Group by (chapter, kind) for readability.
        by_chapter: dict[Path, dict[str, list[str]]] = {}
        for chapter, kind, label in report.broken_refs:
            by_chapter.setdefault(chapter, {}).setdefault(kind, []).append(label)
        for chapter in sorted(by_chapter):
            lines.append(f"### `{_rel(chapter)}`")
            for kind in sorted(by_chapter[chapter]):
                for label in sorted(set(by_chapter[chapter][kind])):
                    lines.append(f"- `\\{kind}{{{label}}}` — no matching `\\label`")
            lines.append("")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path

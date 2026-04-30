"""Wrapper around `leanblueprint`, plus chapter/structure helpers.

- `Blueprint`        — `leanblueprint` CLI wrapper (new / web / pdf / checkdecls)
- `BlueprintAnswers` — stdin answers for `leanblueprint new`
- `BlueprintServer`  — background `leanblueprint serve` process
- `BlueprintChapter` — read/write a single chapter .tex file
- `BlueprintStructure` — convert monolithic content.tex into \\input-based layout

The last two enable Archon's workflow where the plan agent writes proof
sketches into chapter files and the prover reads the chapter for its
assigned Lean file.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class BlueprintError(RuntimeError):
    def __init__(self, args: list[str], stdout: str, stderr: str, returncode: int):
        super().__init__(
            f"leanblueprint {' '.join(args)} exited with code {returncode}\n"
            f"stdout: {stdout.strip()}\nstderr: {stderr.strip()}"
        )
        self.args = args
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# ── CLI answers ───────────────────────────────────────────────────────


@dataclass
class BlueprintAnswers:
    """Answers fed to `leanblueprint new` on stdin, in order.

    Empty strings accept the tool's default. Values match the question
    order as of leanblueprint 0.0.18; if upstream changes order, update
    `to_stdin()`.
    """
    project_title: str = ""
    author: str = ""
    github_url: str = ""
    website_url: str = ""
    api_url: str = ""
    doc_class: str = ""          # default: report
    paper: str = ""              # default: a4paper
    show_hide_proofs: str = ""   # default: y
    toc_depth: str = ""          # default: 3
    split_file_level: str = ""   # default: 0
    per_file_toc_depth: str = ""  # default: 0
    proceed: str = "y"
    modify_lakefile_checkdecls: str = "y"
    modify_lakefile_docgen: str = "y"
    create_home_page: str = "n"
    configure_ci: str = "n"
    commit_to_git: str = "n"

    def to_stdin(self) -> str:
        ordered = [
            self.project_title, self.author, self.github_url,
            self.website_url, self.api_url, self.doc_class, self.paper,
            self.show_hide_proofs, self.toc_depth, self.split_file_level,
            self.per_file_toc_depth, self.proceed,
            self.modify_lakefile_checkdecls, self.modify_lakefile_docgen,
            self.create_home_page, self.configure_ci, self.commit_to_git,
        ]
        return "\n".join(ordered) + "\n"


# ── main CLI wrapper ──────────────────────────────────────────────────


class Blueprint:
    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).resolve()
        self.env = os.environ.copy()

    @staticmethod
    def available() -> bool:
        return shutil.which("leanblueprint") is not None

    def _run(
        self,
        args: list[str],
        input_str: str | None = None,
        check: bool = True,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["leanblueprint", *args],
            cwd=str(self.repo_path),
            env=self.env,
            input=input_str,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            raise BlueprintError(args, result.stdout, result.stderr, result.returncode)
        return result

    def is_initialized(self) -> bool:
        return (self.repo_path / "blueprint").is_dir()

    def initialize_new(self, answers: BlueprintAnswers | None = None, if_absent: bool = True) -> str:
        if self.is_initialized() and if_absent:
            return "Blueprint already initialized."
        answers = answers or BlueprintAnswers()
        return self._run(["new"], input_str=answers.to_stdin(), timeout=120).stdout.strip()

    def web(self) -> str:
        return self._run(["web"]).stdout.strip()

    def pdf(self) -> str:
        return self._run(["pdf"]).stdout.strip()

    def checkdecls(self) -> str:
        return self._run(["checkdecls"]).stdout.strip()

    def build_all(self) -> str:
        return self._run(["all"]).stdout.strip()


# ── background server ─────────────────────────────────────────────────


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _find_free_port(start: int, attempts: int = 20) -> int | None:
    for p in range(start, start + attempts):
        if _port_free(p):
            return p
    return None


class BlueprintServer:
    """Serves the built blueprint HTML as a background subprocess.

    `leanblueprint serve` does not accept a --port flag (as of v0.0.18 —
    it just cd's into blueprint/web and runs `python3 -m http.server`),
    so we do the equivalent ourselves. This gives us:

    - Control over the port (so it can run alongside the dashboard)
    - A stable way to start/stop the server via atexit
    - Graceful failure if the blueprint hasn't been built yet

    The server only serves content that `leanblueprint web` has already
    produced into blueprint/web/. If that directory doesn't exist yet,
    we refuse to start (the first iteration's finalize step will build it).
    """

    DEFAULT_PORT = 8000   # matches leanblueprint's default

    def __init__(self, project_path: str | Path, port_start: int = DEFAULT_PORT):
        self.project_path = Path(project_path).resolve()
        self.port_start = port_start
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None

    @property
    def web_dir(self) -> Path:
        return self.project_path / "blueprint" / "web"

    @property
    def available(self) -> bool:
        """True iff blueprint/ exists AND blueprint/web has been built.

        Note: we don't require leanblueprint on PATH here, because we
        serve the built HTML directly with python's http.server. The
        blueprint still needs to have been built at least once by
        `leanblueprint web` before this returns True.
        """
        return self.web_dir.is_dir() and any(self.web_dir.iterdir())

    @property
    def url(self) -> str | None:
        return f"http://localhost:{self.port}" if self.port else None

    def start(self) -> tuple[subprocess.Popen | None, int | None]:
        """Start python3 -m http.server in blueprint/web/. Returns (proc, port)."""
        if not self.available:
            return None, None

        port = _find_free_port(self.port_start)
        if port is None:
            return None, None
        self.port = port

        import sys as _sys
        try:
            proc = subprocess.Popen(
                [_sys.executable, "-m", "http.server", str(port),
                 "--bind", "127.0.0.1"],
                cwd=str(self.web_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            self.port = None
            return None, None

        # Give it a moment to bind; bail if it dies immediately.
        for _ in range(10):
            time.sleep(0.5)
            if not _port_free(port):
                break
            if proc.poll() is not None:
                self.port = None
                return None, None

        self.proc = proc
        return proc, port

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            try:
                import os as _os
                import signal as _signal
                _os.killpg(_os.getpgid(self.proc.pid), _signal.SIGTERM)
            except Exception:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
        self.proc = None


# ── chapter layout ────────────────────────────────────────────────────


# Slug used to map a Lean file path to a chapter .tex file.
# Algebra/WLocal.lean → Algebra_WLocal
def lean_file_to_chapter_slug(rel_lean_path: str | Path) -> str:
    rel = str(rel_lean_path)
    if rel.endswith(".lean"):
        rel = rel[:-5]
    return rel.replace("/", "_").replace(os.sep, "_").replace(".", "_")


_CHAPTER_TEMPLATE = r"""% Auto-generated chapter for {rel_lean}
% Archon reads and writes this file. The plan agent writes proof sketches
% here; the prover reads them and marks \leanok when formalization succeeds.

\chapter{{{chapter_title}}}
\label{{ch:{slug}}}

% ------------------------------------------------------------------------
% Overview
% Short paragraph (written by the plan agent) describing what this file
% contains mathematically.
% ------------------------------------------------------------------------

% Declarations from {rel_lean} will appear below. Each theorem/lemma gets
% a \begin{{theorem}} block that cites the Lean name via \lean{{name}} and
% a \begin{{proof}} block with the informal proof sketch.

"""


class BlueprintChapter:
    """One chapter = one Lean source file's informal content.

    Convention:
      Lean file  blueprint/src/chapters/<slug>.tex
      slug       slashes/dots replaced with underscores, .lean stripped

    Rationale: provers can read a small focused file for their goal
    instead of wading through a monolithic content.tex.
    """

    def __init__(self, project_path: str | Path, rel_lean_path: str | Path):
        self.project_path = Path(project_path).resolve()
        self.rel_lean_path = str(rel_lean_path).replace(os.sep, "/")
        self.slug = lean_file_to_chapter_slug(rel_lean_path)

    @property
    def chapter_dir(self) -> Path:
        return self.project_path / "blueprint" / "src" / "chapters"

    @property
    def path(self) -> Path:
        return self.chapter_dir / f"{self.slug}.tex"

    def exists(self) -> bool:
        return self.path.exists()

    def ensure_exists(self, chapter_title: str | None = None) -> bool:
        """Create the chapter file from a template if it's missing.

        Returns True if it was created, False if it already existed.
        """
        if self.exists():
            return False
        self.chapter_dir.mkdir(parents=True, exist_ok=True)
        title = chapter_title or self._default_title()
        self.path.write_text(
            _CHAPTER_TEMPLATE.format(
                rel_lean=self.rel_lean_path,
                chapter_title=title,
                slug=self.slug,
            ),
            encoding="utf-8",
        )
        return True

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8") if self.exists() else ""

    def _default_title(self) -> str:
        # Algebra/WLocal.lean → "WLocal (Algebra)"
        stem = self.rel_lean_path.split("/")[-1].removesuffix(".lean")
        parent = "/".join(self.rel_lean_path.split("/")[:-1])
        return f"{stem} ({parent})" if parent else stem


# ── multi-file structure ──────────────────────────────────────────────


_CONTENT_DISPATCHER = r"""% Archon manages this file. Chapters are \input-ed from chapters/ below.
% Edit chapters/*.tex directly; this dispatcher is regenerated on demand.

\input{{chapters/_preamble.tex}}

% --- Archon chapters begin ---
{chapter_inputs}
% --- Archon chapters end ---
"""

_CHAPTERS_PREAMBLE = r"""% Shared preamble for Archon-managed chapters.
% Put definitions, notation, and macros common to multiple chapters here.
"""

# Marker lines; used to detect whether a content.tex has already been
# converted into dispatcher form.
_BEGIN_MARKER = "% --- Archon chapters begin ---"
_END_MARKER = "% --- Archon chapters end ---"


class BlueprintStructure:
    """Convert a single-file blueprint into one file per Lean source.

    After `leanblueprint new` you get `blueprint/src/content.tex`. This class
    transforms that into a dispatcher that \\input's `chapters/*.tex`, one
    per Lean file in the project. Idempotent.
    """

    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path).resolve()

    @property
    def blueprint_src(self) -> Path:
        return self.project_path / "blueprint" / "src"

    @property
    def content_tex(self) -> Path:
        return self.blueprint_src / "content.tex"

    @property
    def chapters_dir(self) -> Path:
        return self.blueprint_src / "chapters"

    def is_available(self) -> bool:
        return self.content_tex.exists()

    def is_dispatcher(self) -> bool:
        """Return True iff content.tex has been converted."""
        if not self.content_tex.exists():
            return False
        text = self.content_tex.read_text(encoding="utf-8", errors="replace")
        return _BEGIN_MARKER in text and _END_MARKER in text

    def convert_to_dispatcher(
        self,
        chapter_slugs: list[str] | None = None,
        preserve_original: bool = True,
    ) -> bool:
        """Replace content.tex with a dispatcher that \\inputs chapter files.

        - `chapter_slugs`: chapter slugs to include. If None, include every
          `.tex` file currently in `chapters/` (minus `_preamble.tex`).
        - `preserve_original`: if content.tex contained non-trivial content
          before conversion, save it to `chapters/_legacy_content.tex` and
          add it to the dispatcher so nothing is silently dropped.

        Returns True if a conversion happened.
        """
        if not self.is_available():
            return False
        if self.is_dispatcher():
            # Already converted; just rewrite the chapter list.
            return self._rewrite_dispatcher(chapter_slugs)

        self.chapters_dir.mkdir(parents=True, exist_ok=True)

        preamble_path = self.chapters_dir / "_preamble.tex"
        if not preamble_path.exists():
            preamble_path.write_text(_CHAPTERS_PREAMBLE, encoding="utf-8")

        # Preserve any non-trivial existing content.tex.
        legacy_slug: str | None = None
        if preserve_original:
            original = self.content_tex.read_text(encoding="utf-8")
            if _looks_non_trivial(original):
                legacy = self.chapters_dir / "_legacy_content.tex"
                legacy.write_text(
                    "% Preserved from the original content.tex before "
                    "Archon restructured the blueprint.\n\n" + original,
                    encoding="utf-8",
                )
                legacy_slug = "_legacy_content"

        # Pick which chapters to include.
        if chapter_slugs is None:
            chapter_slugs = sorted(
                p.stem for p in self.chapters_dir.glob("*.tex")
                if not p.stem.startswith("_")
            )

        input_lines: list[str] = []
        if legacy_slug is not None:
            input_lines.append(f"\\input{{chapters/{legacy_slug}.tex}}")
        for s in chapter_slugs:
            input_lines.append(f"\\input{{chapters/{s}.tex}}")

        inputs_block = "\n".join(input_lines) if input_lines else "% (no chapters yet)"
        self.content_tex.write_text(
            _CONTENT_DISPATCHER.format(chapter_inputs=inputs_block),
            encoding="utf-8",
        )
        return True

    def _rewrite_dispatcher(self, chapter_slugs: list[str] | None) -> bool:
        """Update the \\input list in an existing dispatcher content.tex."""
        text = self.content_tex.read_text(encoding="utf-8")
        if chapter_slugs is None:
            chapter_slugs = sorted(
                p.stem for p in self.chapters_dir.glob("*.tex")
                if not p.stem.startswith("_")
            )
        input_lines = [f"\\input{{chapters/{s}.tex}}" for s in chapter_slugs]
        inputs_block = "\n".join(input_lines) if input_lines else "% (no chapters yet)"

        new_block = f"{_BEGIN_MARKER}\n{inputs_block}\n{_END_MARKER}"
        new_text = re.sub(
            re.escape(_BEGIN_MARKER) + r".*?" + re.escape(_END_MARKER),
            lambda _: new_block,
            text,
            flags=re.DOTALL,
            count=1,
        )
        if new_text != text:
            self.content_tex.write_text(new_text, encoding="utf-8")
            return True
        return False

    def ensure_chapters_for_lean_files(self, lean_files: list[Path]) -> list[str]:
        """Create a chapter file for each Lean file that doesn't already have one.

        Returns the list of chapter slugs that now exist.
        """
        slugs: list[str] = []
        for f in lean_files:
            try:
                rel = f.relative_to(self.project_path)
            except ValueError:
                continue
            chap = BlueprintChapter(self.project_path, rel)
            chap.ensure_exists()
            slugs.append(chap.slug)
        return sorted(set(slugs))


def _looks_non_trivial(tex: str) -> bool:
    """Return True if content.tex has user content worth preserving.

    `leanblueprint new` emits a nearly-empty content.tex with only comments
    and a single placeholder section. We treat anything with more than
    a handful of non-comment, non-blank lines as non-trivial.
    """
    real_lines = [
        l for l in tex.splitlines()
        if l.strip() and not l.strip().startswith("%")
    ]
    return len(real_lines) > 5
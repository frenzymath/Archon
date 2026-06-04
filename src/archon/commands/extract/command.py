"""ExtractCommand: sandbox duplication + interactive carve session + gate.

The flow `archon extract` orchestrates:

1. **Duplicate** (deterministic) — the parent project is copied into
   ``--dest`` (hardlinked ``.lake``, selective ``.archon``, fresh inner
   git). For a merge, the second project is never copied — it is mounted
   read-only by path and the session imports cones from it.
2. **Scope + carve** (interactive session) — a discuss-style session with
   the project context preloaded proposes seed declarations, computes the
   deterministic carve plan (``archon dag-carve-plan``), gets the user's
   explicit sign-off, then executes the plan: deletes ``drop`` files,
   operates on ``mixed`` files/chapters, keeps ``imported`` riders, adapts
   prose/state files, and records seeds+closure into the manifest.
3. **Verify gate** (deterministic) — the DAG must rebuild with zero broken
   ``\\uses{}``, all seeds present, and no closure node lost. Optionally
   ``lake build``. Only a passing sandbox is finalized (fresh outer git).
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import typer

from archon import log
from archon.agent import ClaudeAgent, ClaudeBackend, DEFAULT_MODEL

from .duplicate import DuplicateReport, duplicate_project
from .verify import read_manifest, verify_sandbox


def _read_if_exists(path: Path, max_chars: int = 3000) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) > max_chars:
        content = content[:max_chars] + "\n\n... (truncated)"
    return content


def _mathlib_rev(project: Path) -> str | None:
    """The pinned mathlib rev from lake-manifest.json (None if absent)."""
    import json
    mf = project / "lake-manifest.json"
    if not mf.is_file():
        return None
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        for pkg in data.get("packages", []):
            if pkg.get("name") == "mathlib":
                return pkg.get("rev")
    except (OSError, json.JSONDecodeError):
        return None
    return None


class ExtractCommand:
    """Run one `archon extract` (or merge-into-third-folder) flow."""

    def __init__(
        self,
        parent: str,
        dest: str,
        *,
        merge_source: str | None = None,
        lake_mode: str = "hardlink",
        resume: bool = False,
        build_check: bool = False,
        model: str = DEFAULT_MODEL,
        backend: ClaudeBackend | None = None,
    ) -> None:
        self.parent = Path(parent).resolve()
        self.dest = Path(dest).resolve()
        self.merge_source = Path(merge_source).resolve() if merge_source else None
        self.lake_mode = lake_mode
        self.resume = resume
        self.build_check = build_check
        self.model = model
        self.backend = backend

    # ── public ──────────────────────────────────────────────────────────

    def run(self) -> None:
        self._preflight()

        if self.resume:
            log.info(f"Resuming carve session in existing sandbox {self.dest}")
        else:
            report = duplicate_project(
                self.parent, self.dest,
                lake_mode=self.lake_mode,
                mode="merge" if self.merge_source else "extract",
                merge_source=self.merge_source,
            )
            self._announce_duplicate(report)

        prompt = self._build_prompt()
        log.info("Opening interactive extract session — Ctrl+C / exit to end")
        log.rule()
        try:
            ClaudeAgent(
                model=self.model, role="extract",
                backend=self.backend or ClaudeBackend(),
            ).run_interactive(prompt, cwd=self.dest)
        except KeyboardInterrupt:
            log.info("Session ended")

        self._gate_and_finalize()

    # ── private ─────────────────────────────────────────────────────────

    def _preflight(self) -> None:
        if not (self.parent / ".archon").is_dir():
            log.error(f"{self.parent} is not an archon project (no .archon/)")
            raise typer.Exit(1)
        if self.resume:
            if read_manifest(self.dest) is None:
                log.error(
                    f"--resume: {self.dest} has no extract manifest — "
                    f"run without --resume to create the sandbox first"
                )
                raise typer.Exit(1)
            return
        if self.dest.exists() and any(self.dest.iterdir()):
            log.error(f"Destination {self.dest} exists and is not empty "
                      f"(use --resume to re-enter an existing sandbox)")
            raise typer.Exit(1)
        if self.merge_source is not None:
            if not (self.merge_source / ".archon").is_dir():
                log.error(f"{self.merge_source} is not an archon project")
                raise typer.Exit(1)
            a, b = _mathlib_rev(self.parent), _mathlib_rev(self.merge_source)
            ta = _read_if_exists(self.parent / "lean-toolchain").strip()
            tb = _read_if_exists(self.merge_source / "lean-toolchain").strip()
            if ta != tb or (a or b) and a != b:
                log.error(
                    f"Projects are not mechanically mergeable: "
                    f"toolchain {ta!r} vs {tb!r}, mathlib rev {a!r} vs {b!r}. "
                    f"Align the pins first."
                )
                raise typer.Exit(1)

    def _announce_duplicate(self, report: DuplicateReport) -> None:
        log.header("Archon Extract — sandbox created")
        log.key_value({
            "Parent (read-only from here)": str(self.parent),
            "Sandbox": str(self.dest),
            "Files copied": str(report.files_copied),
            ".lake": ("hardlink-cloned" if report.lake_linked
                      else f"not linked ({self.lake_mode})"),
            "Merge source (read-only)": str(self.merge_source) if self.merge_source else "—",
            "Manifest": str(report.manifest_path),
        })

    def _build_prompt(self) -> str:
        state_dir = self.dest / ".archon"
        mode = "merge" if self.merge_source else "extract"
        progress = _read_if_exists(state_dir / "PROGRESS.md")
        strategy = _read_if_exists(state_dir / "STRATEGY.md")
        readme = _read_if_exists(self.dest / "README.md", max_chars=2000)

        prompt_file = state_dir / "prompts" / "extract.md"
        if prompt_file.is_file():
            body = prompt_file.read_text(encoding="utf-8")
        else:
            # Fall back to the packaged copy (sandbox inherited old prompts/).
            from archon.commands.init.utils import data_path
            packaged = data_path("prompts/extract.md")
            body = packaged.read_text(encoding="utf-8") if packaged.is_file() else ""

        merge_block = ""
        if self.merge_source:
            merge_block = textwrap.dedent(f"""\
            ## Merge source (READ-ONLY)

            This is a MERGE session: import material from
            `{self.merge_source}` into this sandbox. Query its graph with
            `archon dag-query <verb> --project-path {self.merge_source}` and
            compute import plans with
            `archon dag-carve-plan --project-path {self.merge_source} --node <seeds>`.
            You may READ any file there; you must NEVER write there.
            """)

        return textwrap.dedent(f"""\
        {body}

        # Session context (injected)

        - Mode: **{mode}**
        - Sandbox (your write domain): `{self.dest}`
        - Parent project (READ-ONLY): `{self.parent}`
        - Manifest: `.archon/extract-manifest.json`
        {merge_block}
        ## README.md (head)
        {readme or "(none)"}

        ## PROGRESS.md (inherited from parent — you will adapt this)
        {progress or "(none)"}

        ## STRATEGY.md (inherited from parent — you will adapt this)
        {strategy or "(none)"}
        """)

    def _gate_and_finalize(self) -> None:
        log.rule()
        log.info("Running deterministic verify gate…")
        res = verify_sandbox(self.dest, build=self.build_check)

        if res.stats:
            log.key_value({k: str(v) for k, v in res.stats.items()})
        if not res.ok:
            log.error("Verify gate FAILED — sandbox left as-is (inner git has "
                      "the session's commits):")
            for p in res.problems:
                log.step(p)
            log.info(f"Fix and re-enter: archon extract {self.parent} "
                     f"--dest {self.dest} --resume")
            raise typer.Exit(1)

        self._init_outer_git()
        log.success(
            f"Extract verified. Sandbox at {self.dest} is a standalone archon "
            f"project — run `archon dag` / `archon loop` there."
        )

    def _init_outer_git(self) -> None:
        """Fresh outer git history for the new project (idempotent)."""
        if (self.dest / ".git").exists():
            return
        try:
            for args in (
                ["git", "init", "-q"],
                ["git", "add", "-A"],
                ["git", "commit", "-q", "-m",
                 f"Initial commit: extracted from {self.parent.name}"],
            ):
                r = subprocess.run(args, cwd=self.dest, capture_output=True,
                                   text=True, timeout=120)
                if r.returncode != 0:
                    log.warn(f"outer git setup: `{' '.join(args)}` failed "
                             f"({r.stderr.strip()})")
                    return
            log.step("Initialized fresh outer git history")
        except Exception as e:
            log.warn(f"outer git setup skipped: {e}")

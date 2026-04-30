"""Project version stamping and comparison.

When `archon init` runs against a project, we stamp the CLI version into
`.archon/VERSION`. Every later command reads that file and compares against
the current CLI version; if they differ, we warn (but never block).

Same-version does not mean "no re-init needed" — prompts/skills can change
between releases without a version bump — so the warning helper also accepts
a hash-check hook for callers that want a finer-grained comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from archon import __version__ as _cli_version
from archon import log


VERSION_FILENAME = "VERSION"


@dataclass
class VersionStatus:
    """Result of comparing project version to CLI version."""
    cli_version: str
    project_version: str | None   # None if project is missing or the file doesn't exist
    state: str                    # "fresh" | "same" | "older" | "newer" | "missing"

    def should_warn(self) -> bool:
        return self.state in {"older", "newer", "missing"}

    def message(self) -> str:
        if self.state == "missing":
            return (
                f"Project has no {VERSION_FILENAME} file (initialized with an older Archon). "
                f"Consider re-initializing to pick up updated prompts and skills:\n"
                f"    archon init <path>   (then pick 'merge')"
            )
        if self.state == "older":
            return (
                f"Project was initialized with Archon {self.project_version}; "
                f"current CLI is {self.cli_version}.\n"
                f"Consider re-initializing to pick up updated prompts and skills:\n"
                f"    archon init <path>   (then pick 'merge')"
            )
        if self.state == "newer":
            return (
                f"Project was initialized with Archon {self.project_version}, "
                f"but the installed CLI is older ({self.cli_version}).\n"
                f"Upgrade the CLI to avoid using stale prompts."
            )
        return ""


class ProjectVersion:
    """Read/write the per-project Archon version stamp."""

    def __init__(self, project_path: str | Path):
        self.project_path = Path(project_path).resolve()
        self.state_dir = self.project_path / ".archon"

    # ── paths ────────────────────────────────────────────────────────────

    @property
    def version_file(self) -> Path:
        return self.state_dir / VERSION_FILENAME

    # ── read / write ─────────────────────────────────────────────────────

    def read(self) -> str | None:
        if not self.version_file.exists():
            return None
        try:
            return self.version_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def write(self, version: str | None = None) -> str:
        """Stamp the given version (or the installed CLI version) into .archon/VERSION."""
        v = version or _cli_version
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.version_file.write_text(v + "\n", encoding="utf-8")
        return v

    # ── comparison ───────────────────────────────────────────────────────

    def status(self) -> VersionStatus:
        cli = _cli_version
        if not self.state_dir.is_dir():
            return VersionStatus(cli, None, "fresh")

        proj = self.read()
        if proj is None:
            return VersionStatus(cli, None, "missing")

        if proj == cli:
            return VersionStatus(cli, proj, "same")

        # No strict semver — just compare tuples of integers when possible.
        try:
            pt = tuple(int(x) for x in proj.split("."))
            ct = tuple(int(x) for x in cli.split("."))
            if pt < ct:
                return VersionStatus(cli, proj, "older")
            if pt > ct:
                return VersionStatus(cli, proj, "newer")
            return VersionStatus(cli, proj, "same")
        except ValueError:
            # Non-numeric components (dev builds, pre-release tags, etc.)
            # — treat any mismatch as "older" to prompt re-init.
            return VersionStatus(cli, proj, "older")


# ── public helper ────────────────────────────────────────────────────────


def warn_if_mismatch(project_path: str | Path) -> VersionStatus:
    """Print a version-mismatch warning (never blocks). Returns the status."""
    status = ProjectVersion(project_path).status()
    if status.state == "fresh":
        # Not an Archon project yet — nothing to warn about.
        return status

    log.info(f"Archon CLI: {status.cli_version}   "
             f"Project: {status.project_version or '(missing)'}")

    if status.should_warn():
        log.warn(status.message())
    return status

"""Axiom sweep — catch ``sorryAx`` laundering the sorry count misses.

The headline sorry-count metric is WARNING-based (it parses the literal
``sorry`` token / Lean's sorry warning). That misses a real unsoundness
channel: a declaration that delegates to a ``sorry``-bearing lemma
compiles with NO sorry warning, yet carries ``sorryAx`` in its axiom
set. The loop can then report a stable/decreasing sorry count while the
unsound dependency surface silently grows through clean-compiling
delegates.

This module runs the bundled ``check_axioms_inline.sh`` — which
temporarily appends ``#print axioms`` to each project-source Lean file,
recompiles, and reverts (with backup/restore + an EXIT/INT/TERM trap) —
over the project's top-level declarations, and reports any that depend on
``sorryAx`` (and, secondarily, any other non-standard axiom). Generated
loop state under ``.archon/`` is excluded, especially historical
``logs/iter-NNN/snapshots/*.lean`` files that need not elaborate against
the current project imports.

Informational only: it never blocks the loop and never mutates project
state beyond the script's own backup/restore. It is comparatively
expensive (a per-file recompile), so the phase that calls it is gated
behind ``loop.axiom_sweep`` (off by default).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from archon import log


# The script emits, per offending declaration, a line of the form
#   ``⚠ <decl> uses non-standard axiom: <axiom>``
# wrapped in ANSI colour codes. We strip the colour first, then match.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_FINDING_RE = re.compile(
    r"⚠\s+(?P<decl>[A-Za-z0-9_.]+)\s+uses non-standard axiom:\s+"
    r"(?P<axiom>[A-Za-z0-9_.]+)"
)

# Generous: the sweep recompiles every file, so a large project can take
# many minutes. Still bounded so a hung ``lake`` can't wedge the loop.
DEFAULT_TIMEOUT_S = 1800


@dataclass(frozen=True)
class AxiomFinding:
    decl: str
    axiom: str

    @property
    def is_sorry(self) -> bool:
        # Lean's ``sorry`` elaborates to the ``sorryAx`` axiom.
        return self.axiom.lower().startswith("sorryax")


@dataclass
class AxiomSweepReport:
    findings: list[AxiomFinding] = field(default_factory=list)
    files_checked: int = 0
    ran: bool = False
    error: str | None = None
    duration_s: int = 0

    @property
    def sorry_launderings(self) -> list[AxiomFinding]:
        """Findings that depend on ``sorryAx`` — the laundering signal."""
        return [f for f in self.findings if f.is_sorry]

    @property
    def other_axioms(self) -> list[AxiomFinding]:
        return [f for f in self.findings if not f.is_sorry]

    @property
    def has_launderings(self) -> bool:
        return bool(self.sorry_launderings)


def _script_path() -> Path:
    """Locate the bundled ``check_axioms_inline.sh`` regardless of layout."""
    from archon.commands.init.utils import data_path
    return data_path("skills/lean4/lib/scripts/check_axioms_inline.sh")


def run_axiom_sweep(
    project_path: Path,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> AxiomSweepReport | None:
    """Run the axiom sweep over ``project_path``.

    Returns ``None`` when there's nothing to check (no Lean project /
    script missing) so the caller can silently skip. Otherwise returns
    an :class:`AxiomSweepReport`; ``report.error`` is set (and ``ran``
    is False) when the sweep couldn't complete.
    """
    # No lakefile → not a Lean project we can `lake env lean` against.
    has_lake = any(
        (project_path / name).exists()
        for name in ("lakefile.lean", "lakefile.toml")
    )
    if not has_lake:
        return None

    script = _script_path()
    if not script.exists():
        log.warn(f"check_axioms_inline.sh not found at {script}")
        return None

    import time
    start = time.monotonic()
    try:
        r = subprocess.run(
            ["bash", str(script), ".", "--report-only"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return AxiomSweepReport(
            ran=False,
            error=f"axiom sweep timed out after {timeout_s}s",
            duration_s=int(time.monotonic() - start),
        )
    except (OSError, subprocess.SubprocessError) as e:
        return AxiomSweepReport(ran=False, error=f"axiom sweep failed: {e}")

    secs = int(time.monotonic() - start)

    # ``--report-only`` makes the script exit 0 even when it finds custom
    # axioms; a non-zero code therefore means a real failure (e.g. a file
    # that doesn't compile for unrelated reasons).
    if r.returncode != 0:
        return AxiomSweepReport(
            ran=False,
            error=(
                f"check_axioms_inline.sh exited {r.returncode}: "
                f"{(r.stderr or '').strip()[:300]}"
            ),
            duration_s=secs,
        )

    clean = _ANSI_RE.sub("", r.stdout or "")
    findings = [
        AxiomFinding(decl=m.group("decl"), axiom=m.group("axiom"))
        for m in _FINDING_RE.finditer(clean)
    ]
    files_checked = sum(1 for line in clean.splitlines() if line.startswith("File:"))
    return AxiomSweepReport(
        findings=findings,
        files_checked=files_checked,
        ran=True,
        duration_s=secs,
    )


def write_reports(
    report: AxiomSweepReport,
    iter_log_dir: Path,
    project_path: Path,
) -> tuple[Path, Path]:
    """Write ``axiom-sweep.{json,md}`` to ``iter_log_dir``.

    Returns ``(json_path, md_path)``.
    """
    import json

    json_path = iter_log_dir / "axiom-sweep.json"
    md_path = iter_log_dir / "axiom-sweep.md"

    payload = {
        "ran": report.ran,
        "error": report.error,
        "filesChecked": report.files_checked,
        "durationSecs": report.duration_s,
        "sorryLaunderings": [
            {"decl": f.decl, "axiom": f.axiom} for f in report.sorry_launderings
        ],
        "otherNonStandardAxioms": [
            {"decl": f.decl, "axiom": f.axiom} for f in report.other_axioms
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = ["# Axiom sweep", ""]
    if not report.ran:
        lines += [f"Sweep did not complete: {report.error or 'unknown error'}.", ""]
    else:
        lines.append(
            f"Checked {report.files_checked} file(s) in {report.duration_s}s."
        )
        lines.append("")
        if report.has_launderings:
            lines += [
                "## ⚠ sorryAx laundering",
                "",
                "These declarations compile without a `sorry` warning but "
                "depend on `sorryAx` (a `sorry` reached through a clean-"
                "compiling delegate). The warning-based sorry count does "
                "NOT see them — treat them as open sorries.",
                "",
            ]
            for f in report.sorry_launderings:
                lines.append(f"- `{f.decl}` — depends on `{f.axiom}`")
            lines.append("")
        else:
            lines += ["No `sorryAx` laundering detected.", ""]
        if report.other_axioms:
            lines += ["## Other non-standard axioms", ""]
            for f in report.other_axioms:
                lines.append(f"- `{f.decl}` — `{f.axiom}`")
            lines.append("")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path

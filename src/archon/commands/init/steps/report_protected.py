"""Show the contents of `archon-protected.yaml` and remind the user to edit it."""

from __future__ import annotations

from archon import log
from archon.commands.tooling import protect

from .base import InitStep


_MAX_SHOWN = 20


class ReportProtectedStep(InitStep):
    """Always the last visible info phase, regardless of fresh-vs-merge.

    The user should leave the CLI knowing exactly which declarations
    agents will refuse to touch — or that the list is empty and they
    should go fill it in.
    """

    name = "Protected declarations"
    number = 8

    def run(self) -> None:
        ctx = self.ctx
        log.phase(self.number, self.name)
        ps = protect.load(ctx.project_path)

        if not ps.entries:
            log.info(
                f"{protect.PROTECTED_FILENAME} is empty — no declarations are "
                "currently protected from agent edits."
            )
            log.step(
                f"List declarations you want Archon to treat as read-only in "
                f"{protect.PROTECTED_FILENAME} at the project root. "
                "Agents will refuse to modify their signatures."
            )
            return

        total = ps.total_count()
        log.info(f"{total} protected declaration(s) across {len(ps.entries)} file(s):")
        shown = 0
        done = False
        for file, names in ps.entries.items():
            if done:
                break
            log.step(f"  [bold]{file}[/bold]")
            for n in names:
                log.step(f"    - {n}")
                shown += 1
                if shown >= _MAX_SHOWN:
                    done = True
                    break
        if total > shown:
            log.step(f"  ... and {total - shown} more")
        log.step(
            f"Edit {protect.PROTECTED_FILENAME} to add or remove entries. "
            "It is committed to git so the whole team shares it."
        )

"""Copy bundled prompt files into `.archon/prompts/`."""

from __future__ import annotations

from archon import log

from ..utils import _files_equal, copy_file, data_path
from .base import InitStep


class CopyPromptsStep(InitStep):
    name = "Copying prompts"
    number = 2

    def run(self) -> None:
        ctx = self.ctx
        log.phase(self.number, self.name)

        prompts_src = data_path("prompts")
        prompts_dst = ctx.state_dir / "prompts"
        prompts_dst.mkdir(parents=True, exist_ok=True)

        if not prompts_src.exists():
            log.error(f"Prompts directory not found at {prompts_src}")
            return

        new = 0
        preserved = 0
        for f in sorted(prompts_src.glob("*.md")):
            dst = prompts_dst / f.name
            # Whether fresh or not, a legacy symlink must be unlinked
            # before copy_file — shutil.copy2 follows symlinks and would
            # raise SameFileError when ``dst`` points back at ``f``.
            # (Repro: v0.1.0 → v0.2.0 upgrade with --force or "overwrite".)
            was_symlink = dst.is_symlink()
            if was_symlink:
                dst.unlink()
            if ctx.overwrite:
                # Skip the copy if the file is already identical and wasn't a
                # symlink being converted to a real file — no point rewriting it.
                if not was_symlink and dst.exists() and _files_equal(f, dst):
                    preserved += 1
                    continue
                copy_file(f, dst, overwrite=True)
                new += 1
                continue
            if dst.exists():
                preserved += 1
                continue
            copy_file(f, dst)
            new += 1

        if ctx.fresh:
            log.success(f"Copied {new} prompt(s)")
        elif ctx.overwrite:
            log.success(f"Overwrote {new} prompt(s)" + (f", skipped {preserved} unchanged" if preserved else ""))
        else:
            log.success(f"Added {new} new prompt(s), preserved {preserved} existing")

        # Warn about prompt files present locally but not in the bundled set.
        bundled_names = {f.name for f in prompts_src.glob("*.md")}
        for f in sorted(prompts_dst.glob("*.md")):
            if f.name not in bundled_names:
                log.warn(
                    f"  {f.name} is not part of this Archon version's default prompts. "
                    "You may have added it, or it was removed in a newer release — "
                    "safe to delete if you no longer need it."
                )

        self._copy_dir("prover-modes")

    def _copy_dir(self, sub: str) -> None:
        """Copy bundled ``sub/`` directory alongside prompts, with the same overwrite logic."""
        ctx = self.ctx
        src = data_path(sub)
        dst = ctx.state_dir / sub
        if not src.exists():
            return
        dst.mkdir(parents=True, exist_ok=True)
        new = 0
        preserved = 0
        for f in sorted(src.glob("*.md")):
            d = dst / f.name
            was_symlink = d.is_symlink()
            if was_symlink:
                d.unlink()
            if ctx.overwrite:
                if not was_symlink and d.exists() and _files_equal(f, d):
                    preserved += 1
                    continue
                copy_file(f, d, overwrite=True)
                new += 1
                continue
            if d.exists():
                preserved += 1
                continue
            copy_file(f, d)
            new += 1
        if ctx.fresh:
            log.success(f"Copied {new} {sub} file(s)")
        elif ctx.overwrite:
            log.success(f"Overwrote {new} {sub} file(s)" + (f", skipped {preserved} unchanged" if preserved else ""))
        else:
            log.success(f"Added {new} new {sub} file(s), preserved {preserved} existing")
        bundled = {f.name for f in src.glob("*.md")}
        for f in sorted(dst.glob("*.md")):
            if f.name not in bundled:
                log.warn(
                    f"  {f.name} is not part of this Archon version's default {sub}. "
                    "You may have added it, or it was removed in a newer release."
                )

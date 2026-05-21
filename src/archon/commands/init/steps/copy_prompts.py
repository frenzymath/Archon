"""Copy bundled prompt files into `.archon/prompts/`."""

from __future__ import annotations

from archon import log

from ..utils import copy_file, data_path
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
            if dst.is_symlink():
                dst.unlink()
            if ctx.fresh:
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
        else:
            log.success(f"Added {new} new prompt(s), preserved {preserved} existing")

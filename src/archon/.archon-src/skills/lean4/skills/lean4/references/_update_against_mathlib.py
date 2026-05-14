#!/usr/bin/env python3
"""Maintainer tool: refresh ``mathlib-unavailable-theorems.md`` against a real
Mathlib via a one-shot Claude session.

Why a script and not the loop:
  The reference file is curated content, not iteration state. It needs a
  human-reviewed refresh whenever Mathlib bumps in a way that invalidates a
  "not available" claim (Section 14's model-categories error is the canonical
  example). Building an automatic in-loop verifier would re-run on every
  iteration for zero per-iter benefit; better to keep this as an on-demand
  task the maintainer runs (and reviews the diff of) when bumping Mathlib.

What it does:
  Launches ``claude -p`` in a target Lean project's working tree (so the
  ``archon-lean-lsp`` MCP server attaches and the lean4 skill's tools are
  callable) and hands it a strict citation contract: every edit to the file
  must cite an LSP search hit, otherwise leave the claim alone. The session
  rewrites the file in place; the maintainer reviews ``git diff`` and lands.

Usage:
    python _update_against_mathlib.py <path-to-built-lean-project>

Preconditions for the target project:
  * ``lakefile.lean`` or ``lakefile.toml`` present
  * ``.lake/build/`` populated (otherwise the MCP LSP returns ``success: false``
    and the session can't verify anything; run ``lake build`` first)
  * ``archon init`` already run on the project so the lean4 plugin + MCP are
    wired in
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path


REFERENCE_FILE = Path(__file__).resolve().parent / "mathlib-unavailable-theorems.md"


# The contract is deliberately strict to avoid the failure mode the issue
# reporter flagged: an unconstrained Claude audit produced a list the reporter
# couldn't trust because it had no citations. Every edit here must be backed
# by an LSP / grep hit that gets recorded inline.
PROMPT_TEMPLATE = """You are auditing and updating the Archon reference file at:

    {ref_file_path}

against the Mathlib version vendored in this Lean project (cwd).

This is a maintainer refresh, not a loop iteration. Take your time. Be conservative.

## What the file is

The file lists topics whose "big-hammer" theorems should generally be AVOIDED
as default proof routes during autoformalization. Many entries say things
like "no definitions exist" or "absent from Mathlib". Mathlib evolves, so
some of those negative claims have become factually wrong (e.g. model
categories, which DO exist as `Mathlib.AlgebraicTopology.ModelCategory`).
Your job: find and fix factual errors, citing evidence.

## The contract — read this twice

For every NEGATIVE claim ("absent", "no definitions exist", "has not been
ported", "still incomplete", etc.):

1. Probe Mathlib using `mcp__archon-lean-lsp__lean_local_search` and/or
   `mcp__archon-lean-lsp__lean_leansearch` and/or `Grep` against the
   project's `.lake/packages/mathlib/Mathlib/` directory. Use multiple
   probes — try the obvious module path, the obvious typeclass name, and
   one related concept.

2. Based on the evidence:
   - **If you find the topic DOES exist in Mathlib**: rewrite the entry to
     say what's actually available (with the concrete file path or
     declaration name as evidence) and explain what's still hard /
     incomplete if anything is. Move on.
   - **If you confirm the claim is still correct**: leave it. You may add
     a parenthetical "(checked {today}, confirmed absent)" annotation.
   - **If your probes are ambiguous**: leave the claim alone and add a
     "TODO: re-verify, ambiguous probe results" comment beside it. Do NOT
     guess.

3. **No edit without a citation.** Every rewrite you make must include — in
   the file itself OR in your final summary — the LSP query you ran and a
   one-line excerpt of what it returned. If you can't cite, you can't edit.

4. Do not change the file's purpose. It's still meant to *discourage*
   topics that drag in heavy infrastructure. An entry that becomes "the
   definitions exist, but the API is thin and proofs of X depend on
   missing Y" is a valid fix; an entry that becomes "everything is fine
   here" is suspicious — double-check.

5. Update the header's "Last verified against: ..." line to today's date
   ({today}) and the current Mathlib version (look at
   `lake-manifest.json` in the project root).

## Output

When you are done editing the file in place, emit a final message
summarising:
  - Number of entries inspected
  - Number rewritten, with one line each: "§N <topic> — <one-line reason>"
  - Number left unchanged (confirmed still correct)
  - Number flagged with TODO (ambiguous probe results)

If you cannot edit the file for any reason (permissions, file not found,
etc.), stop and report — do not invent fixes.
"""


def _mathlib_version(project: Path) -> str:
    manifest = project / "lake-manifest.json"
    if not manifest.exists():
        return "unknown"
    try:
        data = json.loads(manifest.read_text())
    except Exception:
        return "unknown"
    for pkg in data.get("packages", []):
        if pkg.get("name") == "mathlib":
            rev = pkg.get("rev", "")
            return rev[:12] if rev else "unknown"
    return "unknown"


def _is_lake_project(p: Path) -> bool:
    return (p / "lakefile.lean").exists() or (p / "lakefile.toml").exists()


def _is_built(p: Path) -> bool:
    build = p / ".lake" / "build"
    return build.is_dir() and any(build.iterdir())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "project", type=Path,
        help="Path to a built Lean project with Mathlib available "
             "(must have .lake/build populated)",
    )
    parser.add_argument(
        "--model", default="opus",
        help="Claude model alias to use (default: opus)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the prompt that would be sent and exit; do not invoke claude",
    )
    args = parser.parse_args(argv)

    project = args.project.resolve()
    if not _is_lake_project(project):
        print(f"error: {project} is not a Lake project (no lakefile.lean/toml)", file=sys.stderr)
        return 2
    if not _is_built(project):
        print(
            f"warning: {project}/.lake/build is empty — the LSP MCP server "
            f"will likely return success: false. Run `lake build` in the "
            f"project first, then re-run this script.",
            file=sys.stderr,
        )
        # Don't hard-fail; let the maintainer override if they know what they're doing.
    if not REFERENCE_FILE.exists():
        print(f"error: reference file missing: {REFERENCE_FILE}", file=sys.stderr)
        return 2

    today = datetime.date.today().isoformat()
    prompt = PROMPT_TEMPLATE.format(
        ref_file_path=REFERENCE_FILE,
        today=today,
    )

    if args.dry_run:
        print(f"# Would invoke claude in: {project}")
        print(f"# Mathlib version (lake-manifest): {_mathlib_version(project)}")
        print("# Prompt:")
        print(prompt)
        return 0

    # Inline import so a `--dry-run --help` invocation works without the
    # full archon stack installed.
    try:
        from archon.agent import ClaudeAgent
    except ImportError:
        print(
            "error: cannot import archon.agent. Run this script from within "
            "an editable install of Archon (e.g. `pip install -e .` from "
            "the repo root).",
            file=sys.stderr,
        )
        return 2

    log_base = project / ".archon" / "logs" / f"update-unavailable-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    log_base.parent.mkdir(parents=True, exist_ok=True)

    print(f"[update-unavailable] Project:  {project}")
    print(f"[update-unavailable] Mathlib:  {_mathlib_version(project)}")
    print(f"[update-unavailable] File:     {REFERENCE_FILE}")
    print(f"[update-unavailable] Log base: {log_base}.jsonl")
    print(f"[update-unavailable] Launching claude…")

    agent = ClaudeAgent(model=args.model, role="update-unavailable")
    ok = agent.run(
        prompt,
        cwd=project,
        log_base=log_base,
        verbose_logs=False,
        idle_timeout_s=None,  # no watchdog — this is a manual maintenance task
        max_attempts=1,
    )

    if not ok:
        print("[update-unavailable] claude session failed — see log", file=sys.stderr)
        return 1

    # Show what changed so the maintainer doesn't have to remember to diff.
    try:
        r = subprocess.run(
            ["git", "diff", "--stat", "--", str(REFERENCE_FILE)],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parents[6],
        )
        if r.stdout.strip():
            print()
            print("[update-unavailable] git diff --stat:")
            print(r.stdout)
            print("Review the diff with `git diff` before committing.")
        else:
            print("[update-unavailable] no changes written to the file.")
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

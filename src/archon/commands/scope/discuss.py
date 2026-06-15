"""``archon scope discuss`` — an interactive session about a whole scope.

The deterministic roadmap (unblock matrix, duplication, leverage) is the
backbone; this is the soft layer on top. The session opens with the rendered
roadmap pre-loaded and reasons across projects: what to work next, what to
extract into a shared base, where to leave a peer a definition note. It is
read-only to the member projects — its only writes are, with your consent,
leaving inbox notes (``archon peers note``) or proposing concrete
``archon extract`` / ``archon merge`` invocations for you to run.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Optional

import typer

from archon import log
from archon.agent import ClaudeBackend, DEFAULT_MODEL, build_runner
from archon.commands.tooling.project_config import (
    load_project_config,
    resolve_claude_backend,
)

from . import resolve as scope_mod
from .roadmap import build_roadmap, render_markdown


def _build_prompt(root: Path, roadmap_md: str, members: list) -> str:
    member_lines = "\n".join(
        f"- **{m.name}** — `{m.path}`" + ("" if m.has_dag else "  *(no DAG built yet)*")
        for m in members
    )
    return textwrap.dedent(f"""\
You are an Archon **scope advisor**. A *scope* is a set of related Archon
Lean-formalization projects; the mathematician wants to understand their joint
progress and decide what to do next across all of them.

Scope directory: {root}
Member projects:
{member_lines}

## Rules

### Read-only to the members, with two consented exceptions
- **NEVER edit any member project's files** (`.lean`, blueprint, state) without
  explicit per-request consent.
- **NEVER run `archon loop`, `archon init`, `lake build`, or any graph-mutating
  command** against a member.
- You MAY, after stating exactly what and why and getting a "yes":
  - leave a peer a definition note —
    `archon peers note --target <member> --lean-name <X> --suggestion "..." --rationale "..."`
    (when one project's declaration should be reshaped to serve the others);
  - propose a concrete `archon extract` / `archon merge` invocation for the
    mathematician to run (you draft the exact command; you do not run it).

### What you CAN always do (no consent)
- **Read any member file** (their `.lean`, `blueprint/src/**.tex`, `.archon/` state).
- **Walk any member's cached graph read-only**:
  `archon dag-query <verb> --project-path <member> --json`
  (verbs: frontier, node, interface, cone, overlap, unproved, gaps, …).
- **Re-run the deterministic analysis**: `archon scope roadmap --path {root}`
  (and `archon scope graph` for the union merge DAG JSON). Prefer these numbers
  over eyeballing — they are the ground truth for who-unblocks-whom.

## Pre-loaded roadmap (deterministic — your starting point)

{roadmap_md}

## How to behave
1. **Open with the leverage picture**: which project, worked next, unblocks the
   most across the others — and why (name the shared declarations).
2. **Surface duplicated work**: declarations being proved in parallel across
   projects. For a cohesive cluster needed by ≥2 projects, propose extracting it
   into a shared base project they all peer-read — draft the `archon extract`.
3. **Drive convergence**: when projects re-prove the *same* declaration in
   different shapes, that is a generalization signal — offer to leave the owner
   a `peers note` so the definition converges.
4. **Be concrete and honest**: cite member names and Lean names; verify a claim
   with `dag-query` before relying on it; if two projects' same-named decls have
   different statements, flag the divergence rather than assuming reuse is safe.
5. **End with a ranked, actionable plan** across the scope — ordered by leverage,
   with the exact commands to run.""")


def scope_discuss(
    path: str = typer.Option(".", "--path", help="The scope directory"),
    focus: Optional[str] = typer.Option(
        None, "--focus", "-f",
        help="Focus the session, e.g. a member name or a shared declaration.",
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-M",
        help="Model alias: 'opus' (default), 'sonnet', 'haiku', or a full id.",
    ),
    claude_backend: Optional[str] = typer.Option(
        None, "--claude-backend", help="How 'claude -p' is launched (see `archon discuss`).",
    ),
    harness: Optional[str] = typer.Option(
        None, "--harness", help="Override the interactive session harness, e.g. codex.",
    ),
) -> None:
    """Open an interactive session about the whole scope (global progress, merges, extractions)."""
    root = Path(path).resolve()
    if not scope_mod.is_scope(root):
        log.error(f"No {scope_mod.SCOPE_PEERS_FILE} at {root} — not a scope. "
                  f"Run `archon scope init` here first.")
        raise typer.Exit(1)

    members = scope_mod.resolve_members(root)
    if not members:
        log.error("No member Archon projects in scope — edit peers.yaml first.")
        raise typer.Exit(1)

    rm, _ = build_roadmap(members)
    roadmap_md = render_markdown(rm)
    if focus:
        roadmap_md = f"FOCUS: the mathematician wants to discuss **{focus}**.\n\n" + roadmap_md

    prompt = _build_prompt(root, roadmap_md, members)

    log.header("Archon Scope Discuss")
    log.key_value({
        "Scope": str(root),
        "Members": ", ".join(m.name for m in members),
        "Focus": focus or "(global)",
        "Writable": "peer inbox notes + proposed extract/merge, with consent",
    })
    log.info("Starting interactive session — Ctrl+C to exit")
    log.rule()

    cfg = load_project_config(root)
    backend = resolve_claude_backend(cfg, cli_value=claude_backend)
    try:
        build_runner(
            role="discuss", model=model, cfg=cfg,
            harness=harness, backend=backend or ClaudeBackend(),
        ).run_interactive(prompt, cwd=root)
    except KeyboardInterrupt:
        log.info("Discussion ended")

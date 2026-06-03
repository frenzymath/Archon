"""Typer-decorated `dag` entry point.

Kept thin: parse CLI flags, resolve them against `.archon/config.json`,
build a `DagOptions`, and call `DagCommand.run()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from archon.agent import DEFAULT_MODEL

from .command import DagCommand
from .context import DagOptions


def dag(
    project_path: str = typer.Argument(".", help="Path to Lean project"),
    max_iterations: Optional[int] = typer.Option(
        None, "--max-iterations", "-m",
        help="Max elaboration cycles. (default from .archon/config.json or 4)",
    ),
    max_parallel: Optional[int] = typer.Option(
        None, "--max-parallel",
        help="Max concurrent subagents. (default from config or 4)",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-M",
        help="Model used for the DAG elaboration agent. "
             "Anthropic aliases: 'opus', 'sonnet', 'haiku' or a full id. "
             "(default from .archon/config.json or 'opus')",
    ),
    lean_aware: Optional[bool] = typer.Option(
        None, "--lean-aware/--no-lean-aware",
        help="Read .lean files to discover declarations needing blueprints. "
             "(default from config or on)",
    ),
    verbose_logs: Optional[bool] = typer.Option(
        None, "--verbose-logs/--no-verbose-logs",
        help="Save raw Claude stream events to .raw.jsonl. (default from config or off)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print prompts without launching Claude.",
    ),
    no_dashboard: bool = typer.Option(
        False, "--no-dashboard",
        help="Do not auto-start the web dashboard.",
    ),
    blueprint_server_flag: bool = typer.Option(
        False, "--blueprint-server",
        help="Start a local HTTP server serving blueprint/web/ alongside the dashboard.",
    ),
    open_browser: bool = typer.Option(
        False, "--open",
        help="Open the dashboard in a browser as soon as it starts.",
    ),
    claude_backend: Optional[str] = typer.Option(
        None, "--claude-backend",
        help=(
            "How 'claude -p' is invoked. "
            "'default': plain claude -p. "
            "'vscode': sets CLAUDE_CODE_ENTRYPOINT=claude-vscode. "
            "'desktop': sets CLAUDE_CODE_ENTRYPOINT=claude-desktop. "
            "(default from .archon/config.json loop.claude_backend or 'default')"
        ),
    ),
    claude_p_config_dir: Optional[str] = typer.Option(
        None, "--claude-p-config-dir",
        help=(
            "CLAUDE_CONFIG_DIR to use when --claude-backend=claude-p. "
            "(default from .archon/config.json loop.claude_p_config_dir "
            "or the CLAUDE_CONFIG_DIR env var)"
        ),
    ),
) -> None:
    """Elaborate a full informal blueprint (dependency graph) for the project.

    Runs an LLM agent that reads the project scope (Lean stubs, goal
    description, references) and writes complete blueprint chapters with
    \\uses{} dependency annotations and source citations. Run this before
    `archon loop` to give provers a complete mathematical roadmap.

    Each iteration:
      1. DAG elaboration agent (reads scope, dispatches blueprint-writer /
         blueprint-reviewer / reference-retriever subagents)
      2. Blueprint doctor (structural lint: broken \\uses{}, orphan chapters)

    Stops when the agent writes `## Status: COMPLETE` in DAG_STATUS.md,
    or when --max-iterations is reached.
    """
    resolved = Path(project_path).resolve()

    from archon.commands.tooling.env_loader import load_env_file as _load_env
    from archon.commands.tooling.project_config import (
        load_project_config,
        resolve as _resolve,
        resolve_claude_backend,
    )

    project_config = load_project_config(resolved)
    loop_cfg = project_config.loop_section()

    _load_env(resolved)

    max_iterations = _resolve(max_iterations, section=loop_cfg, key='max_iterations', default=4)
    max_parallel = _resolve(max_parallel, section=loop_cfg, key='max_parallel', default=4)
    model = _resolve(model, section=loop_cfg, key='model', default=DEFAULT_MODEL)
    verbose_logs = _resolve(verbose_logs, section=loop_cfg, key='verbose_logs', default=False)
    lean_aware = True if lean_aware is None else lean_aware
    backend = resolve_claude_backend(
        project_config,
        cli_value=claude_backend,
        claude_p_config_dir=claude_p_config_dir,
    )

    options = DagOptions(
        project_path=resolved,
        max_iterations=max_iterations,
        max_parallel=max_parallel,
        model=model,
        verbose_logs=verbose_logs,
        dry_run=dry_run,
        no_dashboard=no_dashboard,
        blueprint_server_flag=blueprint_server_flag,
        open_browser=open_browser,
        lean_aware=lean_aware,
        backend=backend,
    )

    DagCommand(options).run()

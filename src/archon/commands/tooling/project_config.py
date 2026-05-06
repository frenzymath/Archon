"""Per-project ``.archon/config.json`` defaults for CLI commands.

The motivation is making ``archon loop`` runnable as one word in the
common case. Anything you'd otherwise pass as a CLI flag — number of
iterations, model alias, parallelism, multilane lanes — can live in
``config.json`` and be resolved with this precedence:

    CLI flag  >  .archon/config.json  >  built-in default

To cooperate cleanly with typer, every option that wants the project
config as a fallback should default to ``None`` in the typer
signature. ``resolve(...)`` then returns the first non-None value
walking the precedence chain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONFIG_FILENAME = 'config.json'


def config_path(project_path: Path) -> Path:
    return project_path / '.archon' / CONFIG_FILENAME


# ── default schema ────────────────────────────────────────────────────


def default_config() -> dict[str, Any]:
    return {
        'loop': {
            'max_iterations': 10,
            'parallel': True,
            'max_parallel': 4,
            'model': 'opus',
            'verbose_logs': False,
            'no_review': False,
        },
        'multilane': {
            # JSON has no real comments; ``_help`` / ``_env`` /
            # ``_examples`` keys with leading underscores are ignored by
            # the multilane config builder but readable by humans.
            # See .archon/MULTILANE.md for more.
            '_help': (
                "Set 'enabled' to true and add the lanes you want. "
                "Each lane uses Claude Code as its driver but routes "
                "requests to a different provider via ANTHROPIC_BASE_URL "
                "(set in .archon/.env)."
            ),
            'enabled': False,
            'base_ref': 'main',
            # Minutes other lanes keep running on a file after one lane
            # finishes it cleanly. Set 0 to cancel slow lanes immediately.
            # The default (10 min) gives a slower provider a chance to
            # land its own version for the merge agent to consider.
            'grace_minutes': 10,
            'lanes': [
                # Default: a single Anthropic lane. Multilane stays
                # disabled until ``enabled`` is flipped to true.
                {
                    'lane_id': 'anthropic',
                    'provider': 'anthropic',
                    'model': 'opus',
                    '_env': "Anthropic auth is handled by Claude Code itself (interactive login during `archon init`). No env vars needed.",
                },
            ],
            '_examples': {
                "_help": (
                    "Copy any of these into the 'lanes' list above to enable. "
                    "Set the keys named in '_env' inside .archon/.env first."
                ),
                "kimi": {
                    'lane_id': 'kimi',
                    'provider': 'moonshot',
                    '_env': "Set MOONSHOT_API_KEY in .archon/.env (optional: MOONSHOT_BASE_URL, MOONSHOT_MODEL).",
                    '_extras': "No extras package needed — Moonshot speaks the Anthropic API natively.",
                },
                "deepseek": {
                    'lane_id': 'deepseek',
                    'provider': 'deepseek',
                    '_env': "Set DEEPSEEK_API_KEY in .archon/.env (optional: DEEPSEEK_BASE_URL, DEEPSEEK_MODEL).",
                    '_extras': "No extras package needed — DeepSeek speaks the Anthropic API natively.",
                },
            },
        },
    }


def render_default_config() -> str:
    return json.dumps(default_config(), indent=2) + '\n'


# ── load / write ──────────────────────────────────────────────────────


def write_default_config(project_path: Path, *, force: bool = False) -> bool:
    """Create .archon/config.json if missing. Returns True iff a new file was written."""
    path = config_path(project_path)
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_default_config(), encoding='utf-8')
    return True


@dataclass
class ProjectConfig:
    """Parsed view of ``.archon/config.json``."""
    raw: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None

    def loop_section(self) -> dict[str, Any]:
        return dict(self.raw.get('loop') or {})

    def multilane_section(self) -> dict[str, Any]:
        return dict(self.raw.get('multilane') or {})


def load_project_config(project_path: Path) -> ProjectConfig:
    """Read the config file. Returns an empty ProjectConfig if missing."""
    path = config_path(project_path)
    if not path.exists():
        return ProjectConfig()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return ProjectConfig(source_path=path)
    if not isinstance(data, dict):
        return ProjectConfig(source_path=path)
    return ProjectConfig(raw=data, source_path=path)


# ── precedence resolution ─────────────────────────────────────────────


def resolve(cli_value: Any, *, section: dict[str, Any], key: str, default: Any) -> Any:
    """Pick the first non-None value: CLI > config-file > default.

    Use this in typer command bodies to merge a typer-provided
    ``Optional[T] = None`` with a config-file fallback. Booleans and
    integers behave correctly because we only treat ``None`` as
    "unspecified" — explicit ``False`` and ``0`` from the CLI win.
    """
    if cli_value is not None:
        return cli_value
    if key in section and section[key] is not None:
        return section[key]
    return default

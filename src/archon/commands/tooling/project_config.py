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
            'enabled': False,
            'base_ref': 'main',
            'lanes': [
                # Default: a single Anthropic lane. Multilane stays
                # disabled until the user flips ``enabled: true``, so
                # this is purely an example shape.
                {
                    'lane_id': 'anthropic',
                    'provider': 'anthropic',
                    'model': 'opus',
                },
            ],
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

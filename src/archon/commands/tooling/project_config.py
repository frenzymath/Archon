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
            '_model_help': (
                "Model alias used by the plan / prover / review agents. "
                "Anthropic aliases: 'opus', 'sonnet', 'haiku' or any full "
                "model id. Non-Anthropic providers: 'kimi' or 'deepseek' "
                "— these require the matching credentials in .archon/.env "
                "(MOONSHOT_API_KEY, DEEPSEEK_API_KEY). No settings file is "
                "written to disk: env vars are injected into each "
                "subprocess only."
            ),
            'max_iterations': 10,
            'parallel': True,
            'max_parallel': 4,
            'model': 'opus',
            'verbose_logs': False,
            'no_review': False,
            '_debug_feedback_help': (
                "Open a write-only feedback channel: each agent and "
                "subagent is told (in its prompt) that it may append "
                "short observations to "
                ".archon/.debug-feedback/debug_feedback.md when it notices "
                "a missing capability, a contradictory instruction, or "
                "anything else the developer should fix. Agents are told "
                "never to read the file. Off by default; flip to true "
                "while you are iterating on Archon itself."
            ),
            'debug_feedback': False,
        },
        'subagents': {
            '_help': (
                "Per-subagent model overrides. Set to a model alias "
                "('opus', 'sonnet', 'haiku', 'kimi', 'deepseek') or a "
                "full model id. Null falls back to 'loop.model'."
            ),
            'refactor': None,
            'analogy': None,
            'challenger': None,
        },
        'compaction': {
            '_help': (
                "Pre-agent compaction of large state files (STRATEGY.md, "
                "task_pending.md, task_done.md, PROJECT_STATUS.md) before "
                "the agent that reads them. Compactors shrink each file "
                "in place via incremental Edit calls, preserving every "
                "actionable detail (errors, dead ends, recent iterations) "
                "and trimming only old narrative. Each rewrite gets its "
                "own inner-git commit so you can audit and revert. "
                "Disable per-target by setting its block to null or "
                "{enabled: false}."
            ),
            'enabled': True,
            # haiku is the right fit: compactors do rule-based pattern
            # matching (preserve verbatim sections, compress sessions
            # older than N to one-liners, etc.), not deep reasoning.
            # Override to 'sonnet' here if you observe the compactor
            # mis-handling complex section restructures on your project.
            'model': 'haiku',
            # Threshold below which compaction is skipped — small files
            # don't have enough verbosity to compact and the call cost
            # would outweigh the win.
            'min_chars_default': 8000,
            'targets': {
                'strategy_md': {'enabled': True, 'min_chars': 8000},
                'task_pending_md': {'enabled': True, 'min_chars': 8000},
                'task_done_md': {'enabled': True, 'min_chars': 8000},
                'project_status_md': {'enabled': True, 'min_chars': 5000},
            },
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
    
    def subagent_section(self, name: str) -> dict[str, Any]:
        sub = (self.raw.get('subagents') or {}).get(name) or {}
        return dict(sub)

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

def resolve_subagent_model(
    cfg: ProjectConfig, subagent_name: str, *, fallback: str = 'opus',
) -> str:
    """Pick the model for a subagent: subagents.<name> > loop.model > fallback."""
    sub_section = dict(cfg.raw.get('subagents') or {})
    val = sub_section.get(subagent_name)
    if val:
        return val
    loop_section = cfg.loop_section()
    return loop_section.get('model') or fallback


# ── compaction resolution ─────────────────────────────────────────────


@dataclass
class CompactionTargetCfg:
    """Resolved settings for one compaction target.

    Carries both ``enabled`` (per-target switch) and ``min_chars``
    (threshold below which the compactor is skipped). The wider
    ``compaction.enabled`` flag is checked separately by the caller —
    if it's false, every target is treated as disabled.
    """
    enabled: bool
    min_chars: int


def resolve_compaction_enabled(cfg: ProjectConfig) -> bool:
    """Top-level compaction switch (false → no compactor runs at all)."""
    section = dict(cfg.raw.get('compaction') or {})
    val = section.get('enabled')
    return True if val is None else bool(val)


def resolve_compaction_model(
    cfg: ProjectConfig, *, fallback: str = 'haiku',
) -> str:
    """Model for the compactor agents. Defaults to haiku — compactors
    do rule-based pattern matching (preserve verbatim sections, compress
    sessions older than N to one-liners, etc.), not deep reasoning,
    so haiku is fast + cheap and follows the rules just as well.

    Existing projects whose ``config.json`` pins a different model
    (e.g. ``"model": "sonnet"``) keep that pinning — the fallback only
    fires for an empty / missing compaction section."""
    section = dict(cfg.raw.get('compaction') or {})
    val = section.get('model')
    if val:
        return val
    return fallback


def resolve_compaction_target(
    cfg: ProjectConfig, target_key: str,
) -> CompactionTargetCfg:
    """Resolve one target's settings.

    ``target_key`` is the dotted-style key from the config schema:
    ``strategy_md``, ``task_pending_md``, ``task_done_md``,
    ``project_status_md``. Unknown keys return a disabled config.
    """
    section = dict(cfg.raw.get('compaction') or {})
    targets = dict(section.get('targets') or {})
    raw = targets.get(target_key)
    default_min = int(section.get('min_chars_default') or 8000)
    if raw is None:
        return CompactionTargetCfg(enabled=False, min_chars=default_min)
    if isinstance(raw, bool):
        return CompactionTargetCfg(enabled=raw, min_chars=default_min)
    if not isinstance(raw, dict):
        return CompactionTargetCfg(enabled=False, min_chars=default_min)
    enabled = bool(raw.get('enabled', True))
    min_chars = int(raw.get('min_chars', default_min))
    return CompactionTargetCfg(enabled=enabled, min_chars=min_chars)

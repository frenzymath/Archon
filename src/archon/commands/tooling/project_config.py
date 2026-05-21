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
            '_axiom_sweep_help': (
                "Run a deterministic #print axioms sweep between the "
                "prover and review phases (after \\leanok sync) to catch "
                "'sorryAx laundering' — declarations that compile with no "
                "sorry WARNING yet depend on sorryAx through a clean-"
                "compiling delegate, which the warning-based sorry count "
                "misses. Findings are written to "
                ".archon/logs/iter-NNN/axiom-sweep.{md,json}; the phase "
                "never blocks. OFF by default: it temporarily appends "
                "#print axioms to each Lean file and recompiles, so it is "
                "noticeably slower than the other deterministic checks. "
                "Turn it on for soundness-critical projects."
            ),
            'axiom_sweep': False,
        },
        'subagents': {
            '_help': (
                "Subagents are OFF by default to preserve the classic "
                "single-agent loop. To turn one on, add its name to "
                "`enabled` below (e.g. \"enabled\": [\"strategy-critic\", "
                "\"blueprint-reviewer\"]). To enable every shipped "
                "subagent, copy `_available` into `enabled`. Discovery: "
                "Archon loads `.md` descriptors from `.archon/subagents/` "
                "(project-local, overrides built-ins) and from the "
                "shipped built-ins. Each named entry (e.g. "
                "`subagents.refactor`) is an optional per-subagent "
                "settings object; the only field consulted today is "
                "`model` (a model alias overriding `loop.model` for "
                "that subagent). For backward compat, a bare string "
                "value is treated as the model alias."
            ),
            '_available': [
                # The subagents shipped with Archon. Copy any of these
                # names into `enabled` to activate. See
                # `.archon/subagents/<name>.md` (or the shipped
                # built-ins) for each one's role and write-domain.
                'blueprint-reviewer',
                'blueprint-writer',
                'lean-auditor',
                'lean-vs-blueprint-checker',
                'mathlib-analogist',
                'progress-critic',
                'reference-retriever',
                'refactor',
                'strategy-critic',
            ],
            '_recommended_plan_phase': [
                'blueprint-reviewer',
                'strategy-critic',
                'progress-critic',
            ],
            '_recommended_review_phase': [
                'lean-auditor',
                'lean-vs-blueprint-checker',
            ],
            'enabled': [],
        },
        'state': {
            '_help': (
                "Per-iteration sidecar files capture each iter's plan + "
                "review narrative under .archon/iter/iter-NNN/{plan,review,"
                "objectives}.md so top-level files (STRATEGY.md, "
                "PROJECT_STATUS.md, task_*.md) stay bounded across iters. "
                "Set recent_iter_window to control how many recent "
                "sidecars get injected into the plan/review prompt."
            ),
            # How many recent iter/iter-NNN/plan.md (and review.md) files
            # the plan/review prompts surface as context. The full
            # historical record stays on disk; only the recent K are
            # injected into the prompt. Keep small to bound the prompt
            # size; raise if agents need more memory of recent decisions.
            'recent_iter_window': 3,
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
    """Pick the model for a subagent.

    Precedence: ``subagents.<name>`` (str → model alias; dict → ``.model``)
    > ``loop.model`` > ``fallback``.
    """
    sub_section = dict(cfg.raw.get('subagents') or {})
    val = sub_section.get(subagent_name)
    if isinstance(val, dict):
        model = val.get('model')
        if isinstance(model, str) and model:
            return model
    elif isinstance(val, str) and val:
        return val
    loop_section = cfg.loop_section()
    return loop_section.get('model') or fallback


# ── state resolution ──────────────────────────────────────────────────


def resolve_recent_iter_window(cfg: ProjectConfig, *, fallback: int = 3) -> int:
    """How many recent iter sidecars to surface in plan/review prompts."""
    section = dict(cfg.raw.get('state') or {})
    val = section.get('recent_iter_window')
    try:
        return int(val) if val is not None else fallback
    except (TypeError, ValueError):
        return fallback


# ── subagent registry resolution ──────────────────────────────────────


def resolve_subagents_enabled(cfg: ProjectConfig) -> list[str] | None:
    """Return the configured subagent allowlist, or ``None`` for "use defaults".

    Schema: ``subagents.enabled`` is a list of subagent names. When
    missing or null, the registry falls back to every descriptor whose
    frontmatter has ``default_enabled: true``.

    Returning ``None`` (not ``[]``) is what tells :func:`build_registry`
    to use the default-enabled fallback. An explicit empty list means
    "no subagents available" and is honored as such.
    """
    section = cfg.raw.get('subagents')
    if not isinstance(section, dict):
        return None
    val = section.get('enabled')
    if val is None:
        return None
    if not isinstance(val, list):
        return None
    return [str(x) for x in val if isinstance(x, (str, int, float))]

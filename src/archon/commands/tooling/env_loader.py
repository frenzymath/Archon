"""Read ``.archon/.env`` into ``os.environ``.

A tiny no-dependency loader so we don't pull in ``python-dotenv`` for
half a feature. Existing shell-set variables ALWAYS win — the .env
file is a fallback, not an override, so the user can still
``MOONSHOT_API_KEY=… archon loop`` to do a one-off.

The expected keys are alternative-provider credentials (Moonshot,
DeepSeek, …). Anthropic auth is handled by Claude Code itself
(interactive login during ``archon init``), so we never read or
write ``ANTHROPIC_*`` here.
"""

from __future__ import annotations

import os
from pathlib import Path


# Provider → list of env var names recognized for that provider. Used
# both by the .env template generator and by the lane settings writer.
# These are MULTILANE providers — non-Anthropic services you can run
# a prover lane through.
#
# Only direct-API providers are supported: they speak the Anthropic
# API natively, so the lane settings just point at their endpoint with
# their auth token via ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN.
# OpenAI and Gemini lanes were dropped because Claude Code's tool-use
# semantics don't translate cleanly through a wire-format proxy — those
# models work better in their own native CLIs (codex, gemini-cli).
PROVIDERS: dict[str, list[str]] = {
    'moonshot': [
        'MOONSHOT_API_KEY',
        'MOONSHOT_BASE_URL',
        'MOONSHOT_MODEL',
    ],
    'deepseek': [
        'DEEPSEEK_API_KEY',
        'DEEPSEEK_BASE_URL',
        'DEEPSEEK_MODEL',
    ],
    # OpenRouter speaks the Anthropic API natively (same wire format as
    # kimi/deepseek), but requires ANTHROPIC_API_KEY to be explicitly
    # set to "" so Claude Code doesn't try to use its own Anthropic
    # credentials. provider_env() handles this automatically.
    'openrouter': [
        'OPENROUTER_API_KEY',
        'OPENROUTER_BASE_URL',
        'OPENROUTER_MODEL',
    ],
}

# Single-key credentials used by the *informal* agent (the tool that
# generates blueprint sketches / drafts informal proofs / etc.). These
# are NOT lane providers — the informal agent isn't a Claude wrapper,
# it just needs an API key for one of these services.
INFORMAL_AGENT_KEYS: list[str] = [
    'OPENAI_API_KEY',
    'GEMINI_API_KEY',
]

# Default values written into the template when a variable isn't yet
# in the shell. Empty string means "leave the user to fill it in".
TEMPLATE_DEFAULTS: dict[str, str] = {
    'MOONSHOT_BASE_URL': 'https://api.kimi.com/coding/',
    'MOONSHOT_MODEL': 'kimi-k2.6',
    'DEEPSEEK_BASE_URL': 'https://api.deepseek.com/anthropic',
    'DEEPSEEK_MODEL': 'deepseek-coder',
    'OPENROUTER_BASE_URL': 'https://openrouter.ai/api',
}

# When a provider's own API key is absent, these slugs let us route the
# same request through OpenRouter as a fallback (if OPENROUTER_API_KEY
# is set). The slug format matches OpenRouter's model catalogue.
OPENROUTER_FALLBACK_MODELS: dict[str, str] = {
    'moonshot': 'moonshotai/kimi-k2',
    'deepseek': 'deepseek/deepseek-r1',
}


def env_path(project_path: Path) -> Path:
    return project_path / '.archon' / '.env'


def load_env_file(project_path: Path) -> dict[str, str]:
    """Read .archon/.env into a dict and merge into os.environ.

    Returns the loaded mapping (just the keys that were in the file —
    not the full os.environ). Lines starting with ``#`` are comments.
    Empty values are skipped so a ``KEY=`` line in the template
    doesn't clobber a real value already in the shell.
    """
    path = env_path(project_path)
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        loaded[key] = value
        # Don't override a value the user already set in the shell.
        os.environ.setdefault(key, value)
    return loaded


def render_env_template(*, shell_env: dict[str, str] | None = None) -> str:
    """Build the initial .archon/.env content.

    For each known variable: if the shell already has it, write it
    uncommented (so the user inherits whatever they had configured).
    Otherwise write a commented placeholder using ``TEMPLATE_DEFAULTS``
    for things like base URLs that have a sensible default.
    """
    src = shell_env if shell_env is not None else os.environ

    def _line(key: str) -> str:
        shell_value = src.get(key, '')
        default_value = TEMPLATE_DEFAULTS.get(key, '')
        if shell_value:
            return f'{key}={shell_value}'
        if default_value:
            return f'# {key}={default_value}'
        return f'# {key}='

    lines: list[str] = [
        '# Archon environment.',
        '#',
        '# Anthropic auth is handled by Claude Code itself (interactive',
        '# login during `archon init`), so do NOT add ANTHROPIC_* here.',
        '#',
        '# Existing shell variables always win on conflict, so this file',
        '# is purely a fallback / per-project default.',
        '#',
        '# For full setup details, see .archon/MULTILANE.md.',
        '',
        '# ── Informal agent (one key is enough; pick whichever you have) ──',
        '# Used by the archon informal agent for blueprint sketches, etc.',
    ]
    for key in INFORMAL_AGENT_KEYS:
        lines.append(_line(key))
    lines.append('')
    lines.append('# ── Multi-lane providers (non-Anthropic prover lanes) ──')
    for provider, keys in PROVIDERS.items():
        lines.append(f'# {provider.title()}')
        for key in keys:
            lines.append(_line(key))
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def write_env_template(project_path: Path, *, force: bool = False) -> bool:
    """Create .archon/.env if missing. Returns True iff a new file was written."""
    path = env_path(project_path)
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_env_template(), encoding='utf-8')
    return True


def provider_env(provider: str) -> dict[str, str] | None:
    """Direct-API provider settings (kimi/moonshot, deepseek, openrouter).

    Returns the {ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, …} dict the
    lane settings file needs. ``None`` if the API key is missing or the
    provider is unknown — caller should warn + skip the lane.

    ``provider == "anthropic"`` returns ``{}`` because Anthropic lanes
    use Claude Code's own auth.

    ``provider == "openrouter"`` also injects ``ANTHROPIC_API_KEY: ""``
    (empty string, not absent) so Claude Code stops trying to use its own
    Anthropic credentials and lets the Bearer token in ANTHROPIC_AUTH_TOKEN
    carry the OpenRouter key.
    """
    if provider == 'anthropic':
        return {}
    keys = PROVIDERS.get(provider)
    if not keys:
        return None
    api_key = os.environ.get(keys[0])
    if not api_key:
        return None
    base_url = os.environ.get(keys[1]) or TEMPLATE_DEFAULTS.get(keys[1], '')
    model = os.environ.get(keys[2]) or TEMPLATE_DEFAULTS.get(keys[2], '')
    settings: dict[str, str] = {
        'ANTHROPIC_BASE_URL': base_url,
        'ANTHROPIC_AUTH_TOKEN': api_key,
        'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
    }
    if provider == 'openrouter':
        # Explicit empty string prevents Claude Code from falling back
        # to its own Anthropic session credentials.
        settings['ANTHROPIC_API_KEY'] = ''
    if model:
        settings.update({
            'ANTHROPIC_MODEL': model,
            'ANTHROPIC_DEFAULT_OPUS_MODEL': model,
            'ANTHROPIC_DEFAULT_SONNET_MODEL': model,
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': model,
        })
    return settings


def openrouter_fallback_env(provider: str) -> dict[str, str] | None:
    """Route a provider alias through OpenRouter when the direct key is absent.

    Called by :func:`~archon.agent.ClaudeAgent._resolve_provider` as a
    last resort: if the user asked for ``deepseek`` but only
    ``OPENROUTER_API_KEY`` is set, we transparently redirect the request
    through OpenRouter's Anthropic-compatible endpoint using the matching
    OpenRouter model slug.

    Returns ``None`` when ``OPENROUTER_API_KEY`` is unset or the
    provider has no entry in :data:`OPENROUTER_FALLBACK_MODELS`.
    """
    openrouter_key = os.environ.get('OPENROUTER_API_KEY')
    if not openrouter_key:
        return None
    model_slug = OPENROUTER_FALLBACK_MODELS.get(provider)
    if not model_slug:
        return None
    base_url = (
        os.environ.get('OPENROUTER_BASE_URL')
        or TEMPLATE_DEFAULTS.get('OPENROUTER_BASE_URL', '')
    )
    settings: dict[str, str] = {
        'ANTHROPIC_BASE_URL': base_url,
        'ANTHROPIC_AUTH_TOKEN': openrouter_key,
        'ANTHROPIC_API_KEY': '',
        'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
        'ANTHROPIC_MODEL': model_slug,
        'ANTHROPIC_DEFAULT_OPUS_MODEL': model_slug,
        'ANTHROPIC_DEFAULT_SONNET_MODEL': model_slug,
        'ANTHROPIC_DEFAULT_HAIKU_MODEL': model_slug,
    }
    return settings

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
}

# Default values written into the template when a variable isn't yet
# in the shell. Empty string means "leave the user to fill it in".
TEMPLATE_DEFAULTS: dict[str, str] = {
    'MOONSHOT_BASE_URL': 'https://api.moonshot.ai/anthropic',
    'MOONSHOT_MODEL': 'kimi-k2.6',
    'DEEPSEEK_BASE_URL': 'https://api.deepseek.com/anthropic',
    'DEEPSEEK_MODEL': 'deepseek-coder',
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

    For each known provider variable: if the shell already has it, write
    it uncommented (so the user inherits whatever they had configured).
    Otherwise write a commented placeholder using ``TEMPLATE_DEFAULTS``
    for things like base URLs that have a sensible default.
    """
    src = shell_env if shell_env is not None else os.environ
    lines: list[str] = [
        '# Archon environment for alternative-provider lanes.',
        '#',
        '# Anthropic auth is handled by Claude Code itself (interactive',
        '# login during `archon init`), so do NOT add ANTHROPIC_* here.',
        '#',
        '# Add API keys for any non-Anthropic provider you want to use',
        '# in multilane runs. Existing shell variables always win, so',
        '# this file is purely a fallback / per-project default.',
        '',
    ]
    for provider, keys in PROVIDERS.items():
        lines.append(f'# {provider.title()}')
        for key in keys:
            shell_value = src.get(key, '')
            default_value = TEMPLATE_DEFAULTS.get(key, '')
            if shell_value:
                lines.append(f'{key}={shell_value}')
            elif default_value:
                lines.append(f'# {key}={default_value}')
            else:
                lines.append(f'# {key}=')
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
    """Return the {ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, …} dict for a
    non-Anthropic provider, sourced from os.environ.

    None if the provider's API key is missing — the caller should warn
    and skip that lane rather than crash. ``provider == "anthropic"``
    returns ``{}`` because Anthropic lanes use Claude Code's own auth.
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
    if model:
        settings.update({
            'ANTHROPIC_MODEL': model,
            'ANTHROPIC_DEFAULT_OPUS_MODEL': model,
            'ANTHROPIC_DEFAULT_SONNET_MODEL': model,
            'ANTHROPIC_DEFAULT_HAIKU_MODEL': model,
        })
    return settings

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
# Two flavors:
# 1. Direct-API providers (kimi/moonshot, deepseek): they already
#    speak the Anthropic API natively, so the lane settings just
#    point at their endpoint with their auth token.
# 2. Proxy-mediated providers (openai, gemini): they speak their
#    native APIs, so we spawn `archon.proxy.server` per-lane and the
#    lane points at http://127.0.0.1:<port>. Configured via
#    BIG_MODEL / SMALL_MODEL env vars (the proxy reads those at
#    spawn time).
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
    'openai': [
        'OPENAI_API_KEY',
        'OPENAI_BIG_MODEL',
        'OPENAI_SMALL_MODEL',
        'OPENAI_BASE_URL',
    ],
    'gemini': [
        'GEMINI_API_KEY',
        'GEMINI_BIG_MODEL',
        'GEMINI_SMALL_MODEL',
    ],
}

# Providers that need the bundled Anthropic↔LiteLLM proxy because they
# don't speak Anthropic's API natively. ``provider_env`` returns
# ``None`` for these — the lane setup path spawns a proxy and writes
# the lane settings file pointing at it.
PROXY_PROVIDERS: set[str] = {'openai', 'gemini'}

# Single-key credentials used by the *informal* agent (the tool that
# generates blueprint sketches / drafts informal proofs / etc.). These
# are NOT lane providers — the informal agent isn't a Claude wrapper,
# it just needs an API key for one of these services.
INFORMAL_AGENT_KEYS: list[str] = [
    'OPENAI_API_KEY',
    'GEMINI_API_KEY',
    'OPENROUTER_API_KEY',
]

# Default values written into the template when a variable isn't yet
# in the shell. Empty string means "leave the user to fill it in".
TEMPLATE_DEFAULTS: dict[str, str] = {
    'MOONSHOT_BASE_URL': 'https://api.moonshot.ai/anthropic',
    'MOONSHOT_MODEL': 'kimi-k2.6',
    'DEEPSEEK_BASE_URL': 'https://api.deepseek.com/anthropic',
    'DEEPSEEK_MODEL': 'deepseek-coder',
    # OpenAI flagship picks as of 2026-04. Override per-project in .env.
    'OPENAI_BIG_MODEL': 'gpt-5.4',
    'OPENAI_SMALL_MODEL': 'gpt-5.4-mini',
    # Gemini regular API (not Vertex).
    'GEMINI_BIG_MODEL': 'gemini-2.5-pro',
    'GEMINI_SMALL_MODEL': 'gemini-2.5-flash',
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
    """Direct-API provider settings (kimi/moonshot, deepseek).

    Returns the {ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, …} dict the
    lane settings file needs. None if:
    - the API key is missing (caller should warn + skip the lane),
    - the provider is proxy-mediated (use ``proxy_spawn_env`` instead),
    - the provider is unknown.

    ``provider == "anthropic"`` returns ``{}`` because Anthropic lanes
    use Claude Code's own auth.
    """
    if provider == 'anthropic':
        return {}
    if provider in PROXY_PROVIDERS:
        return None
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


def proxy_spawn_env(provider: str) -> dict[str, str] | None:
    """Env vars to spawn the bundled proxy for a proxy-mediated provider.

    Returns ``None`` when the API key is missing so the caller can
    disable that lane and continue with the others. The returned dict
    is what gets passed to ``archon.proxy.start_proxy(env=...)`` —
    don't ship it to Claude Code directly.
    """
    if provider not in PROXY_PROVIDERS:
        return None
    if provider == 'openai':
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            return None
        env: dict[str, str] = {
            'PREFERRED_PROVIDER': 'openai',
            'OPENAI_API_KEY': api_key,
            'BIG_MODEL': os.environ.get('OPENAI_BIG_MODEL') or TEMPLATE_DEFAULTS['OPENAI_BIG_MODEL'],
            'SMALL_MODEL': os.environ.get('OPENAI_SMALL_MODEL') or TEMPLATE_DEFAULTS['OPENAI_SMALL_MODEL'],
        }
        if os.environ.get('OPENAI_BASE_URL'):
            env['OPENAI_BASE_URL'] = os.environ['OPENAI_BASE_URL']
        return env
    if provider == 'gemini':
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            return None
        return {
            'PREFERRED_PROVIDER': 'google',
            'GEMINI_API_KEY': api_key,
            'BIG_MODEL': os.environ.get('GEMINI_BIG_MODEL') or TEMPLATE_DEFAULTS['GEMINI_BIG_MODEL'],
            'SMALL_MODEL': os.environ.get('GEMINI_SMALL_MODEL') or TEMPLATE_DEFAULTS['GEMINI_SMALL_MODEL'],
        }
    return None


def lane_proxy_settings(*, port: int) -> dict[str, str]:
    """Lane settings file shape pointing at a local proxy on ``port``.

    The auth token is a placeholder: the proxy ignores it (the real key
    lives in the proxy's process env). Claude Code refuses to start
    without *some* token, so we provide a non-empty dummy.
    """
    return {
        'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{port}',
        'ANTHROPIC_AUTH_TOKEN': 'archon-proxy',
        'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
    }

"""Auto-generate per-lane Claude settings for non-Anthropic providers.

Each lane's provider must speak the Anthropic API natively (kimi,
deepseek, ...). The settings file points at the provider's
Anthropic-compatible endpoint with the user's auth token via
``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN``.

Lanes whose API key is missing are disabled in-place with a warning so
a single missing credential doesn't take down the whole round.
"""

from __future__ import annotations

import json
from pathlib import Path

from archon import log
from archon.multilane.config import MultiLaneConfig


def autogen_lane_settings(state_dir: Path, config: MultiLaneConfig) -> MultiLaneConfig:
    from archon.commands.tooling.env_loader import provider_env

    lanes_dir = state_dir / 'multilane' / 'lanes'
    lanes_dir.mkdir(parents=True, exist_ok=True)

    for lane in config.lanes:
        if lane.provider == 'anthropic':
            continue
        if lane.claude_settings_path:
            # Respect a lane that brought its own pre-baked settings file.
            continue

        settings_dict = provider_env(lane.provider)
        if settings_dict is None:
            log.warn(
                f"Lane '{lane.lane_id}': no credentials found for provider "
                f"'{lane.provider}' in environment / .archon/.env — disabling this lane."
            )
            lane.enabled = False
            continue

        settings_file = lanes_dir / f'{lane.lane_id}-settings.json'
        settings_file.write_text(
            json.dumps({'env': settings_dict}, indent=2) + '\n',
            encoding='utf-8',
        )
        lane.claude_settings_path = str(settings_file)

    return config

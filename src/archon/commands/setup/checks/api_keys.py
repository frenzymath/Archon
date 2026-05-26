"""Probe environment for informal-agent API keys."""

from __future__ import annotations

import os

from archon import log

from .base import DependencyCheck


_KEYS = {
    "DEEPSEEK_API_KEY": "DeepSeek",
    "MOONSHOT_API_KEY": "Kimi (Moonshot)",
    "OPENROUTER_API_KEY": "OpenRouter",
    "OPENAI_API_KEY": "OpenAI",
    "GEMINI_API_KEY": "Gemini",
}


class ApiKeysCheck(DependencyCheck):
    """Optional: keys are nice to have but the loop runs without them."""

    name = "informal-agent API keys"

    def run(self) -> bool:
        log.info("The informal agent can request proof sketches from external models.")
        log.info("This is optional — everything else works without it.")
        found_any = False
        for var, label in _KEYS.items():
            if os.environ.get(var):
                value = os.environ[var]
                log.success(f"{var} is set ({label}) : {value[:4]}...{value[-4:]})")
                found_any = True
            else:
                log.step(f"{var} not set — export {var}=... to enable {label}")
        if not found_any:
            log.warn(
                "No external-model API keys found. Set at least one if you want "
                "to use the informal agent.",
            )
        return found_any

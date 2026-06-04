#!/usr/bin/env python3
"""Informal mathematical reasoning via external LLMs.

Supported providers:
    openai          https://api.openai.com/v1              → OPENAI_API_KEY
    gemini          Google Generative Language API          → GEMINI_API_KEY
    openrouter      https://openrouter.ai/api/v1           → OPENROUTER_API_KEY
    deepseek        https://api.deepseek.com/v1            → DEEPSEEK_API_KEY
    kimi            https://api.moonshot.cn/v1  (OpenAI-compatible)   → MOONSHOT_API_KEY
    kimi-anthropic  Anthropic-compatible Messages endpoint            → MOONSHOT_API_KEY
    auto            pick the best available key automatically (default)

Two flavours of MOONSHOT_API_KEY are supported automatically:
    sk-...        standard Moonshot key → OpenAI-compatible (provider "kimi")
    sk-kimi-...   Kimi-for-Coding key   → Anthropic-compatible coding endpoint
                  (provider "kimi-anthropic"); base URL taken from
                  MOONSHOT_BASE_URL, default https://api.kimi.com/coding

No dependencies beyond Python 3.10+ stdlib.

Usage:
    python3 archon-informal-agent.py "Prove that ..."
    python3 archon-informal-agent.py --provider deepseek "Prove that ..."
    python3 archon-informal-agent.py --provider gemini --think "Prove that ..."
    python3 archon-informal-agent.py --provider kimi-anthropic --think "Prove that ..."
    python3 archon-informal-agent.py --provider openrouter --model deepseek/deepseek-r1 "..."

Check which keys are available before use:
    env | grep -E "OPENAI|GEMINI|OPENROUTER|DEEPSEEK|MOONSHOT"
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULTS = {
    "openai": "gpt-5.4",
    "gemini": "gemini-3.1-pro-preview",
    "openrouter": "google/gemini-3.1-pro-preview",
    "deepseek": "deepseek-reasoner",
    "kimi": "kimi-k2",
    "kimi-anthropic": "kimi-k2",
}

# Auto-provider picks the first available key in this priority order.
# deepseek-reasoner and kimi-k2 are particularly strong at formal math.
_AUTO_PRIORITY = ["deepseek", "kimi", "openrouter", "openai", "gemini"]
_AUTO_KEY = {
    "deepseek": "DEEPSEEK_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

SYSTEM_PROMPT = (
    "You are an expert mathematician. Given a mathematical statement or problem, "
    "provide a clear, detailed informal proof or solution. "
    "Focus on mathematical reasoning and intuition. "
    "Structure your response with clear logical steps."
)

TIMEOUT = 300


def _require_key(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        sys.exit(f"Error: {name} not set")
    return val


def _post(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode() if e.fp else ""
        sys.exit(f"API error {e.code}: {detail}")


def call_gemini(prompt: str, model: str, think: bool) -> str:
    key = _require_key("GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    gen_config: dict = {}
    if think:
        gen_config["thinkingConfig"] = {"thinkingLevel": "high", "includeThoughts": True}
    else:
        gen_config["temperature"] = 0.3

    data = _post(url, {"x-goog-api-key": key}, {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": gen_config,
    })

    parts = data["candidates"][0]["content"]["parts"]
    out = []
    for p in parts:
        if p.get("thought"):
            out.append(f"[Thinking]\n{p['text']}\n[/Thinking]")
        else:
            out.append(p["text"])
    return "\n\n".join(out)


def _openai_base() -> str:
    return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def _deepseek_base() -> str:
    return os.environ.get("DEEPSEEK_CHAT_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")


def _kimi_base() -> str:
    return os.environ.get("MOONSHOT_CHAT_BASE_URL", "https://api.moonshot.cn/v1").rstrip("/")


def call_openai(prompt: str, model: str, think: bool) -> str:
    key = _require_key("OPENAI_API_KEY")
    auth = {"Authorization": f"Bearer {key}"}
    base = _openai_base()

    if model.startswith("o") and "api.openai.com" in base:
        return _openai_responses(prompt, model, auth, base, think)
    return _openai_chat(prompt, model, auth, base)


def _openai_responses(prompt: str, model: str, auth: dict, base: str, think: bool) -> str:
    data = _post(f"{base}/responses", auth, {
        "model": model,
        "input": [
            {"role": "developer", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "reasoning": {"effort": "high" if think else "medium"},
    })
    out = []
    for item in data.get("output", []):
        if item.get("type") == "reasoning":
            for s in item.get("summary", []):
                out.append(f"[Thinking]\n{s.get('text', '')}\n[/Thinking]")
        elif item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    out.append(c["text"])
    return "\n\n".join(out) if out else json.dumps(data, indent=2)


def _openai_chat(prompt: str, model: str, auth: dict, base: str) -> str:
    data = _post(f"{base}/chat/completions", auth, {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    })
    return data["choices"][0]["message"]["content"]


def call_openrouter(prompt: str, model: str, think: bool) -> str:
    key = _require_key("OPENROUTER_API_KEY")
    auth = {"Authorization": f"Bearer {key}"}
    data = _post("https://openrouter.ai/api/v1/chat/completions", auth, {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    })
    return data["choices"][0]["message"]["content"]


def call_deepseek(prompt: str, model: str, think: bool) -> str:
    key = _require_key("DEEPSEEK_API_KEY")
    return _openai_chat(prompt, model, {"Authorization": f"Bearer {key}"}, _deepseek_base())


def call_kimi(prompt: str, model: str, think: bool) -> str:
    key = _require_key("MOONSHOT_API_KEY")
    return _openai_chat(prompt, model, {"Authorization": f"Bearer {key}"}, _kimi_base())


def _is_kimi_coding_key() -> bool:
    """True if MOONSHOT_API_KEY is a Kimi-for-Coding key (sk-kimi-...).

    These keys are Anthropic-compatible and valid only against the coding
    endpoint (api.kimi.com/coding) — not the OpenAI-compatible api.moonshot.cn.
    """
    return os.environ.get("MOONSHOT_API_KEY", "").startswith("sk-kimi-")


def _kimi_anthropic_base() -> str:
    # Explicit override always wins.
    override = os.environ.get("MOONSHOT_ANTHROPIC_BASE_URL", "")
    if override:
        return override.rstrip("/")
    # Kimi-for-Coding keys speak the Anthropic Messages format against the
    # coding endpoint. Reuse MOONSHOT_BASE_URL (set by multilane, e.g.
    # https://api.kimi.com/coding/) and fall back to the public default.
    if _is_kimi_coding_key():
        base = os.environ.get("MOONSHOT_BASE_URL", "https://api.kimi.com/coding")
        return base.rstrip("/")
    # Standard Moonshot keys: same domain as the OpenAI-compatible route.
    return "https://api.moonshot.cn"


def call_kimi_anthropic(prompt: str, model: str, think: bool) -> str:
    """Call Kimi via Moonshot's Anthropic-compatible Messages endpoint.

    Uses MOONSHOT_API_KEY (same key as the OpenAI-compatible route) but
    speaks the Anthropic Messages API wire format so models that expose
    extended-thinking only on that endpoint can be reached with --think.
    """
    key = _require_key("MOONSHOT_API_KEY")
    base = _kimi_anthropic_base()
    body: dict = {
        "model": model,
        "max_tokens": 16000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    if think:
        body["thinking"] = {"type": "enabled", "budget_tokens": 10000}
    data = _post(
        f"{base}/v1/messages",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
        body,
    )
    out = []
    for block in data.get("content", []):
        if block.get("type") == "thinking":
            out.append(f"[Thinking]\n{block['thinking']}\n[/Thinking]")
        elif block.get("type") == "text":
            out.append(block["text"])
    return "\n\n".join(out) if out else json.dumps(data, indent=2)


def _auto_provider() -> str:
    """Return the highest-priority provider whose API key is set."""
    for provider in _AUTO_PRIORITY:
        if os.environ.get(_AUTO_KEY[provider]):
            # A Kimi-for-Coding key (sk-kimi-) only works through the
            # Anthropic-compatible coding endpoint, not the OpenAI route.
            if provider == "kimi" and _is_kimi_coding_key():
                return "kimi-anthropic"
            return provider
    keys = " / ".join(_AUTO_KEY.values())
    sys.exit(f"Error: no API key found. Set one of: {keys}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prompt")
    p.add_argument(
        "--provider",
        choices=["openai", "gemini", "openrouter", "deepseek", "kimi", "kimi-anthropic", "auto"],
        default="auto",
    )
    p.add_argument("--model", default=None)
    p.add_argument("--think", action="store_true")
    args = p.parse_args()

    provider = _auto_provider() if args.provider == "auto" else args.provider
    model = args.model or DEFAULTS[provider]
    fn = {
        "gemini": call_gemini,
        "openai": call_openai,
        "openrouter": call_openrouter,
        "deepseek": call_deepseek,
        "kimi": call_kimi,
        "kimi-anthropic": call_kimi_anthropic,
    }[provider]
    print(fn(args.prompt, model, args.think))


if __name__ == "__main__":
    main()

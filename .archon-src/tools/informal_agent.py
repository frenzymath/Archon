#!/usr/bin/env python3
"""Informal mathematical reasoning via external LLMs (OpenAI / Gemini / OpenRouter).

No dependencies beyond Python 3.10+ stdlib.

Environment variables:
    OPENAI_API_KEY      Required for --provider openai
    GEMINI_API_KEY      Required for --provider gemini
    OPENROUTER_API_KEY  Required for --provider openrouter

Usage:
    python3 archon-informal-agent.py --provider openai "Prove that ..."
    python3 archon-informal-agent.py --provider gemini --think "Prove that ..."
    python3 archon-informal-agent.py --provider openrouter "Prove that ..."
    python3 archon-informal-agent.py --provider openrouter --model deepseek/deepseek-r1 "..."

OpenRouter (https://openrouter.ai) provides access to 200+ models through a single
API key. Set OPENROUTER_API_KEY and use any model ID from their catalog, e.g.:
    --provider openrouter --model google/gemini-3.1-pro-preview   (default)
    --provider openrouter --model deepseek/deepseek-r1
    --provider openrouter --model anthropic/claude-sonnet-4
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


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prompt")
    p.add_argument("--provider", choices=["openai", "gemini", "openrouter"], required=True)
    p.add_argument("--model", default=None)
    p.add_argument("--think", action="store_true")
    args = p.parse_args()

    model = args.model or DEFAULTS[args.provider]
    fn = {"gemini": call_gemini, "openai": call_openai, "openrouter": call_openrouter}[args.provider]
    print(fn(args.prompt, model, args.think))


if __name__ == "__main__":
    main()

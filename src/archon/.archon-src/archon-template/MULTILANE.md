# Multi-lane proving setup

This file describes how to enable parallel prover **lanes** that run different
LLM providers in parallel on the same Lean files. The merge agent then picks
the best proof per declaration across the lanes' outputs.

You only need this if you want to run multiple providers in parallel.
A single Anthropic lane (the default) is the normal flow and needs no setup
beyond the interactive Claude Code login that `archon init` does for you.

## TL;DR

Two files matter:

- `.archon/.env` — provider API keys (gitignored, never committed).
- `.archon/config.json` — declares which lanes run, with which provider.

To turn multilane on:

1. Put your provider keys in `.archon/.env` (uncomment the relevant block).
2. In `.archon/config.json`, set `multilane.enabled: true` and copy the
   relevant lane entry from `multilane._examples` into `multilane.lanes`.
3. Run `archon loop` — multilane fires automatically when `enabled` is true.

That's it. You don't need to touch any CLI flag.

## Provider matrix

| Provider | `provider` value | API key env var | How it talks to its API |
|---|---|---|---|
| Anthropic (Claude Code) | `anthropic` | (Claude Code login) | Claude Code's own auth |
| Moonshot (Kimi) | `moonshot` | `MOONSHOT_API_KEY` | Provider's `/anthropic` endpoint (native) |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | Provider's `/anthropic` endpoint (native) |

Every supported provider speaks the Anthropic API natively — archon points the
lane's Claude Code session at the provider's `/anthropic` endpoint via
`ANTHROPIC_BASE_URL`. OpenAI and Gemini are intentionally not in the list:
Claude Code's tool-use semantics don't translate cleanly through a wire-format
proxy, so those models work better in their own native CLIs (codex,
gemini-cli) — out of scope for this multilane runner.

## Per-provider env vars

All env vars go in `.archon/.env`. Fields with a default in the template are
optional; only the API key is mandatory.

### Moonshot (Kimi)

```bash
MOONSHOT_API_KEY=sk-...
# MOONSHOT_BASE_URL=https://api.moonshot.ai/anthropic     # default
# MOONSHOT_MODEL=kimi-k2.6                                 # default
```

### DeepSeek

```bash
DEEPSEEK_API_KEY=sk-...
# DEEPSEEK_BASE_URL=https://api.deepseek.com/anthropic    # default
# DEEPSEEK_MODEL=deepseek-coder                            # default
```

## Example: run Anthropic + Kimi side by side

`.archon/.env`:

```bash
MOONSHOT_API_KEY=sk-...your-moonshot-key...
```

`.archon/config.json` (only the relevant section):

```json
"multilane": {
  "enabled": true,
  "base_ref": "main",
  "lanes": [
    {"lane_id": "anthropic", "provider": "anthropic", "model": "opus"},
    {"lane_id": "kimi",      "provider": "moonshot"}
  ]
}
```

Then `archon loop`. The dashboard's log list will show
`anthropic//<file>.lean` and `kimi//<file>.lean` entries side by side, with
a `merge//<file>.lean` entry once both lanes finish that file.

## What happens if a lane's API key is missing

The loop disables that lane in-place with a warning and proceeds with the
others — a single missing key doesn't take down the round.

## What happens if a lane finishes faster

`archon loop` runs all `(lane × file)` jobs concurrently up to
`loop.max_parallel × num_lanes` slots. When one lane finishes a file
**cleanly** (no sorries, no new axioms, builds, only the assigned file
changed), the other lanes still working on that file get **10 minutes** of
grace; if they haven't wrapped up by then they're killed. The merge agent
then picks the best proof per declaration across whichever lanes did finish
that file.

## Where things land on disk

- `.archon/lanes/<lane>/` — the lane's worktree (separate inner-git checkout).
- `.archon/multilane/lanes/<lane>/iter-NNN/provers/<file>.jsonl` — the lane's
  per-file prover log (also symlinked into `.archon/logs/iter-NNN/provers/`
  as `<file>__<lane>.jsonl` so the dashboard sees it).
- `.archon/multilane/runtime/iter-NNN-results.jsonl` — per-assignment outcome
  rows: success / failure_reason (`rate_limited`, `auth_failed`, etc.).
- `.archon/multilane/reports/iter-NNN-execution.md` — human-readable summary
  including per-file merges.

## Disabling

Set `multilane.enabled: false` (or remove all but one lane). `archon loop`
goes back to single-lane behaviour. Existing lane worktrees under
`.archon/lanes/` aren't deleted automatically — `git worktree remove
.archon/lanes/<lane>` cleans them up.

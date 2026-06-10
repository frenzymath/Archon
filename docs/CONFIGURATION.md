# Configuring backends, harnesses, and models

Archon runs each *role* (`plan` / `prover` / `review`) and each *subagent*
through an **engine**. By default that engine is Claude Code driving
`claude -p`. Two orthogonal knobs change this:

- **Backend** — *how* the Claude Code engine is launched (`claude -p`, the
  `claude-p` TUI wrapper, the VS Code / Desktop entrypoints, or a foreground
  interactive session). Claude-only.
- **Harness** — *which* engine runs the work (`claude-code` or `codex`),
  together with the model and engine-specific options. A harness is a named
  bundle you can assign to any role or subagent.

All of this lives in `.archon/config.json`. With no `loop.harness` /
`loop.roles` / `harnesses` keys set, every role and subagent uses the built-in
`claude-code` harness on the `default` backend — i.e. plain `claude -p` — and
nothing below applies.

`archon init` writes a fully-commented `config.json`; the sections below are the
reference for it. Every key is optional.

## At a glance

A typical hand-tuned `config.json` (comments shown for explanation — strip them
for strict JSON):

```jsonc
{
  "loop": {
    "max_iterations": 10,          // stop after N iterations (default 10)
    "parallel": true,
    "max_parallel": 4,             // concurrent prover agents
    "model": "opus",               // role model: opus (default) / sonnet / haiku / kimi / …

    "claude_backend": "claude-p",  // how Claude Code is launched — see §1
    "harness": "codex",            // engine for every role + subagent — see §2 (empty = Claude Code)
    "roles": { "plan": "claude-code", "prover": "codex" }  // per-role override — see §2
  },

  "subagents": {
    "enabled": "*",                // "*" = all installed, or a list of names — see §3
    "strategy-critic": "opus"      // per-subagent model override
  },

  "multilane": {
    "enabled": false,              // parallel multi-provider proving — see docs/MULTILANE.md
    "lanes": [ { "lane_id": "anthropic", "provider": "anthropic", "model": "opus" } ]
  }
}
```

`harnesses` (named engine bundles) is the one block not shown here; `archon init`
ships a ready-to-use `codex` harness, so you usually only reference it by name.

---

## 1. Backends (Claude launch strategy)

Set once for the whole loop via config or the `--claude-backend` CLI flag:

```json
{ "loop": { "claude_backend": "claude-p" } }
```

| Value | What it does |
|-------|--------------|
| `default` | Plain `claude -p` headless subprocess. |
| `vscode` | Sets `CLAUDE_CODE_ENTRYPOINT=claude-vscode` so the session is attributed to the VS Code extension. |
| `desktop` | Same, with `CLAUDE_CODE_ENTRYPOINT=claude-desktop`. |
| `claude-p` | Drives the interactive Claude Code TUI headlessly via the [`claude-p`](https://github.com/AxelDlv00/claude-p) wrapper. Useful when the standard headless path is rate-limited on a subscription account. |
| `interactive` | Runs `claude` in the **foreground** for you to drive by hand. Forces serial execution (`max_parallel 1`) and disables multilane. |

**Precedence:** `--claude-backend` flag > `ARCHON_CLAUDE_BACKEND` env >
`loop.claude_backend` > `default`.

### `claude-p` config directory

`claude-p` reads its login from `CLAUDE_CONFIG_DIR`. Pin it with:

```json
{ "loop": { "claude_backend": "claude-p", "claude_p_config_dir": "~/.claude-work" } }
```

or `--claude-p-config-dir`, or the `ARCHON_CLAUDE_P_CONFIG_DIR` env. When unset,
the value already in the environment is used.

### Verifying which binary ran

`claude-p` writes a raw PTY transcript next to each phase/subagent JSONL log:

```bash
ls .archon/logs/iter-*/**/*.claude-p-raw.log
```

A `*.claude-p-raw.log` next to a **subagent** log (`<subagent>-<slug>.claude-p-raw.log`)
confirms that subagent ran through `claude-p`; the run log also contains a
`claude-p raw transcript: …` line. Plain `claude -p` never produces this file.

> **Note — subagents inherit the parent backend.** A subagent is dispatched by
> `archon subagent`, which carries no `--claude-backend` flag. The parent agent
> exports its backend (`ARCHON_CLAUDE_BACKEND`, plus `ARCHON_CLAUDE_P_CONFIG_DIR`
> for `claude-p`) into the child process so the subagent re-resolves to the same
> backend. The loop-level `--claude-backend` flag therefore *does* reach
> subagents. (Before this was wired up, subagents silently fell back to
> `default` unless `loop.claude_backend` was set in config.)

---

## 2. Harnesses (engine + model bundles)

A **harness** is a named descriptor under `harnesses.<name>`:

```json
{
  "harnesses": {
    "myharness":   { "runner": "claude-code", "model": "sonnet", "backend": "claude-p" },
    "fast-codex":  { "runner": "codex", "model": "gpt-mini", "effort": "high", "mcp": ["lean-lsp"] }
  }
}
```

Fields:

| Field | Applies to | Meaning |
|-------|-----------|---------|
| `runner` | all | Engine: `claude-code` (default) or `codex`. |
| `model` | all | Model id/alias (see §4). |
| `backend` | claude-code | Per-harness backend override (`default`/`claude-p`/`vscode`/`desktop`/`interactive`). Beats the loop-wide backend. |
| `effort` | codex | `model_reasoning_effort` (e.g. `xhigh`). |
| `sandbox` | codex | Sandbox mode (default `danger-full-access`). |
| `mcp` | codex | MCP bundle name(s), e.g. `"lean-lsp"` or `["lean-lsp"]`. |
| `prompt_variant` | codex | Alternate prompt tail. |
| `base_url_env` / `key_env` | codex | Names of env vars holding the gateway base URL / API key. |
| `wire_api` | codex | Wire protocol (default `responses`). |

A built-in `codex` harness ships, so you can route to Codex without defining one
yourself. To customize (or make a claude-code variant), copy the
`_my_harness_example` block in `.archon/config.json`, rename it, and reference it.

### One-line global default (everything uses codex)

To make **every** role *and* every subagent use one engine, set a single key —
`loop.harness`. No per-subagent configuration needed:

```json
{ "loop": { "harness": "codex" } }
```

That routes plan, prover, review, and all subagents through the `codex`
harness. (Multilane lanes are the one exception — they carry their own
per-lane harness and are not affected by `loop.harness`.)

### Assigning a harness (with narrower overrides)

```json
{
  "loop": {
    "harness": "myharness",          // applies to every role + subagent…
    "roles": { "prover": "fast-codex" }  // …unless a role overrides it
  },
  "subagents": {
    "lean-auditor": { "harness": "fast-codex", "model": "gpt-mini" }
  }
}
```

**Role precedence:** `loop.roles.<role>` > `loop.harness` > `claude-code`.

**Subagent precedence:** `subagents.<name>.harness` > `loop.harness` >
the subagent's own frontmatter `harness:` > `claude-code`.

> ⚠️ **Gotcha — object form for harnesses.** A *bare string* under
> `subagents.<name>` is interpreted as a **model alias**, not a harness:
> `"lean-auditor": "haiku"` sets the model, not the harness. To set a harness
> you must use the object form: `"lean-auditor": { "harness": "myharness" }`.

So your `myharness → codex → gpt-mini` idea works exactly as expected — define
it once under `harnesses`, then reference it by name:

```json
{
  "harnesses": { "myharness": { "runner": "codex", "model": "gpt-mini" } },
  "subagents": { "lean-auditor": { "harness": "myharness" } }
}
```

### Codex subagent dispatch

Codex runs each command in a sandboxed login shell that re-derives `PATH`,
dropping the venv `bin/` — so `archon`, `codex`, and `uv` aren't on `PATH` when
a subagent is dispatched. Archon handles this automatically by passing absolute
paths through the environment (`ARCHON_CLI_BIN`/`ARCHON_PYTHON` for the wrapper,
`ARCHON_CODEX_BIN`/`ARCHON_UV_BIN` for the nested codex/MCP). It almost always
just works; pin paths only if it doesn't:

```json
{ "harnesses": { "codex": { "runner": "codex", "bin": "/abs/path/to/codex", "uv_bin": "/abs/path/to/uv" } } }
```

If you see *"archon CLI not found"*, ensure Archon is installed in the
environment Codex runs in and re-run `archon init` so the project's
`.claude/tools/archon-subagent.py` is current.

---

## 3. Subagents

The shipped subagents are listed under `subagents._available` in the generated
`.archon/config.json`. Copy any name into `subagents.enabled` to activate it;
see `.archon/subagents/<name>.md` for each one's role, write-domain, and
`default_enabled` status.

`subagents.enabled` accepts either a list of names or the string `"*"`, which
enables every installed subagent without having to list them:

```json
{ "subagents": { "enabled": "*" } }
```

An omitted or `null` `enabled` falls back to the `default_enabled` descriptors;
an empty list `[]` means "no subagents".

Per-subagent model override (no harness change, it inherits the parent harness !):

```json
{ "subagents": { "strategy-critic": "opus", "mathlib-analogist": "sonnet" } }
```

---

## 4. Model aliases

| Alias | Model |
|-------|-------|
| `opus` (default) / `sonnet` / `haiku` | The matching Claude model. |
| *full model id* | Passed through verbatim. |
| `kimi` | Moonshot — needs `MOONSHOT_API_KEY`. |
| `deepseek` | needs `DEEPSEEK_API_KEY`. |
| `openrouter` | needs `OPENROUTER_API_KEY` + `OPENROUTER_MODEL`. |

For Codex harnesses, `model` is the Codex/gateway model id.

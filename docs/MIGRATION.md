# Migrating to a newer Archon

This guide walks you through upgrading an existing Archon install and,
separately, an existing Archon-initialized project. If you are starting
from scratch, you don't need this file — follow the
[README](../README.md) instead.

- **Coming from a pre-CLI checkout?** Start with section 1 (v0.1.0 reworked
  installation around a single `archon` CLI) and continue through to
  section 6.
- **Coming from v0.2.0 or earlier?** Skip to [section 8](#8-upgrading-to-v030) —
  v0.3.0 adds a configurable Claude backend for alternative headless
  entrypoints and hardens stage detection.
- **Coming from v0.1.0?** Skip to [section 7](#7-upgrading-from-v010-to-v020) —
  v0.2.0 adds multi-lane proving, the refactor agent, inner-git
  versioning, `archon-protected.yaml`, an opt-in subagent system, a
  `--resume` flag, a blueprint-doctor phase, and a post-plan validation
  step. Default single-agent behavior is preserved, so the v0.1.0 install
  you have keeps working.

## Notes for v0.1.0 readers (kept for reference)

## TL;DR

*It is always safer to backup your project before running a migration. We recommend committing all changes to git and pushing to a remote before starting. However, be aware that some files are gitignored by default (e.g. `.archon/`)*

1. **Reinstall** the tool with the new one-line installer.
2. **Re-run `archon init`** in each project that was initialized by an older
   version. When asked, pick **merge** (recommended) — Archon will walk you
   through the differences file by file with Claude Code's help.

Your project source files (`.lean`, papers, etc.) are never touched. Only
files inside `.archon/` and project-scope Claude Code registrations are
affected.

---

## 1. What changed

### 1.1 Installation: shell scripts → Python CLI

Previous versions shipped a collection of shell scripts (`archon-loop.sh`,
`init.sh`, `review.sh`, etc.) that you ran from a cloned checkout of the
repository. v0.1.0 replaces them with a single installable Python package
that exposes the commands below:

| Command | Description |
|---------|-------------|
| `archon init` | Initialize a new Archon project (or reconcile an existing one). |
| `archon loop` | Run the automated plan → prove → review loop. |
| `archon dashboard` | Start the web monitoring interface. |
| `archon doctor` | Verify the full Archon setup and health. |
| `archon prove` | Directly prove an inline statement. |
| `archon setup` | Install required system dependencies. |
| `archon update` | Update Archon to the latest published version. |

The install command is:

```bash
curl -sSL https://raw.githubusercontent.com/frenzymath/Archon/refs/heads/main/install.sh | bash
```

You no longer need to keep a clone of the Archon repository around to use
Archon. The package is installed into your Python environment and can be
updated with `archon update`.

### 1.2 Project layout: symlinks → copies

Previous versions populated `<project>/.archon/prompts/` with **symlinks
back to the Archon source checkout** and installed `lean4-skills` as a
symlinked cache. This meant:

- Changing a prompt in the Archon repo instantly affected every project.
- Deleting or moving the Archon checkout silently broke every project that
  was symlinked to it.

v0.1.0 uses **copies** instead. Each project gets its own independent copy
of the prompts, `CLAUDE.md`, the informal agent, and the skills plugin.
This removes the fragility and lets you safely edit prompts per-project,
but it also means template updates no longer propagate automatically — you
pull them in by re-running `archon init` and choosing **merge** or
**overwrite**.

### 1.3 Dashboard: manual → auto-launch

`archon loop` now launches the web dashboard in the background on a free
port in 8080–8099 by default and prints the URL. The dashboard keeps
running after the loop finishes so you can inspect results. Pass
`--no-dashboard` to disable, or `--open` to open a browser automatically.

### 1.4 Re-init is now safer

Running `archon init` on a project that was already initialized — by this
or an older version — no longer errors or overwrites your edits silently.
It detects the existing setup and offers four choices:

- **keep** — leave files alone; just refresh MCP / plugin registrations.
- **merge** *(recommended)* — launch Claude Code in a focused diff session
  and reconcile each prompt / `CLAUDE.md` file interactively.
- **overwrite** — replace all Archon files with the bundled versions
  (discards local edits to prompts and `CLAUDE.md`).
- **abort** — cancel without changes.

User state (`PROGRESS.md`, `USER_HINTS.md`, `task_pending.md`, `task_done.md`,
`proof-journal/`) is preserved in all non-abort modes.

### 1.5 MCP / plugin registration is self-healing

The old MCP registration pointed into the Archon source checkout. If you
moved or deleted that checkout, the MCP server would silently break.
`archon init` now:

- Removes any existing `archon-lean-lsp` registration and re-adds it with
  the current install's path.
- Detects when the `archon-local` plugin marketplace points at a stale
  path and updates it.
- Disables conflicting global `lean4-skills` / `lean-lsp` plugins **for
  this project only** — your other projects are untouched.

---

## 2. Upgrading the tool

You do not need to uninstall the old scripts first — they live in your cloned checkout and are inert once you stop running them.

### 2.1 Fresh install

If you never ran Archon via pip before:

```bash
curl -sSL https://raw.githubusercontent.com/frenzymath/Archon/refs/heads/main/install.sh | bash
```

This fetches the repository, runs `pip install .`, and executes
`archon setup` to install system dependencies. We recommend using a
dedicated virtual environment (e.g. `python -m venv ~/.venvs/archon &&
source ~/.venvs/archon/bin/activate`) before running the installer.

### 2.2 If you already installed a preview CLI build

If you installed one of the preview builds from the PR branch, update with:

```bash
archon update
```

This re-runs the installer against `main`.

### 2.3 Verify the install

```bash
archon --help
archon doctor
```

If `archon doctor` is happy, you're ready to migrate your projects.

---

## 3. Upgrading an existing project

Do this for each project where you previously ran the old `init.sh`.

### 3.1 Before you start

**Backup, commit and push your project.** Both the recommended merge flow and the
fallback overwrite flow only touch `.archon/` and your project-scope Claude
Code registrations, not your `.lean` files — but you should have a clean
checkpoint anyway before running any tool that edits project state.

Commit `lean` files:

```bash
cd /path/to/your-lean-project
git add .
git commit -m "Backup before Archon CLI migration"
git push
```

Back-up `.archon/` state files:

```bash 
cp -r .archon/ .archon-backup/
```

If you have customizations under `.archon/prompts/` or in `.archon/CLAUDE.md`
that you want to keep, be aware that currently `.archon/` is gitignored.

### 3.2 Run `archon init`

```bash
archon init /path/to/your-lean-project
```

Archon detects the existing setup and prints something like:

```
⚠ This project has already been initialized with Archon.
  Detected layout:        legacy-symlink
  Current stage:          prover
  Prompts are symlinks:   yes

Detected the legacy symlink-based layout. The new CLI copies prompts
into .archon/prompts/ instead of symlinking. Re-initializing directly
would break the old symlinks.

How would you like to proceed?
  [k] keep
  [m] merge      (recommended)
  [o] overwrite
  [a] abort
```

The right choice depends on what you've edited:

| Situation | Choose |
|-----------|--------|
| You never edited anything under `.archon/prompts/` or `.archon/CLAUDE.md`. | **overwrite** |
| You edited some prompts and want to review the differences. | **merge** |
| You want to keep your current setup and only refresh registrations. | **keep** |
| You are not sure. | **merge** |

### 3.3 The merge flow in detail

When you pick `merge`, Archon:

1. Copies the new bundled prompts and `CLAUDE.md` to a staging directory
   (`.archon/.archon-incoming/`).
2. Launches Claude Code with a focused prompt.
3. For every file that differs, Claude summarizes the changes and asks you
   to choose:
   - `[L]` keep your local version
   - `[N]` take the new bundled version
   - `[M]` merge manually — Claude writes a proposed merge and stops so you
     can review it in your editor
4. Cleans up the staging directory when done.

Claude is instructed to never touch `PROGRESS.md`, `USER_HINTS.md`,
`task_pending.md`, `task_done.md`, `proof-journal/`, or any `.lean` file.
Only prompts and `CLAUDE.md` are in scope.

If Claude Code is not installed (it should be, if `archon setup` succeeded),
the merge step falls back to a text-only diff summary.

### 3.4 After init completes

`archon init` will:

- Finish by running `/archon-lean4:doctor` to verify Lean, MCP, and skills
  are healthy.
- Print the next step: `archon loop /path/to/your-lean-project`.

You can now run the loop as usual.

---

## 4. Things you can safely delete

Once the new CLI is installed and your projects have been re-initialized,
the following are no longer needed and can be removed:

- Your old Archon source checkout (if you installed via `pip install .`
  from it, the package has been copied into your Python environment — the
  checkout itself is no longer referenced).
- Any shell aliases or scripts that called `archon-loop.sh`, `init.sh`, or
  `review.sh` directly.
- The `.archon/prompts/` directory content *in projects you have already
  migrated* — but leave the directory itself alone; `archon init` manages
  it. (If you're worried, just leave it; stale symlinks are cleaned up on
  the next `init`.)

Do **not** delete `<project>/.archon/PROGRESS.md`,
`<project>/.archon/USER_HINTS.md`, `<project>/.archon/task_*.md`, or
`<project>/.archon/proof-journal/` — these contain your formalization state.

---

## 5. Troubleshooting

### `archon: command not found` after install

The `install.sh` script runs `pip install .` into whichever Python
environment is active when you invoke it. If you ran it inside a venv,
`archon` is only on your PATH when that venv is active. Activate it, or
install into a more permanent location and ensure that location's `bin/`
is on your PATH.

### `Claude Code is not installed`

Run `archon setup` — it will install `uv` and Claude Code and verify your
Lean toolchain. By default it asks before running `sudo`; pass `--yes` to
accept automatically.

### Merge mode shows "Claude Code is not installed — falling back to a text-only diff summary"

Install Claude Code via `archon setup`, then re-run `archon init` and
choose **merge** again.

### `archon-lean-lsp` does not appear in `claude mcp list`

Run `archon init` again. v0.1.0 explicitly removes and re-adds the
registration so the path always points at the current install.

### The dashboard did not start

Check that Node.js and npm are installed (run `archon setup` if not), and
that at least one port in 8080–8099 is free. If neither applies, pass
`--no-dashboard` to `archon loop` and start it manually in another
terminal with `archon dashboard /path/to/your-lean-project`.

### I accidentally chose overwrite and lost my prompt edits

If you committed your project before migrating (section 3.1), please note that `.archon` is gitignored by default, so you should create a backup beforehand if you want to recover your old prompts.

### I initialized successfully but the loop complains about the stage being "init"

The interactive init step did not complete. Re-run `archon init` and make
sure to finish the Claude Code session (it will ask you to confirm initial
objectives and then write them to `PROGRESS.md`).

---

## 6. Rolling back

If the migration goes sideways and you want to return to the previous
state of a project:

```bash
cd /path/to/your-lean-project
cp -r .archon-backup/ .archon/
```

Where `.archon-backup/` is a copy of `.archon/` from before the migration. Note that by default `.archon/` is gitignored.
The MCP and plugin registrations can be refreshed by running
the old `init.sh` again from your former Archon checkout, or by
re-running `archon init` and picking **keep**.

To roll back the tool install itself:

```bash
pip uninstall archon
```

Then reinstall whichever version you were on previously.

---

## 7. Upgrading from v0.1.0 to v0.2.0

v0.2.0 adds multi-lane proving, the refactor agent, inner-git versioning of
agent work, and a frozen-signature surface (`archon-protected.yaml`). Most of
this is transparent — your existing v0.1.0 projects keep working — but a few
things are worth doing once per project to pick up the new behaviour.

### 7.1 Reinstall the CLI

```bash
archon update
```

If you originally installed without `archon update` available, run the
one-line installer again — it is idempotent.

### 7.2 Re-run `archon init` in each project (recommended)

The prompts and `CLAUDE.md` template gained several pieces of guidance in
v0.2.0 — iteration-number canonicalization, LaTeX-macro hygiene, and a rule
against listing off-limits files in `## Current Objectives`. The plan agent
also now picks up the bundled dependency-graph script. To pull these into a
project initialised under v0.1.0:

```bash
archon init /path/to/your-lean-project
```

Pick **merge** (recommended) — Archon walks you through the diffs file by
file. Your `PROGRESS.md`, `task_*.md`, `proof-journal/`, and `.lean` files
are never touched.

### 7.3 New files inside `.archon/` after v0.2.0 init

| File | What it is | Edit? |
|------|------------|------|
| `.archon/git-dir/` | Inner git repo. Every agent phase commits here as `archon[NNN/phase]`. | No — managed by Archon. |
| `.archon/config.json` | Per-project loop and multilane settings. Versioned with your project. | Yes — see [MULTILANE.md](https://github.com/frenzymath/Archon/blob/main/src/archon/.archon-src/archon-template/MULTILANE.md). |
| `.archon/.env` | API keys for the informal agent and multilane providers. | Yes — gitignored, never commit. |
| `.archon/REFACTOR_DIRECTIVE.md` | Where the plan agent writes refactor directives. Cleared after each refactor pass. | Plan agent writes; you can read for context. |
| `.archon/STRATEGY.md` | Plan agent's living long-arc plan. | Plan agent owns; you can read. |
| `.archon/VERSION` | Stamped at init time so re-init knows what version produced the project. | No. |

### 7.4 New file at the project root: `archon-protected.yaml`

If you want to freeze certain declaration signatures from agent edits, add
them here. Example:

```yaml
src/MyProject/Core.lean:
  - main_theorem
  - key_definition
```

Agents will refuse to rename or re-sign listed declarations. The refactor
agent may move them between files (and update the file path key in this
yaml) but cannot otherwise touch them. The file is committed to the project
git so the whole team shares the protected surface.

`archon init` writes an empty `archon-protected.yaml` if none exists; fill it
in when you are ready.

### 7.5 The CLI gained four commands

| Command | What it does |
|---------|--------------|
| `archon refactor /path/to/project` | Run only the refactor phase against the current `REFACTOR_DIRECTIVE.md`. |
| `archon discuss /path/to/project` | Open Claude Code interactively with full Archon context loaded — for debugging or brainstorming without firing the loop. |
| `archon branch <name> /path/to/project` | Create a new branch in the inner git from a historical agent commit (e.g. before a bad refactor). |
| `archon version /path/to/project` | Show the Archon CLI version and, in a project, the project version. |

### 7.6 Enabling subagents (optional)

v0.2.0 introduces an opt-in **subagent system** — descriptor-driven
helpers the plan / review agent can dispatch when it needs a focused,
fresh-context check. **All ship disabled** so the loop behaves exactly as
it did in v0.1.0 unless you opt in. To turn one or more on, edit
`.archon/config.json`:

```json
"subagents": {
  "enabled": ["strategy-critic", "blueprint-reviewer", "progress-critic"]
}
```

The shipped `config.json` includes an `_available` list naming every
shipped subagent; copy any of them into `enabled`. Recommended starting
sets:

- **Plan phase**: `blueprint-reviewer`, `strategy-critic`, `progress-critic`
- **Review phase**: `lean-auditor`, `lean-vs-blueprint-checker`

Subagents with `mandatory: [<phase>]` in their frontmatter must be
dispatched at least once when enabled. The plan / review prompts surface
them with a `[MANDATORY]` tag and a post-phase audit warns (does not
abort) when one is missed. With every subagent disabled, the catalog is
empty and no mandatory dispatch is ever required.

To enable every shipped subagent at once, copy the entire `_available`
list into `enabled`.

### 7.7 `max_parallel` default lowered from 8 to 4

Fresh projects pick up `max_parallel: 4` in `.archon/config.json`.
**Existing projects keep whatever value they already have** — re-running
`archon init` with `keep` or `merge` preserves your current setting. To
restore v0.1.0 behavior on a fresh project, either set:

```json
"loop": { "max_parallel": 8 }
```

or pass `--max-parallel 8` on the command line.

### 7.8 `ui/start.sh` removed

If you previously launched the dashboard via `ui/start.sh`, use the CLI
instead:

```bash
archon dashboard /path/to/your-lean-project
```

(`archon loop` already auto-launches it — this only matters if you were
starting the dashboard standalone.)

### 7.9 New `--resume` flag

| Flag | What it does |
|------|--------------|
| `--resume` | When a previous `archon loop` was interrupted mid-iteration, resume the in-flight iteration at its last completed phase. The phase is auto-detected from `.archon/iter/iter-NNN/meta.json`. |

### 7.10 New blueprint-doctor phase

Runs automatically at the top of each iteration. It scans `blueprint/src/`
for orphan files, broken `\uses{...}` references, and missing
`\lean{...}` blocks; the plan agent then sees the findings inline under
`## Blueprint doctor — live structural findings`. No configuration is
needed — it's silently included in every iteration.

---

## 8. Upgrading to v0.3.0

v0.3.0 adds a configurable Claude backend for alternative headless
entrypoints and hardens stage detection.

### 8.1 Reinstall the CLI

```bash
archon update
```

### 8.2 Configurable Claude backend

You can now use alternative entrypoints for the headless `claude -p` command
via the new `--claude-backend` CLI flag or by setting `loop.claude_backend`
in `.archon/config.json`. This is useful for environments like VS Code or
Claude Desktop.

Supported values:
- `default`: plain `claude -p` (default)
- `vscode`: sets `CLAUDE_CODE_ENTRYPOINT=claude-vscode`
- `desktop`: sets `CLAUDE_CODE_ENTRYPOINT=claude-desktop`

### 8.3 Hardened stage detection

Stage detection in `PROGRESS.md` is now more resilient. Human or agent
annotations (e.g., dates or iteration numbers) appended after the stage token
are ignored by the orchestrator, preventing the loop from stalling on
unexpected input.

---

## Questions or issues

Please open an issue on the
[Archon repository](https://github.com/frenzymath/Archon/issues) and
describe what you ran, what you expected, and what you saw. Include the
output of `archon doctor` if possible.
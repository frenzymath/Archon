---
name: golf
description: "Minimize proof term size for already-compiled proofs. No structural changes, no signature modifications."
compatible_stages:
  - prover
  - polish
read_blueprint: false
dispatcher_notes: |
  Use when a file compiles without sorry and the planner wants proof minimization specifically.
  Narrower than `polish` (golf only — no Mathlib refactor, no helper extraction).
  Good for a targeted pass after the proof is complete but verbose.
  Do NOT use when sorries still exist.
---

## Your goal

Minimize the size and verbosity of proof terms for declarations in your assigned `.lean` file that already compile without `sorry`. You must not introduce new sorries, change any signature, or alter any statement. The file must compile after every change.

## Workflow

1. Read `PROGRESS.md` for your current objectives (read only — do not edit it).
2. Read `task_pending.md` for context.
3. Check the `.lean` file for `/- USER: ... -/` comments.
4. For each declaration in scope (those listed in your objective, or all if unspecified):
   a. Check that it has no `sorry` and compiles — if it does have sorry, skip it and note it.
   b. Attempt to shorten the proof using the strategies below.
   c. Verify compilation after every change.
5. Write results to `task_results/<your_file>.md`.

**Write permissions**: only your assigned `.lean` file(s) and `task_results/<your_file>.md`. Do NOT edit `PROGRESS.md`, `task_pending.md`, other agents' files, or the blueprint chapter.

## Golfing strategies (in order of reliability)

1. **Terminal tactics**: replace manual tactic chains with `simp`, `ring`, `omega`, `norm_num`, `decide`, `aesop`, `tauto`, `linarith` where they close the goal outright.
2. **`simp only` narrowing**: replace `simp` (which can be fragile) with `simp only [...]` listing exactly the lemmas that fired — reduces proof brittleness and is often shorter.
3. **`exact?` / `apply?`**: use LSP-assisted search to find a single Mathlib lemma that closes a goal currently requiring multiple steps.
4. **Term-mode for trivial goals**: replace `by exact h` or `by rfl` with the term directly.
5. **`calc` compression**: if a `calc` chain has two steps that share a lemma, merge them.
6. **Inline private helpers**: if a `private lemma` is used exactly once and its proof is short, inline it.

## Hard constraints

- **No new `sorry` or axioms**.
- **No signature changes** — not even cosmetic renaming of arguments.
- **No statement changes** — not even simplifying a hypothesis to a defeq form.
- **No structural refactoring** — do not extract new helpers, do not reorganize file structure. That is `polish` mode.
- **Verify after each declaration** — never batch changes; a broken file is harder to diagnose.

## When golfing fails

If a proof resists all golfing attempts (it's already near-minimal, or the tactics that would shorten it require imports that make it longer), record that in your log and move on. Do not force a golf that introduces fragility.

## Protected declarations

Read `archon-protected.yaml`. Proof bodies of protected declarations may be golfed; signatures may not change.

## LSP MCP tools

The `archon-lean-lsp` server exposes Lean LSP operations as **MCP tool calls** (`mcp__archon-lean-lsp__lean_diagnostic_messages`, `mcp__archon-lean-lsp__lean_goal`, etc.). Never call them as shell commands.

- First LSP action: `mcp__archon-lean-lsp__lean_diagnostic_messages` on your file. If `success: false`, retry or `lake build` first.

## Logging

```markdown
# Algebra/WLocal.lean

## wLocal_iff — golf pass
- **Before:** 42 lines (manual case split + 6 `have` steps)
- **After:** 11 lines (`simp only [IsLocalRing.iff_unique_maximalIdeal, ...]`)
- **Strategy:** `exact?` found `PrimeSpectrum.comap_injective` covering steps 2–4

## helper_bijective — golf pass
- **Before:** 18 lines
- **After:** 18 lines (no improvement — proof is already minimal; `omega` and `simp` don't apply)
- **Note:** already near-minimal; suggest leaving as is.
```

## End-of-session handoff

Before stopping:

1. Verify the file compiles (no sorries, no axioms).
2. Write `task_results/<your_file>.md` with before/after line counts for each declaration attempted.
3. **Write a `## Why I stopped` section** with one of:
   - `Real progress`: N declarations shortened; list them with before/after sizes.
   - `No improvement possible`: proofs are already near-minimal — describe what was tried.
   - `Skipped sorry declarations`: file still has sorries; list them so the planner knows.
   - `Directive not followed`: explain the deviation.

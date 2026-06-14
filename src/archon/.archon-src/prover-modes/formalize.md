---
name: formalize
description: "Read the blueprint chapter and introduce matching Lean declaration stubs with sorry bodies. Default mode for the autoformalize stage."
compatible_stages:
  - autoformalize
default_for_stages:
  - autoformalize
read_blueprint: true
dispatcher_notes: |
  Use when a Lean file is empty or missing declarations that the blueprint chapter already describes.
  Do NOT use when stubs already exist — switch to `prove` or `fine-grained` instead.
  The output is always a compiling file with sorry stubs; no proofs are expected.
---

## Your goal

Read the blueprint chapter for your assigned file and introduce a matching Lean declaration (with a `sorry` proof body) for every `\begin{theorem}` / `\begin{lemma}` / `\begin{definition}` block in the chapter. The file must compile cleanly with sorries in place when you stop.

## Workflow

1. Read `PROGRESS.md` for your current objectives (read only — do not edit it). Note the blueprint chapter path your objective points to.
2. **Read your blueprint chapter** — `blueprint/src/chapters/<your_slug>.tex`, where `<your_slug>` is your Lean file path with `/` replaced by `_` and `.lean` stripped (e.g. `Algebra/WLocal.lean` → `Algebra_WLocal.tex`). This chapter is the source of truth for what declarations this file should contain.
3. Read `task_pending.md` for context from prior sessions.
4. Check the `.lean` file for `/- USER: ... -/` comments for file-specific hints.
5. For each blueprint block, introduce a matching Lean declaration with a `sorry` proof body.
6. Verify the file compiles.
7. Write results to `task_results/<your_file>.md`.

**Write permissions**: only your assigned `.lean` file(s) and `task_results/<your_file>.md`. Do NOT edit `PROGRESS.md`, `task_pending.md`, other agents' files, or the blueprint chapter — blueprint markers (`\leanok`, `\mathlibok`) are the review agent's responsibility.

## Protected declarations

Before touching any `.lean` file, read `archon-protected.yaml`. If a chapter block points (via `\lean{...}`) to a name already listed there with a different signature, do NOT change that signature. Keep the existing declaration, align your stub around it, and note the discrepancy in your task result.

## Blueprint alignment

Do not edit the blueprint chapter. Your only interaction with it is to **read** it.

If the informal statement maps cleanly to an existing Mathlib definition or lemma, note it in your task result so the review agent can add `\mathlibok`. Do not introduce redundant declarations when Mathlib already has them — stub the equivalence instead.

## Naming and Mathlib

- Prefer Mathlib names and definitions when the blueprint's notion matches.
- Use mathematically meaningful names; avoid ad-hoc problem-specific names.
- **Never modify working proofs** — if a declaration already has no `sorry` and compiles, leave it alone.

## LSP MCP tools

The `archon-lean-lsp` server exposes Lean LSP operations as **MCP tool calls** (`mcp__archon-lean-lsp__lean_diagnostic_messages`, etc.). Never call them as shell commands.

- First LSP action: `mcp__archon-lean-lsp__lean_diagnostic_messages` on your file. If `success: false`, retry once or `lake build` then retry.

## Logging

Write to `task_results/<your_file>.md`:

```markdown
# Algebra/WLocal.lean

## Summary
- Added N theorem/lemma/definition stubs from blueprint chapter Algebra_WLocal.tex
- All stubs compile with `sorry`

## Stubs created (for review agent)
1. `Algebra.WLocal.wLocal_iff` — from `thm:wLocal_iff`. Statement formalized. Review agent: add `\leanok` to statement block.
2. `Algebra.WLocal.finite_closed` — backed by existing Mathlib lemma `Set.Finite.isClosed`. Review agent: add `\mathlibok`.

## Skipped / Deferred
- `thm:stacks_0A31` — could not formalize; blueprint statement uses category-theoretic phrasing that doesn't map cleanly yet.
```

## End-of-session handoff

Before stopping:

1. Verify the file compiles (all declarations present, only `sorry` bodies).
2. Write `task_results/<your_file>.md` listing which blocks became which Lean declarations, which are backed by Mathlib, and which did not translate.
3. **Write a `## Why I stopped` section** with one of:
   - `Real progress`: N stubs introduced and compiling.
   - `Partial progress`: some stubs introduced; list what's missing and why.
   - `Blueprint unreadable`: describe the specific parsing difficulty.
   - `Directive not followed`: explain the deviation.

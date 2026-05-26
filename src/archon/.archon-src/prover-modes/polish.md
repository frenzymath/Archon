---
name: polish
description: "Verify, golf, and refactor complete proofs. Default mode for the polish stage."
compatible_stages:
  - polish
default_for_stages:
  - polish
read_blueprint: true
dispatcher_notes: |
  Use only when the file already has no sorry and compiles cleanly.
  For targeted proof minimization without structural refactoring, prefer `golf`.
  Do NOT use when sorries still exist — switch to `prove` or `fine-grained` first.
---

## Your goal

Verify, clean, and improve already-compiled proofs in your assigned `.lean` file. No new sorries may be introduced; no signatures may change. The blueprint chapter alignment must be preserved.

## Workflow

1. Read `PROGRESS.md` for your current objectives (read only — do not edit it).
2. **Read your blueprint chapter** — `blueprint/src/chapters/<your_slug>.tex` — to understand the intended proof structure. Your polishing must preserve alignment between Lean proofs and the chapter's blueprint labels.
3. Read `task_pending.md` for context from prior sessions.
4. Check the `.lean` file for `/- USER: ... -/` comments for file-specific hints.
5. Verify compilation and confirm absence of `sorry`, `axiom`, and other escape hatches.
6. Perform code quality improvements:
   - Golf proofs for brevity and clarity (`/lean4:golf`)
   - Refactor to leverage Mathlib (`/lean4:refactor`)
   - Extract reusable helpers from long proofs
7. Verify compilation after each change.
8. Write results to `task_results/<your_file>.md`.

**Write permissions**: only your assigned `.lean` file(s) and `task_results/<your_file>.md`. Do NOT edit `PROGRESS.md`, `task_pending.md`, other agents' files, or the blueprint chapter — marker updates are the review agent's responsibility.

## Protected declarations

Read `archon-protected.yaml` before editing. In polish mode you must especially avoid signature drift: do not rename, re-type, or reorder arguments of protected declarations, even if golfing suggests a cleaner signature. Protected signatures are contractual with the mathematician.

## Constraints

- Do NOT introduce new `sorry` or axioms.
- Do NOT modify declaration signatures or statement types.
- Proof bodies and intermediate helpers may be freely improved.
- Keep edits minimal: do not delete comments or change blueprint labels.
- Verify compilation after each change.
- Do NOT touch the blueprint chapter.

## Blueprint alignment

Do not edit the blueprint chapter. Record in `task_results/<your_file>.md` which declarations are now fully polished. If you notice a stale marker in the blueprint (e.g. `\leanok` on a theorem whose Lean name has drifted), flag it in your task result — the review agent will fix it.

## LSP MCP tools

The `archon-lean-lsp` server exposes Lean LSP operations as **MCP tool calls** (`mcp__archon-lean-lsp__lean_diagnostic_messages`, etc.). Never call them as shell commands.

- First LSP action: `mcp__archon-lean-lsp__lean_diagnostic_messages` on your file. If `success: false`, retry once or `lake build` then retry.

## Logging

```markdown
# Algebra/WLocal.lean

## wLocal_iff
### Polish pass
- **Golf**: reduced proof from 42 lines to 18 using `simp only`
- **Verified**: compiles, no sorries, no new axioms
- **For review agent**: proof block of `thm:wLocal_iff` ready for `\leanok` on the `\begin{proof}`.

## Blueprint status (for review agent)
- 2/2 declarations fully polished. Both proof blocks ready for `\leanok`.
```

## End-of-session handoff

Before stopping:

1. Verify the file still compiles (no sorries, no axioms).
2. In `task_results/<your_file>.md`, list each declaration: fully polished (ready for `\leanok` on proof block) or still needs work (and why).
3. **Write a `## Why I stopped` section** with one of:
   - `Real progress`: N declarations polished; list them.
   - `Partial progress`: some declarations polished; others blocked — name the blockers.
   - `Cosmetics only`: only formatting/comment changes with no proof improvement — say so.
   - `File already clean`: file was already sorry-free and required no further work.
   - `Directive not followed`: explain the deviation.

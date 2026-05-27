---
name: prove
description: "Fill sorry placeholders with complete Lean proofs. Default mode for the prover stage."
compatible_stages:
  - prover
default_for_stages:
  - prover
read_blueprint: true
dispatcher_notes: |
  Default mode — use unless a more specific mode fits better.
  Prefer `fine-grained` when a theorem is large and previous prover passes made no visible progress.
  Prefer `skeletize` when no stub decomposition exists yet and the theorem is too large to attack whole.
  Prefer `mathlib-build` when the sorry is blocked because a required Mathlib lemma does not exist —
  the prover's job is then to build that ingredient axiom-clean, not to close the sorry with a typed pin.
---

## Your goal

Fill `sorry` placeholders with complete Lean proofs in your assigned `.lean` file.

## Workflow

1. Read `PROGRESS.md` for your current objectives (read only — do not edit it).
2. Read `task_pending.md` for prior attempts, dead ends, and lemmas already found.
3. Check the `.lean` file for `/- USER: ... -/` comments (file-specific user hints).
4. **Read the relevant blueprint chapter before writing Lean code.** The chapter holds the mathematical proof sketch you must align with. When stuck, re-reading it is often the fastest path forward.
5. Replace `sorry` with Lean proofs. Push as far as possible.
6. **Always save partial progress.** If you can't fully close a sorry, leave your best attempt — commented-out steps, helper lemmas, partial `by` blocks with `sorry` at the stuck point. The file must still compile, but your work must be visible for the next agent to continue from. NEVER revert to a bare `sorry` — that erases real work.
7. Write your results to `task_results/<your_file>.md`.

**Write permissions**: only your assigned `.lean` file(s) and `task_results/<your_file>.md`. Do NOT edit `PROGRESS.md`, `task_pending.md`, `task_done.md`, blueprint chapters, or other agents' files.

## Protected declarations

Read `archon-protected.yaml` before touching any declaration. You may fill proof bodies of protected declarations but must not rename, re-type, reorder arguments, or weaken hypotheses. Only the mathematician edits protected signatures.

## Avoid early termination

- Don't abandon a proof prematurely. Many complex proofs run to thousands of lines.
- Difficulty is NOT a valid reason to leave a `sorry`.
- Don't delegate to "the next iteration" or "another prover" if more effort could close it.
- Only modify the proof for your assigned task — leave unrelated proofs untouched.
- **Decompose**: break into smaller sub-problems (following the blueprint's lemma structure when available) and solve each individually.
- **Hard bar is a minimum, not a ceiling.** If your objectives specify a "hard bar" (e.g. "add def + pin signature"), that tells you the minimum required — not where to stop. After meeting it, if a recipe exists in `analogies/`, the blueprint chapter has a concrete proof sketch, or you can see a path forward, use your remaining budget to attempt the body. Leave partial progress (partial tactic block, helper lemma, named subgoal that compiles) rather than a bare `sorry`. Partial progress from a real attempt is far more useful to the next iter than a clean stop.

## Completion criteria

Your task is complete ONLY when ALL of:

1. Every `sorry` in scope is replaced with a complete proof.
2. Zero axioms introduced.
3. The file compiles cleanly.

## Never weaken the type to dodge the proof

When the substantive type is unattainable this iter, leave `sorry` with the **intended type signature**. Three patterns are banned:

- **Reflexive-iso placeholder** — replacing `X ≅ Y` with `Nonempty (X ≅ X) := ⟨Iso.refl _⟩`.
- **`Classical.choice` around an explicit witness** — dissolving `Type` vs `Prop` friction without actually constructing the witness.
- **Empty-content `proof_wanted`** — discards the declaration post-elaboration, breaking blueprint cross-references.

**Litmus test**: if you `unfold` your declaration, does it expose the named substantive content or does it stop at `Classical.choice` / `Iso.refl _` / nothing? If the latter, ship the typed `sorry` instead.

## When infrastructure is missing

Do NOT report "Mathlib lacks X" and stop. Before giving up:

1. **Use the informal agent** (`.claude/tools/archon-informal-agent.py`): "Prove [goal] without using [missing infrastructure], only Mathlib." Even an imperfect sketch is valuable.
2. Formalize whatever the informal agent suggests.
3. **If you still can't**: write the alternative sketch to `informal/<theorem_name>.md` and record what you tried, why it failed, AND the alternative route you found.

When stuck more generally: break into smaller subgoals, search Mathlib more thoroughly, prove missing helpers yourself, try alternative strategies, re-read the blueprint, use Web Search for published proofs.

**Impossibility vs difficulty**: technical difficulty → keep trying. Mathematical impossibility → immediately backtrack and document why.

## Proof style

- **Never modify working proofs** — if a declaration has no `sorry` and compiles, do not touch its body.
- Keep edits minimal; don't delete comments or change labels; don't add unrelated declarations.
- Helper lemmas you introduced may be modified if they turn out wrong.
- Add a concise comment above each helper lemma so reuse is easy.
- **`change` vs `show`** — `change` reshapes the goal up to defeq; `show` is display-level only. Default to `change` when in doubt.

## Mathlib tags in PROGRESS.md

The plan agent tags suggested lemmas:

- `[verified]` — confirmed to exist.
- `[expected]` — guessed by naming convention. Quick `lean_local_search`; pivot if it doesn't exist.
- `[gap]` — verified NOT in Mathlib. Don't waste search time; formalize a workaround.

## LSP MCP tools

The `archon-lean-lsp` server exposes Lean LSP operations as **MCP tool calls** (`mcp__archon-lean-lsp__lean_goal`, `mcp__archon-lean-lsp__lean_diagnostic_messages`, etc.). The lean4 skill reference uses short names (`lean_goal`, …) — same tools, never shell binaries.

- Always invoke through your tool-call interface.
- **Never** call `Bash` with `lean_goal …` — there is no such shell command.
- First LSP action: `mcp__archon-lean-lsp__lean_diagnostic_messages` on your file. If `success: false`, retry once or run `lake build` once via Bash, then retry.

## Search protocol

1. `lean_local_search` first.
2. `lean_leansearch` for semantic search — describe the mathematical content, not just the name.
3. `lean_loogle` for simple type patterns only.
4. Never use shell `find` / `grep` to locate Mathlib theorems.

## Tooling traps

- **Trust `goals_after`, not just empty diagnostics**: in `lean_multi_attempt`, `diagnostics: []` can be misleading. Check that `goals_after` is empty or has advanced.
- **Beware `lean_run_code` with imports**: standalone snippets can silently swallow elaboration errors. Rely on `lean_diagnostic_messages` for authoritative verification.

## Logging

Write to `task_results/<your_file>.md` (mirror your `.lean` path: `Algebra/WLocal.lean` → `task_results/Algebra_WLocal.lean.md`):

```markdown
# Algebra/WLocal.lean

## wLocal_iff (line 45)
### Attempt 1
- **Approach:** Direct case split on maximal ideals
- **Result:** FAILED — needed IsLocalRing instance
- **Dead end:** direct case split without IsLocalRing

### Attempt 2
- **Approach:** Stacks 0A31, characterize via bijection on spectra
- **Result:** RESOLVED
- **Key insight:** `PrimeSpectrum.comap_injective` bridges the gap
```

One section per theorem/lemma. Each attempt: approach, result (RESOLVED / FAILED / PARTIAL / IN PROGRESS), dead-end warnings or next steps. Log negative search results.

## End-of-session handoff

Before stopping:

1. Write `task_results/<your_file>.md` with current result, lemmas discovered, concrete next step, dead-end warnings.
2. Save all changes; ensure the file compiles.
3. **Write a `## Why I stopped` section** in your task result with one of the following verdicts — be honest, the planner reads this:
   - `Real progress`: closed N sorries, specific ones named.
   - `Partial progress`: made measurable progress (e.g. decomposed into sub-lemmas, closed one branch) but did not fully close.
   - `Cosmetics only`: changed formatting/comments/style with no proof progress — say so explicitly.
   - `Avoided the goal`: attempted something adjacent but not the assigned target — explain why and what you tried instead.
   - `Infrastructure missing`: a specific Mathlib gap was the blocker — name it, describe the alternative attempted.
   - `Directive not followed`: explain which part of the planner's directive you deviated from and why.

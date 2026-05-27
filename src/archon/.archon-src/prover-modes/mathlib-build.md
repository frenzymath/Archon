---
name: mathlib-build
description: "Build missing Mathlib infrastructure axiom-clean, step by step, as far as possible. No sorry in output — each step is either fully proved or absent."
compatible_stages:
  - autoformalize
  - prover
  - polish
read_blueprint: true
dispatcher_notes: |
  Use when the objective is to grow project-local Mathlib infrastructure
  rather than close a project sorry directly. The prover works bottom-up,
  building a chain of axiom-clean definitions and lemmas, going as far as
  it can in one iteration. It stops only when genuinely blocked — not after
  a single hard step — and hands off a precise decomposition so the planner
  can assign the next step.
  Do NOT use this mode to close a project sorry that already has a recipe —
  use `prove` or `fine-grained` for that.
---

## Your goal

Build project-local Mathlib infrastructure axiom-clean, one step at a time, going **as far as possible** in this iteration. Your output consists of definitions and lemmas that compile without `sorry` and whose `#print axioms` shows only `{propext, Classical.choice, Quot.sound}`. You stop only when you hit a genuine mathematical blocker — not because one step is hard.

## Invariant

**No `sorry` in your output.** Every declaration you add must be fully proved. If you cannot close the current step: try alternatives first, then either prove a smaller sub-step or leave the declaration absent. Never add a declaration with `sorry`.

## Workflow

### 1. Orient

- Read `PROGRESS.md` for the objective: what infrastructure is needed, and why.
- Read the relevant blueprint chapter. It often contains the mathematical path to the ingredient you're building.
- Read `task_pending.md` for prior dead ends. Do not repeat them.

### 2. Scout the Mathlib API

Before writing any Lean, search for what already exists:

- `lean_local_search` for related names.
- `lean_leansearch` with a description of the mathematical content.
- `lean_loogle` for simple type patterns.
- Look for near-misses: a lemma that covers a sub-case, an instance you can compose, a `simp` set that contains what you need.

Even if you think you know the API — search anyway. Signatures change, instances disappear.

### 3. Build bottom-up, step by step

Start from the deepest missing piece identified in your scouting. For each step:

1. **Draft the type signature** and check it typechecks via `lean_diagnostic_messages` or `lean_run_code` before attempting the body.
2. **Write the proof.** Use Mathlib lemmas you verified exist. If a sub-step is missing, recurse: prove that sub-step first.
3. **Verify axiom-cleanliness** immediately after the step compiles: `#print axioms MyLemma`. Any `sorryAx` in the ancestry means the step is not axiom-clean — do not proceed until it is gone.
4. **Continue to the next step** in the chain.

Push as far as you can. Each axiom-clean step you add is permanent progress.

### 4. When stuck on a step — try before stopping

Before declaring a step impossible:

1. **Try a different proof route.** Reformulate the statement, weaken to a special case you can prove, use a detour through an equivalent form.
2. **Use the informal agent** (`.claude/tools/archon-informal-agent.py`): "Prove [goal] using only current Mathlib." Formalize whatever it suggests.
3. **Search more broadly.** The lemma you need might exist under a different name or in a namespace you haven't checked.
4. **Prove a strictly smaller sub-step** that is axiom-clean and genuinely useful — something that shrinks the remaining gap.

Only after exhausting these alternatives do you stop.

### 5. Stopping

Stop when you have tried alternatives and cannot make further axiom-clean progress. Before stopping:

- Commit all axiom-clean steps you completed.
- Write `task_results/<your_file>.md` with a precise handoff (see Logging below).
- Ensure the file compiles with no `sorry`.

## File structure

Place new declarations under a clearly delimited section:

```lean
/-! ## Project-local Mathlib supplement — <TopicName> -/
```

- Use `private` for helpers with no downstream use outside this file.
- Use `theorem` / `lemma` (non-private) for steps that other files will import.
- Add a one-line docstring to each non-private declaration explaining why it is project-local.

## Protected declarations

Read `archon-protected.yaml` before touching any existing declaration. You may add new declarations freely. You must not rename, re-type, or modify signatures of protected declarations.

## API alignment

Mathlib names and signatures are the most common failure source.

- Verify every name with `lean_local_search` or `lean_loogle` — do not rely on memory.
- Check that typeclass assumptions in your statement match what Mathlib requires.
- For `def`-backed types: Lean may not unfold them for typeclass synthesis; use explicit coercions or `change` / `show` in the proof body.

## Write permissions

Only your assigned `.lean` file(s) and `task_results/<your_file>.md`. Do NOT edit `PROGRESS.md`, `task_pending.md`, `task_done.md`, blueprint chapters, or other agents' files.

## Logging

Write to `task_results/<your_file>.md` (mirror the `.lean` path):

```markdown
# Algebra/MathlibSupplement.lean

## Session summary
- Built axiom-clean: `Foo.bar`, `Foo.baz` (lines 12–40)
- Blocked on: `Foo.qux` — needs `Bar.aux` which requires [precise statement]

## Foo.bar (line 12)
- **Approach:** Direct from `Mathlib.Algebra.X` via `simp [Y]`
- **Result:** RESOLVED — axiom-clean

## Foo.qux (not added)
- **Approach 1:** Tried [route] — FAILED because [reason]
- **Approach 2:** Tried informal agent — suggested [sketch], formalization blocked on [specific gap]
- **Next step:** Build `Bar.aux : [precise type statement]` first
- **Dead end:** Do not retry [route] — [why it fails]
```

One section per declaration attempted. For each: approach, result (RESOLVED / FAILED / PARTIAL / NOT ADDED), dead-end warnings, next step.

## End-of-session handoff

The `## Why I stopped` section must use one of:

- `Real progress`: named the N axiom-clean declarations added.
- `Partial progress`: added some steps, named the specific blocker for the next one.
- `Blocked — alternatives exhausted`: named what was tried, why each failed, and the precise statement of the next needed ingredient so the planner can assign it.
- `Infrastructure already exists`: the scouting found that Mathlib already has what was needed — cite the exact lemma name.

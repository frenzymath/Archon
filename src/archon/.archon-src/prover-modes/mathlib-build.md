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
2. **Use the informal agent** if an API key is available (check `env | grep -E "DEEPSEEK|MOONSHOT|OPENROUTER|OPENAI|GEMINI"` first): `.claude/tools/archon-informal-agent.py` "Prove [goal] using only current Mathlib." Formalize whatever it suggests. If no key is set, skip to step 3.
3. **Search more broadly.** The lemma you need might exist under a different name or in a namespace you haven't checked.
4. **Prove a strictly smaller sub-step** that is axiom-clean and genuinely useful — something that shrinks the remaining gap.

Only after exhausting these alternatives do you stop. **Comments describing an approach you haven't tried are not exhausted alternatives — attempt them first.**

### 5. Stopping

**Before stopping, run this self-review:**

1. Did I attempt every approach I wrote in a comment, TODO, or log entry? If not, go back and attempt them.
2. Are there adjacent declarations in the chain I could attempt with the infrastructure I just built?
3. Did I call the informal agent (or document that no key was available) before declaring the step impossible?
4. Is there any approach I thought of but skipped? Attempt it — a failed attempt with a named error is better than a comment.

Stop only when all four answers are "yes" or "not applicable." **Writing a comment about a possible approach counts as identifying it — which means you should attempt it.**

**You are fully authorized to make every mathematical and structural decision within your assigned file without waiting for the planner.** "I'll let the planner decide which route to take" is never a valid stop reason — pick the most promising route, attempt it, document the outcome. The planner corrects route choices next iter; an idle session corrects nothing.

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

**Write a `## Summary` section** before the verdicts: declarations added (count + names), declarations blocked (count + why), sorry count before → after across your file.

**Write a `## Needs blueprint entry` section.** This mode is the biggest source of blueprint debt: every non-private definition/lemma you add is, by construction, infrastructure with **no blueprint block yet** — a `lean_aux` node invisible to the dependency graph (`archon dag-query unmatched` will list it) until the planner/reviewer blueprints it. List each new non-private declaration (name, file, and the facts its proof relies on) so the 1-to-1 Lean ↔ blueprint correspondence the dag agent built is restored next iter. Closing 0 project sorries while adding 3 unreported helpers does not advance the project — it quietly re-introduces the isolation the DAG phase worked to eliminate. Reporting them is mandatory.

**Write a `## Why I stopped` section** — be brutally honest. Only axiom-clean Lean declarations count as progress. Comments, sketches, and log prose do NOT. If you wrote down approaches you didn't attempt, say so:

- `Real progress`: N axiom-clean declarations added — name each one with its line number.
- `Partial progress`: added some declarations, hit a specific blocker on the next — name the blocker precisely (not "it's complex", but the exact type or missing ingredient).
- `Approaches written but not attempted`: identified routes in comments or log but did not attempt them — name each. This is valid but weak; the planner will re-assign with a directive to attempt them.
- `Blocked — alternatives exhausted`: informal agent called (or key unavailable, documented), alternative routes tried, all failed — name what was tried, why each failed, and the exact type statement of the next needed ingredient.
- `Infrastructure already exists`: Mathlib has what was needed — cite the exact lemma name and namespace.

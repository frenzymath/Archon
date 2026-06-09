---
name: fine-grained
description: "Turn each informal mathematical sentence from the blueprint into a named atomic lemma and prove each one individually."
compatible_stages:
  - prover
read_blueprint: true
dispatcher_notes: |
  Use when a theorem is complex AND previous prove passes made no visible proof progress
  (cosmetics only, or sorry count unchanged after a full session).
  Before launching this mode, ensure that the blueprint proof is rigorous and detailed enough to extract atomic sentences. 
  The key invariant: each target is one mathematical sentence — small enough to either close or fail fast.
---

## Your goal

Translate each sentence of the informal proof in the blueprint chapter into an atomic named Lean lemma, then prove each lemma independently. The theorem is never attacked whole. Progress is measured per-sentence, not per-theorem. If sentence themselves can be decomposed further, do so. When blocked, try decomposing the informal sentence further into sub-sentences leading to sub-lemmas, it is a recursive process. 

## Why this matters

Provers stall on large goals because the first difficult step blocks the rest. By forcing each sentence to become its own named claim, every step either closes quickly or fails with a precise, fixable error — instead of a vague "I can't prove this big thing."

## Workflow

1. Read `PROGRESS.md` for your current objectives (read only — do not edit it).
2. **Read your blueprint chapter sentence by sentence.** For the section corresponding to your assigned theorem, list every individual mathematical claim made in the informal proof (not just top-level lemmas — every "since X", "by Y", "note that Z" is a candidate).
3. Read `task_pending.md` for prior attempts and dead ends.
4. Check the `.lean` file for `/- USER: ... -/` comments.
5. For each identified sentence:
   a. Introduce a named `private lemma` encoding that sentence's claim.
   b. Immediately attempt to prove it — using LSP tools, Mathlib search, and tactic exploration.
   c. If it closes: mark RESOLVED in your log; move on.
   d. If it does not close within reasonable effort (e.g., trying decomposing further): leave a `sorry` with the partial tactic attempt visible (never revert to bare `sorry`), naming the specific blocker; move on to the next sentence.
6. After all sentences are attempted, assemble the main theorem from the sub-lemma names (the assembly itself may use `sorry` if some sub-lemmas are still open).
7. Verify the file compiles.
8. Write results to `task_results/<your_file>.md`.

**Write permissions**: only your assigned `.lean` file(s) and `task_results/<your_file>.md`. Do NOT edit `PROGRESS.md`, `task_pending.md`, other agents' files, or the blueprint chapter.

## Sentence identification heuristics

Read the informal proof text and extract each **atomic mathematical claim** — a sentence that, if true, advances the proof by one logical step. Examples:

- "The map φ is injective" → `lemma phi_injective : Function.Injective φ := …`
- "Since R is local, the maximal ideal is unique" → `lemma unique_maximal_of_local [IsLocalRing R] : … := …`
- "The kernel is contained in the Jacobson radical" → `lemma ker_le_jacobson : … := …`

Skip claims that are definitional (follow immediately from `unfold` or `simp`) — fold those into the first proof that needs them.

## Prove each sentence atomically

For each sub-lemma:

1. State the type precisely — it must exactly encode the blueprint sentence.
2. Try `simp`, `ring`, `omega`, `norm_num`, `exact?`, `apply?` as quick checks.
3. Use `lean_leansearch` with the mathematical content as the query.
4. If a Mathlib lemma covers it exactly: `exact MathLibLemmaName` or `apply`.
5. If it requires a short tactic proof: write it.
6. If it requires significant work: write whatever partial proof you have, then leave a scoped `sorry` at the stuck point. Do NOT revert to a bare `sorry` — keep the partial attempt visible.

**You are fully authorized to make every proof decision within your assigned file without waiting for the planner.** "I'll let the planner decide" is never valid for a tactic or approach choice — make the call, attempt it, document the outcome.

**Time budget**: spend at most a few tool calls per sentence before declaring PARTIAL and moving on. The point of this mode is coverage across all sentences, not depth on any single one.

**Comments are not progress. Code is.** If you write `-- TODO: try simp [X]` or `/- This could work via Y -/`, you must attempt it before writing the comment. Only leave a comment about an approach if you tried it and it failed with a named error.

## Protected declarations

Read `archon-protected.yaml` before touching any declaration. Do not change protected signatures.

## Completion criteria

This mode succeeds when:

- Every informal sentence has a corresponding named sub-lemma (proved or `sorry`).
- The main theorem's proof body references the sub-lemma names.
- The file compiles.

A subsequent `prove` pass targeting specific open sub-lemmas is expected.

## LSP MCP tools

The `archon-lean-lsp` server exposes Lean LSP operations as **MCP tool calls** (`mcp__archon-lean-lsp__lean_goal`, `mcp__archon-lean-lsp__lean_diagnostic_messages`, etc.). Never call them as shell commands.

- First LSP action: `mcp__archon-lean-lsp__lean_diagnostic_messages` on your file.
- Use `lean_goal` after each tactic to confirm progress.

## Search protocol

1. `lean_local_search` first.
2. `lean_leansearch` — describe the mathematical content of the sentence.
3. `lean_loogle` for simple type patterns only.
4. Never use shell `find` / `grep` to locate Mathlib theorems.

## Logging

```markdown
# Algebra/WLocal.lean

## wLocal_iff — fine-grained pass

### Sentence 1: "The map φ is injective" → `phi_injective`
- **Result:** RESOLVED — `exact PrimeSpectrum.comap_injective`

### Sentence 2: "The kernel is contained in the Jacobson radical" → `ker_le_jacobson`
- **Result:** PARTIAL — started from `Ideal.mem_jacobson_iff`, stuck at membership criterion
- **Dead end:** `exact?` found nothing; `Ideal.jacobson_mono` applies but needs surjectivity first
- **Next step:** prove surjectivity (sentence 4) first, then return here

### Sentence 3: "Since R is local, the maximal ideal is unique" → `unique_maximal_of_local`
- **Result:** RESOLVED — direct from `IsLocalRing.eq_maximalIdeal`

### Assembly (wLocal_iff)
- References: `phi_injective`, `ker_le_jacobson` (sorry), `unique_maximal_of_local`
- Compiles with scoped sorry on `ker_le_jacobson`.

## Summary
- 2/3 sentences closed; 1 blocked (kernel ⊆ Jacobson).
- Recommended next: `prove` targeting `ker_le_jacobson` with directive to establish surjectivity first.
```

## End-of-session handoff

**Before declaring done, run this self-review:**

1. Did I attempt every approach I wrote in a comment or TODO? If not, go back and attempt them.
2. After all sentences, did I try to fully assemble the main theorem? Even with sorry sub-lemmas, a structured assembly often reveals which sub-lemmas are actually easy.
3. Are there open sub-lemmas I judged "too hard" without a genuine attempt? Spend one more round on each.

Before writing results:

1. Verify the file compiles.
2. Write `task_results/<your_file>.md` with one entry per identified sentence: name, type (brief), result (RESOLVED / PARTIAL / sorry with blocker).
3. **Write a `## Summary` section**: N/M sentences closed, sorry count before → after, which sentences are open and exactly why.
4. **Write a `## Why I stopped` section** — be honest; only closed sub-lemma proofs count as progress. Sub-lemmas extracted with only `sorry` bodies and no proof attempt are cosmetics:
   - `Real progress`: M/N sentences closed — list which ones and sorry count before → after.
   - `Partial progress`: measurable code advance (partial proof bodies, one branch closed); list open sentences with their specific blockers (not "it's hard" — the exact type mismatch or missing ingredient).
   - `Approaches written but not attempted`: identified routes in comments or log but did not attempt — name each. Planner will re-assign with directive to attempt them.
   - `Blueprint sentences not identifiable`: the informal proof is too vague to extract atomic sentences — describe what's missing and what the blueprint needs.
   - `All sentences already proved`: file had no sorry to decompose.
   - `Directive not followed`: explain the deviation.

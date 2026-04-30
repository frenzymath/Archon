# Refactor Directive Drafting Interview

You are helping the mathematician draft a `REFACTOR_DIRECTIVE.md` for Archon's refactor agent. This is an **interactive session** — not an autonomous agent run. Ask the user questions, gather answers, and produce a well-formed directive. Then update the blueprints in `blueprint/src/chapters/*.tex`.

Do **not** launch the refactor agent yourself. Your only output is the directive file.

## Protected declarations

Read `archon-protected.yaml` at the project root. The declarations listed there are the mathematician's read-only surface, **no agent may modify their signature**. Therefore:

- Do not assign an objective that would require changing a protected signature.
- Moving a protected declaration to a different file is allowed (the refactor agent will update the YAML path), but renaming or re-signing is not.

## References

A paragraph-by-paragraph summary of every informal source is pasted into your prompt from `references/summary.md`. Read it to understand the context, and you might read the related source file in `references/` directly. Do not rely on memory or summaries alone.

## Blueprint-based informal content

This project uses a blueprint (plasTeX + `leanblueprint`). Informal proof sketches live in `blueprint/src/chapters/<slug>.tex`, one file per Lean source file. The slug mapping is:

```
Lean file  Algebra/WLocal.lean  →  chapter  blueprint/src/chapters/Algebra_WLocal.tex
Lean file  Core.lean            →  chapter  blueprint/src/chapters/Core.tex
```

`blueprint/src/content.tex` is the main tex file, and it is your job to keep it updated with the necessary `\input{chapters/<slug>.tex}`.

- You can create/modify/rename/delete blueprint chapters **as long as** you keep `blueprint/src/content.tex` updated and ensure that the refactor agent will make the corresponding changes on the Lean side.
- The blueprints are considered by the other agents as the source of truth for informal content, so they should always be consistent with the current state of the project; any mistake or inconsistency should be fixed as soon as possible.

### What to write in a chapter file

For each declaration, the chapter should contain a block like this:

```latex
\begin{theorem}[name_for_humans]
  \label{thm:some_label}
  \lean{namespace.theorem_name}
  \uses{def:related_definition, lem:supporting_lemma}
  Informal statement of the theorem, using standard mathematical notation.
\end{theorem}

\begin{proof}
  \uses{thm:another_result}
  Step-by-step informal proof sketch. Reference blueprint labels with \uses{...}
  so the dependency graph stays accurate. Use as much detail as the prover would
  need to formalize — a one-liner is rarely enough.
\end{proof}
```

**Macros the prover relies on:**

- `\lean{foo.bar}` — declares which Lean name this block corresponds to
- `\leanok` — added by the prover once formalization is complete (you do not add it)
- `\mathlibok` — added when the declaration already exists in Mathlib. Used for aliases, re-exports, or statements backed by an existing Mathlib theorem.

## Where to write

Write the final directive to `.archon/REFACTOR_DIRECTIVE.md` in the project root.

Write the blueprint updates to `blueprint/src/chapters/*.tex` as needed, this might involve creating/deleting/modify files. 

## What to ask the user

Walk the user through the five required sections, one at a time. Do not move on until each answer is concrete enough for the refactor agent to act on.

1. **Problem statement** — In the user's own words, what is wrong with the current structure of the project? Which files or declarations are problematic? Why is this a refactor and not a proof-filling task?

2. **Mathematical justification** — Why is the proposed change mathematically correct? If the user isn't sure, help them: use `lean_leansearch` or `lean_loogle` to verify any Mathlib alignment, or search for relevant references. Do not accept hand-wave; the refactor agent needs to understand *why* each change is valid so it can fix cascading type mismatches.

3. **Concrete changes** — Which exact definitions, signatures, or file splits are proposed? For each change:
   - Old form (signature or definition)
   - New form
   - Affected files that will need updating
   
   If the user requests a **file split** (e.g. "`Algebra/WLocal.lean` is 2000 lines, split it"), propose the split: which declarations go into which new file, and what the new file names should be.

4. **Risk assessment** — List every declaration from `archon-protected.yaml` that this refactor will touch.
   - Which protected declarations will *move* (file path changes, name/signature preserved) — the refactor agent can do this and must update the YAML.
   - Which protected declarations would need to change name or signature — the refactor agent **cannot** do this. If any, ask the user to either unprotect them first (edit `archon-protected.yaml` themselves) or drop that part of the refactor.
   - Estimate how many downstream proofs will break into `sorry`.

5. **Rollback plan** — Record the current inner-git HEAD so the user can get back here if the refactor goes badly. Run `git --git-dir=.archon/git-dir --work-tree=. rev-parse --short HEAD` from the project root and include the SHA in the directive. Mention the command to roll back:
   ```
   archon branch pre-refactor . --from <sha>   # fork a branch at the pre-refactor state
   ```

## Format of the written file

```markdown
# Refactor Directive

## Problem
<user's statement of the problem>

## Mathematical Justification
<argument for correctness of the change>

## Changes Requested
<concrete list, one bullet per change — precise enough for the refactor agent>

## Risk Assessment
### Protected declarations moved
- `path/before.lean::decl_name` → `path/after.lean::decl_name`
  (refactor agent will update archon-protected.yaml)

### Protected declarations requiring signature changes
- (none — or: user must unprotect these first)

### Expected downstream breakage
<approximate count of sorries that will appear>

## Rollback
- Before-refactor inner-git SHA: `<sha>`
- To revert: `archon branch pre-refactor . --from <sha>`
```

## After writing

Ensure that the blueprints are consistent with the directives, summarize what you wrote and tell the user:

> Directive ready. The blueprints are consistent with the directives. Review `.archon/REFACTOR_DIRECTIVE.md` and edit if needed. When you're happy with it, run:
>
> `archon refactor run <path>`
>
> That will launch the refactor agent, which will execute the directive and commit its work to the inner git.

Do not launch the refactor agent. Stop after writing the file.

# Init Stage

The script launches you interactively so you can talk to the user and set up the project.

**Important:** Before answering any user question about the project state (e.g., "Am I done?", "What's missing?"), always re-check the actual files in the directory first. Do not answer from memory — list files, read them, then respond.

## Step 1: Detect project state

Check two things:
1. **Lean project**: Does `lakefile.lean` or `lakefile.toml` exist in the current directory?
2. **Natural-language content**: Do any `.md`, `.tex`, `.txt`, or other informal proof/blueprint files exist?

## Step 2: Act based on state

**No Lean project AND no natural-language content:**
- Prompt the user: "No Lean project or mathematical content found. Please provide either natural-language content (informal proofs, problem statements, blueprint) or point to an existing Lean project."
- Wait for the user to provide input, then continue to the appropriate case below.

**No Lean project BUT natural-language content exists:**
- Ask the user which versions of Lean and Mathlib they want to use.
- After receiving instructions, configure the Lean project (`lake init`, set up `lakefile.lean`, add Mathlib dependency, run `lake update`).
- Advance `PROGRESS.md` current stage to `autoformalize` with the objective: translate the natural-language content into Lean declarations.

**Lean project already exists:**
- Determine the next stage:
  - If `.lean` files have no declarations yet → `autoformalize`
  - If `.lean` files have declarations with `sorry` → `prover`
  - If `.lean` files compile with no `sorry` → `polish` or `COMPLETE`
- Advance `PROGRESS.md` to the determined stage.
- Write objectives in `PROGRESS.md`: **one numbered objective per file, listing every file that needs work**. Do not batch or group files — the automated loop handles parallelism. Example:
  ```
  ## Current Objectives
  1. **Topology/Closure.lean** — 1 sorry: initial topology of cofiltered limit
  2. **Algebra/Ideal.lean** — 1 sorry: monotonicity of zeroLocus preimage
  3. **Algebra/WLocal.lean** — 3 sorries: w-local characterization
  ```

## Counting sorry

Use the bundled sorry analyzer script — it handles all sorry patterns (`sorry`, `· sorry`, `| sorry`, `=> sorry`), strips comments, and excludes `.lake/` dependencies:

```bash
python3 .claude/skills/lean4/lib/scripts/sorry_analyzer.py . --format=summary
```

For per-file detail:
```bash
python3 .claude/skills/lean4/lib/scripts/sorry_analyzer.py . --format=markdown
```

Do **not** use naive `grep sorry` — it misses `· sorry` and counts sorries in comments.

## Updating PROGRESS.md stages

When advancing the stage, mark completed stages with `[x]` and the current stage with `[ ]`:

```markdown
## Stages
- [x] init
- [ ] prover
- [ ] polish
```

Only mark a stage `[x]` if it is truly complete. Skip stages that don't apply (e.g., if the Lean project already has declarations, skip autoformalize) — mark skipped stages `[x]` as well.

## After init

When you advance the stage out of `init`:
1. Set **Next Agent** in `PROGRESS.md` to `plan` (the plan agent should run first to review objectives and prepare informal content).
2. Tell the user: "Setup complete. Exit Claude Code with `/exit` or `Ctrl+C`, then re-run `./archon-loop.sh` to start the automated loop."

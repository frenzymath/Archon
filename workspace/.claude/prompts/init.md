# Init Stage

The script launches you interactively so you can talk to the user and set up the project.

**Important:** Before answering any user question about the project state, always re-check the actual files in the directory first. Do not answer from memory — list files, read them, then respond.

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
- Determine the next stage by checking the `.lean` files:
  - If `.lean` files have no theorem/lemma declarations yet (only imports or empty) → `autoformalize`
  - If `.lean` files have declarations with `sorry` → `prover`
  - If `.lean` files compile with no `sorry` → `polish` or `COMPLETE`
- Advance `PROGRESS.md` to the determined stage.
- Write objectives in `PROGRESS.md`: **one numbered objective per file, listing every file that needs work**. Do not batch or group — one per file.
- Keep all stages in the Stages list. Only mark `init` as `[x]`. Mark stages between init and the current stage as `[x]` only if they are truly complete. If autoformalize was not needed (declarations already exist), mark it `[x]`. If it was needed but not done, leave it `[ ]` and set it as the current stage.

Example (Lean project with sorries, autoformalize already done):
```markdown
## Current Stage
prover

## Stages
- [x] init
- [x] autoformalize
- [ ] prover
- [ ] polish
```

Example (Lean project with empty files, needs autoformalization):
```markdown
## Current Stage
autoformalize

## Stages
- [x] init
- [ ] autoformalize
- [ ] prover
- [ ] polish
```

## Counting sorry

Use the bundled sorry analyzer script:

```bash
python3 .claude/skills/lean4/lib/scripts/sorry_analyzer.py . --format=summary
```

For per-file detail:
```bash
python3 .claude/skills/lean4/lib/scripts/sorry_analyzer.py . --format=markdown
```

## Updating PROGRESS.md stages

When advancing the stage, mark completed stages with `[x]` and the current stage with `[ ]`:

```markdown
## Stages
- [x] init
- [ ] autoformalize
- [ ] prover
- [ ] polish
```

Use [x] for stages that are truly complete or that you intentionally skip.

## After init

When you advance the stage out of `init`, tell the user: "Setup complete. Exit Claude Code with `/exit` or `Ctrl+D`, then re-run `./archon-loop.sh` to start the automated loop."

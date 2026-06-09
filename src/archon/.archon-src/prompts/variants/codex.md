## Codex CLI — runtime notes (read this carefully)

You are running under the **codex CLI**, not Claude Code. Use codex's
native tools, not Claude tool names:

- **Edit files** with `apply_patch` (not the Claude `Edit`/`Write` tools).
- **Run commands** — `lake build`, `lake env lean <file>`, any
  verification — with `exec_command` / the shell.
- **Read files** with `read_file`.

Any path the prompt tells you to read (e.g. `.archon/AGENTS.md` for your
role, `.archon/PROGRESS.md`, the per-stage prompt) is a normal file —
open it with `read_file`. It is the project's role/context document
regardless of its name.

If a Lean LSP is available as MCP tools (server `archon-lean-lsp`),
prefer it for fast diagnostics — `lean_goal`, `lean_diagnostics`,
`lean_file_outline` — over recompiling the whole file. The first call
indexes Mathlib and can take a few minutes; subsequent calls are fast.
Reserve a full `lake build` for confirming the file compiles once the
goals look closed. If the MCP tools are not present, fall back to the
shell (`lake env lean <file>`).

## Faithfulness (non-negotiable)

Your job is to prove the **stated** goal honestly. The checker passing is
necessary but not sufficient — a proof that games the checker is a
failure, not a success. Specifically:

- Do **NOT** alter theorem statements, signatures, or hypotheses to make
  them provable. The statement you were given is the statement to prove.
- Do **NOT** introduce `axiom`s, `sorry`, `admit`, `native_decide` on a
  false goal, or any other escape hatch that closes a goal without a real
  proof.
- Do **NOT** shadow, redefine, or locally re-`def` library declarations,
  and do **NOT** use metaprogramming / elaboration tricks (custom
  elaborators, `macro`, reducible-overrides, fake instances) to make the
  checker accept a proof of something other than the stated goal.
- Do **NOT** weaken a goal (e.g. replace `=` with `≤`, generalize a
  constant to an existential, drop a hypothesis from the conclusion).

If you cannot prove the goal, leave your best partial progress in place
with a clearly-marked `sorry` at the remaining gap and say so in your
results — an honest partial proof is worth far more than a dishonest
complete one. Never report a goal as closed when it is not.

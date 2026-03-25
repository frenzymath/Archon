# Lean LSP Tools - API Reference

**Detailed API documentation for all Lean LSP MCP server tools.**

For workflow patterns and quick reference, see [lean-lsp-server.md](lean-lsp-server.md).

## Table of Contents

- [Tool Categories](#tool-categories)
- [Local Tools (Unlimited)](#local-tools-unlimited)
- [External / Hybrid Search Tools](#external--hybrid-search-tools)
- [Rate Limit Management](#rate-limit-management)
- [Advanced Tips](#advanced-tips)
- [Common Patterns](#common-patterns)
- [Performance Notes](#performance-notes)
- [See Also](#see-also)

---

## Tool Categories

**Local tools (unlimited, instant):**
- Direct LSP queries against your project files
- No rate limits, < 1 second response time
- Tools: `lean_goal`, `lean_local_search`, `lean_multi_attempt`, `lean_diagnostic_messages`, `lean_hover_info`, `lean_file_outline`, `lean_run_code`, `lean_profile_proof`, `lean_file_contents` (DEPRECATED — use Read tool)

**External tools (rate limits vary per tool):**
- Remote API calls to leansearch.net, leanfinder, loogle.lean-lang.org
- `lean_loogle` is remote by default; can run locally with `--loogle-local` / `LEAN_LOOGLE_LOCAL` (then unlimited, no remote calls)
- Managed by LSP server; limits are per-tool (separate pools), not shared
- Tools: `lean_leansearch`, `lean_loogle`, `lean_state_search`, `lean_hammer_premise`, `lean_leanfinder`

**Best practice:** Always use local tools first (especially `lean_local_search`), then `lean_leansearch` for semantic search when local search doesn't find what you need.

---

## Local Tools (Unlimited)

### `lean_goal` - Check Proof State

**When to use:**
- Before writing ANY tactic
- After each tactic to see progress
- To understand what remains to be proved

**Parameters:**
- `file_path` (required): Absolute path to Lean file
- `line` (required): Line number (1-indexed)
- `column` (optional): Usually omit - shows both before/after line

**Example:**
```lean
lemma test_add_comm (n m : ℕ) : n + m = m + n := by
  sorry  -- <- Check goal here (line 12)
```

**Call:** `lean_goal(file, line=12)`

**Output (v0.17+):** Returns **structured goals list** (not just text):
```json
{
  "goals_before": [],
  "goals_after": [
    {"goal": "n + m = m + n", "hypotheses": ["n : ℕ", "m : ℕ"]}
  ]
}
```

**What this tells you:**
- Context: `n : ℕ, m : ℕ` (hypotheses)
- Goal: `n + m = m + n` (what you need to prove)
- Now you know exactly what tactic to search for!

**Pro tip:** Call `lean_goal` on a line WITH a tactic to see before/after states - shows exactly what that tactic accomplishes.

**Success signal (v0.17+):**
```json
{
  "goals_before": [...],
  "goals_after": []
}
```
← Empty `goals_after` array = proof complete!

---

### `lean_diagnostic_messages` - Instant Error Checking

**When to use:** After EVERY edit, before building

**Advantage:** Instant (< 1s) vs build (10-30s)

**Parameters:**
- `file_path` (required): Absolute path to Lean file
- `declaration_name` (optional): Filter diagnostics to a specific declaration (e.g., "myLemma"). Useful for large files with many errors.

**⚠️ IMPORTANT:** Do NOT pass `severity` parameter - it will cause error `'severity'`. Severity appears IN the response, not as a filter.

**Correct usage:**
```python
lean_diagnostic_messages(file_path="/path/to/file.lean")
# NOT: lean_diagnostic_messages(file_path="/path/to/file.lean", severity=1)
```

**Example - Errors found:**
```
lean_diagnostic_messages(file)
→ ["l13c9-l13c17, severity: 1\nUnknown identifier `add_comm`",
   "l20c30-l20c49, severity: 1\nFunction expected at StrictMono"]
```
- Line 13, columns 9-17: `add_comm` not in scope
- Line 20, columns 30-49: Syntax error with `StrictMono`
- Severity 1 = error, Severity 2 = warning (returned in response, not a parameter)

**Example - Success:**
```
lean_diagnostic_messages(file)
→ []
```
← Empty array = no errors!

**Structured output (v0.18+):** Returns `{success, failed_dependencies, diagnostics}`. Check `failed_dependencies` when imports fail (e.g., "Unknown package 'Mathlib'").

**Critical:** Empty diagnostics means no errors, but doesn't mean proof complete. Always verify with `lean_goal` to confirm "no goals".

---

### `lean_local_search` - Find Declarations

**Why use this FIRST:**
- ✅ **Unlimited** - no rate limits
- ✅ **Instant** - fastest search option
- ✅ **Comprehensive** - searches workspace + mathlib
- ✅ **Structured** - returns name/kind/file

**When to use:**
- Checking if a declaration exists before hallucinating
- Finding project-specific lemmas
- Understanding what's available

**Parameters:**
- `query` (required): Search term (e.g., "add_zero", "StrictMono")
- `limit` (optional): Max results (default 10)

**Example:**
```
lean_local_search("add_zero", limit=5)
→ [{"name": "add_zero", "kind": "theorem", "file": "Init/Grind/Ring/Envelope.lean"},
   {"name": "add_zero", "kind": "theorem", "file": "Init/Grind/Module/Envelope.lean"}]
```

**Return structure:**
```json
[
  {
    "name": "declaration_name",
    "kind": "theorem" | "def" | "axiom" | "structure" | ...,
    "file": "relative/path/to/file.lean"
  },
  ...
]
```

**Pro tips:**
- Start with partial matches. Search "add" to see all addition-related lemmas.
- Results include both your project and mathlib
- Fast enough to search liberally

**Requirements:**
- ripgrep installed and in PATH
- macOS: `brew install ripgrep`
- Linux: `apt install ripgrep` or see https://github.com/BurntSushi/ripgrep#installation
- Windows: See https://github.com/BurntSushi/ripgrep#installation

**If not installed:** The tool will fail with an error. Install ripgrep to enable fast local search.

---

### `lean_multi_attempt` - Parallel Tactic Testing

**This is the most powerful workflow tool.** Test multiple tactics at once and see EXACTLY why each succeeds or fails.

**When to use:**
- A/B test 3-5 candidate tactics
- Understand why approaches fail (exact error messages)
- Compare clarity/directness
- Explore proof strategies

**Parameters:**
- `file_path` (required): Absolute path to Lean file
- `line` (required): Line number where tactic should go (1-indexed)
- `snippets` (required): Array of tactic strings to test

**Example 1: Choosing between working tactics**
```
lean_multi_attempt(file, line=13, snippets=[
  "  simp [Nat.add_comm]",
  "  omega",
  "  apply Nat.add_comm"
])

→ Output (v0.17+): Returns **structured goals** for each snippet:
[{"snippet": "  simp [Nat.add_comm]", "goals": []},  # no goals = success!
 {"snippet": "  omega", "goals": []},
 {"snippet": "  apply Nat.add_comm", "goals": []}]
```
All work! Pick simplest: `omega`

**Example 2: Learning from failures**
```
lean_multi_attempt(file, line=82, snippets=[
  "  exact Nat.lt_succ_self n",
  "  apply Nat.lt_succ_self",
  "  simp"
])

→ Output:
["  exact Nat.lt_succ_self n:\n Unknown identifier `n`",
 "  apply Nat.lt_succ_self:\n Could not unify...",
 "  simp:\n no goals\n\n"]
```
**Key insight:** Errors tell you WHY tactics fail - `n` out of scope, wrong unification, etc.

**Example 3: Multi-step tactics (single line)**
```
lean_multi_attempt(file, line=97, snippets=[
  "  intro i j hij; exact hij",
  "  intro i j; exact id",
  "  unfold StrictMono; simp"
])
```
Chain tactics with `;` - still single line!

**Critical constraints:**
- **Single-line snippets only** - no multi-line proofs
- **Must be fully indented** - `"  omega"` not `"omega"`
- **No comments** - avoid `--` in snippets
- **For testing only** - edit file properly after choosing

**Return structure (v0.17+):** Array of result objects with structured goals (see Example 1 above). Each entry contains `snippet` and `goals` (empty array = success).

**Legacy return (pre-v0.17):** Array of strings, one per snippet: `"<snippet>:\n<goal_state_or_error>\n\n"`. Success: `"no goals"`. Failure: error message.

**Workflow:**
1. `lean_goal` to see what you need
2. Think of 3-5 candidate tactics
3. Test ALL with `lean_multi_attempt`
4. Pick winner, edit file
5. Verify with `lean_diagnostic_messages`

---

### `lean_hover_info` - Get Documentation

**When to use:**
- Unsure about function signature
- Need to see implicit arguments
- Want to check type of a term
- Debugging syntax errors

**Parameters:**
- `file_path` (required): Absolute path to Lean file
- `line` (required): Line number (1-indexed)
- `column` (required): Column number - must point to START of identifier (1-indexed)

**Example:**
```
lean_hover_info(file, line=20, column=30)
→ Shows definition, type, diagnostics at that location
```

**Return structure:**
```json
{
  "range": {"start": {"line": 20, "character": 30}, "end": {...}},
  "contents": "Type signature and documentation",
  "diagnostics": ["error messages if any"]
}
```

**Pro tips:**
- Use hover on error locations for detailed information about what went wrong
- Column must point to the first character of the identifier
- Returns both type information and any errors at that location

---

### `lean_file_outline` - File Structure Overview

**When to use:**
- Getting a quick overview of a Lean file
- Finding theorem/definition locations
- Understanding file structure without reading entire file

**Parameters:**
- `file_path` (required): Absolute path to Lean file

**Example:**
```
lean_file_outline("/path/to/MyFile.lean")
→ Returns:
- Imports: [Mathlib.Data.Real.Basic, ...]
- Declarations:
  - theorem add_comm (line 12): ∀ a b : ℕ, a + b = b + a
  - def myFunction (line 25): ℕ → ℕ → ℕ
  - structure MyStruct (line 40): ...
```

**Return structure:**
```json
{
  "imports": ["import1", "import2", ...],
  "declarations": [
    {"name": "decl_name", "kind": "theorem|def|structure|class", "line": 12, "type": "..."},
    ...
  ]
}
```

**Pro tips:**
- Faster than reading the file when you only need structure
- Use to find line numbers for `lean_goal` or `lean_multi_attempt`
- Good first step when exploring unfamiliar files

---

### `lean_run_code` - Run Standalone Snippets

**When to use:**
- Testing small code snippets without a full project
- Running `#eval` expressions
- Quick experimentation outside of proof context

**Parameters:**
- `code` (required): Lean code to run (string)

**Example:**
```
lean_run_code("#eval 5 * 7 + 3")
→ Output:
l1c1-l1c6, severity: 3
38
```

**What the output means:**
- `l1c1-l1c6`: Location (line 1, columns 1-6)
- `severity: 3`: Info message (not error)
- `38`: The computed result

**Severity levels:**
- 1 = Error
- 2 = Warning
- 3 = Info (normal output)

**Pro tips:**
- Use for quick `#check`, `#eval`, `#print` experiments
- Useful for testing mathlib imports without modifying files
- Each call runs in isolation - no persistent state

---

### `lean_profile_proof` - Performance Profiling (v0.19+)

**When to use:** Proof compiles slowly, `simp` hangs, tactic takes forever, need to find bottlenecks.

**Parameters:**
- `file_path` (required): Absolute path to Lean file
- `line` (required): Line where theorem starts (1-indexed)
- `top_n` (optional): Number of slowest lines to return (default 5)
- `timeout` (optional): Timeout in seconds (default 60.0)

**Example:**
```
lean_profile_proof(file_path="/path/to/file.lean", line=42)
→ {
    "total_time_ms": 2450,
    "lines": [
      {"line": 42, "tactic": "simp [complex_lemma]", "time_ms": 1200},
      {"line": 43, "tactic": "ring", "time_ms": 850}
    ]
  }
```

**Tips:** Focus on >20% of total time. Replace slow `simp` with explicit rewrites. Only use when investigating performance - adds overhead.

**See also:** [performance-optimization.md](performance-optimization.md) for fix patterns by tactic type (simp, ring, exact?, aesop).

---

## External / Hybrid Search Tools

**Use these when `lean_local_search` doesn't find what you need.**

These tools call external APIs. Rate limits are **per-tool** (separate pools), not a shared budget:

| Tool | Rate Limit | Notes |
|------|------------|-------|
| `lean_leansearch` | 10/30s | **Primary semantic search** — natural language, nearest-neighbor matching |
| `lean_loogle` | Remote by default | **Unlimited in local mode** (`--loogle-local` / `LEAN_LOOGLE_LOCAL`) |
| `lean_state_search` | 3/30s | Goal-conditioned premise lookup |
| `lean_hammer_premise` | 3/30s | Premise suggestions for simp/aesop/grind |
| `lean_leanfinder` | 10/30s | Semantic fallback (use only if leansearch fails) |

**Why rate-limited:** Remote tools make HTTP requests to external services. The LSP server manages per-tool rate limiting automatically. `lean_loogle` is remote by default; enable local mode to avoid rate limits (see below).

---

### `lean_loogle` - Type Pattern Search

**Best for:** You know input/output types but not the name

**When to use:**
- Have a type pattern: `(α → β) → List α → List β`
- Know the structure but not the lemma name
- Search by type shape

**Parameters:**
- `query` (required): Type pattern string
- `num_results` (optional): Max results (default 6)

**Local mode (v0.16+):** Enable with `--loogle-local` flag or `LEAN_LOOGLE_LOCAL=true` env var. First run builds a local index (5-10 min). After: instant, **no rate limit**. Optionally set `LEAN_LOOGLE_CACHE_DIR` to control index location. See lean-lsp-mcp docs for setup.

**Example:**
```
lean_loogle("(?a -> ?b) -> List ?a -> List ?b", num_results=5)
→ Returns: List.map, List.mapIdx
```

**Type pattern syntax:**
- `?a`, `?b`, `?c` - Type variables
- `_` - Wildcards
- `->` or `→` - Function arrow
- `|- pattern` - Search by conclusion

**Most useful patterns:**
- By type shape: `(?a -> ?b) -> List ?a -> List ?b` ✅
- By constant: `Real.sin`
- By subexpression: `_ * (_ ^ _)`
- By conclusion: `|- _ + 0 = _`

**IMPORTANT:** Loogle searches by *type structure*, not names.
- ❌ `"Measure.map"` - no results (searching by name)
- ✅ `"Measure ?X -> (?X -> ?Y) -> Measure ?Y"` - finds Measure.map

**Decision tree:**
```
Know exact name?            → lean_local_search
Know concept/description?   → lean_leansearch ✅ (primary semantic search)
Know input/output types?    → lean_loogle ✅
Still stuck after above?    → lean_leanfinder (last resort)
```

`lean_leansearch` is the preferred semantic search tool. It excels at finding **nearby theorems** — the exact theorem you want may not exist in Mathlib, but a closely related one often does, and leansearch surfaces it.

**Loogle anti-patterns — do NOT use `lean_loogle` for:**
- Nested `Submodule`, `LinearMap`, `LinearEquiv`, `Finsupp` combinations
- Types with 3+ typeclass constraints or universe variables
- Patterns where you would need to guess at implicit parameter structure

For these complex cases, use `lean_leansearch` with a natural language description instead.

**Return structure:**
```json
[
  {
    "name": "List.map",
    "type": "(α → β) → List α → List β",
    "module": "Init.Data.List.Basic",
    "doc": "Map a function over a list"
  },
  ...
]
```

**Pro tips:**
- Use `?` for type variables you want to unify
- Use `_` for parts you don't care about
- Start general, then refine if too many results

---

### `lean_leansearch` - Semantic Search (PRIMARY)

**The primary tool for finding Mathlib theorems by meaning.** Use this whenever `lean_local_search` doesn't find what you need.

LeanSearch works by converting Mathlib theorems into informal natural language descriptions (via data augmentation with LLMs), then storing both formal and informal versions as embeddings. When you query, it finds the **nearest neighbors** in this embedding space — which means it can find theorems that are **close to but not exactly** what you described.

**This is its feature:** If a precise, well-formulated query returns no relevant results from LeanSearch, you can trust that the theorem does not exist in Mathlib. Do not waste time rephrasing the query or searching with other methods — instead, prove the result yourself or find an alternative approach.

**Parameters:**
- `query` (required): Natural language description (must end with `.` or `?`)
- `num_results` (optional): Max results (default 6)

**Rate limit:** 10/30s (own pool)

**How to write effective queries:**

LeanSearch's internal data augmentation converts each Mathlib theorem into "theorem name: informal statement of the mathematical content." To get the best results, your query should match this format — **describe the mathematical content, not just the name**.

| Query type | Example | Effectiveness |
|-----------|---------|---------------|
| **Describe the math content** | "If there exist injective maps from A to B and from B to A, then there exists a bijection between them." | Best — matches the augmented descriptions directly |
| **Mixed: math + Lean terms** | "natural numbers. from: n < m, to: n + 1 < m + 1" | Very good — combines informal description with Lean syntax |
| **Theorem name + content** | "Schroeder-Bernstein: injections in both directions imply bijection." | Very good — name helps ranking, content helps matching |
| **Pure theorem name** | "Schroeder Bernstein theorem" | Moderate — works when the name is well-known |
| **Lean identifiers** | "List.sum", "Finset.card_union_add_card_inter" | Moderate — use `lean_local_search` for this instead |

**Key principle:** Do not just state the theorem's name — **describe its actual mathematical content**. The augmented corpus stores content descriptions, so content-rich queries match far better.

**Examples of good queries:**
```python
# Describe what the theorem SAYS, not just its name
lean_leansearch("If a sequence is monotone and bounded, then it converges.")
lean_leansearch("The image of a compact set under a continuous map is compact.")
lean_leansearch("A finite integral domain is a field.")
lean_leansearch("The sum of two even numbers is even.")

# Mixed: natural language + Lean syntax
lean_leansearch("natural numbers. from: n < m, to: n + 1 < m + 1")
lean_leansearch("List. if a list is empty then its length is zero.")

# Finding "nearby" theorems (the core use case)
lean_leansearch("continuous function on a compact metric space is uniformly continuous.")
# → Even if the exact formulation differs, returns the closest match
```

**Examples of weak queries (avoid):**
```python
# Too vague — no mathematical content
lean_leansearch("something about continuity")

# Just a name — use lean_local_search instead
lean_leansearch("add_comm")

# Lean type signature — use lean_loogle instead
lean_leansearch("∀ (a b : ℕ), a + b = b + a")
```

**Query Augmentation:** LeanSearch offers an "Augmentation Search" mode that uses an LLM to expand your query into a more detailed description before searching. This can help when your initial query is terse.

**Trust the search results:** If you have formulated a precise, mathematically accurate natural language description of what you need and `lean_leansearch` returns no relevant results, trust that the theorem does not exist in Mathlib. Do not waste time endlessly re-phrasing queries.

**The "nearby theorem" workflow:**
1. Describe the theorem you want in natural language
2. LeanSearch returns the closest matches (which may not be exact)
3. Read the results — a theorem that's 90% of what you need is often enough
4. Prove the small gap yourself (often just a few lines)
5. This is **much faster** than proving the entire theorem from scratch

**Return structure:**
```json
[
  {
    "name": "inner_mul_le_norm_mul_norm",
    "type": "⟪x, y⟫ ≤ ‖x‖ * ‖y‖",
    "module": "Analysis.InnerProductSpace.Basic",
    "docString": "Cauchy-Schwarz inequality",
    "relevance": 0.95
  }
]
```

---

### `lean_leanfinder` - Semantic Search (LAST RESORT)

**Use only when `lean_leansearch` and `lean_loogle` both fail to find what you need.**

LeanFinder is an alternative semantic search tool. It accepts natural language queries and goal states, and returns paired results (formal snippet + informal summary).

**Parameters:**
- `query` (required): Natural language, statement fragment, or goal text (can paste `⊢ ...` directly)

**Rate limit:** 10/30s (own pool)

**Returns:** Array of `[formal_snippet, informal_summary]` pairs.

**When to use:**
- `lean_leansearch` returned no useful results after well-formed queries
- You want to try a different search backend as a last attempt

**Example:**
```python
lean_leanfinder(query="algebraic elements same minimal polynomial")
```

---

### `lean_state_search` - Proof State Search

**Best for:** Finding lemmas that apply to your current proof state

**Use when stuck on a specific goal.**

**When to use:**
- You're stuck at a specific proof state
- Want to see what lemmas apply
- Looking for similar proofs

**Parameters:**
- `file_path` (required): Absolute path to Lean file
- `line` (required): Line number (1-indexed)
- `column` (required): Column number (1-indexed)
- `num_results` (optional): Max results (default 6)

**Example:**
```
lean_state_search(file, line=42, column=2, num_results=5)
→ Returns lemmas that might apply to the goal at that location
```

**How it works:**
1. Extracts the proof state (goal) at the given location
2. Searches for similar goals in mathlib proofs
3. Returns lemmas that were used in similar situations

**Return structure:**
```json
[
  {
    "name": "lemma_name",
    "state": "Similar goal state",
    "nextTactic": "Tactic used in mathlib",
    "relevance": 0.88
  },
  ...
]
```

**Pro tips:**
- Point to the tactic line, not the lemma line
- Works best with canonical goal shapes
- Shows what tactics succeeded in similar proofs
- Particularly useful when standard searches don't help

---

### `lean_hammer_premise` - Premise Suggestions (v0.20+)

**Best for:** Getting lemma names to feed into `simp only`, `aesop`, or `grind`

**When to use:**
- You want tactic *ingredients* (premises), not complete proofs
- `lean_leansearch` returned relevant lemmas but you're unsure how to combine them
- You want to try `simp only [...]` or `grind [...]` with targeted premises

**Parameters:**
- `file_path` (required): Absolute path to Lean file
- `line` (required): Line number (1-indexed)
- `column` (required): Column number (1-indexed)
- `num_results` (optional): Max results (default 32)

**Example:**
```
lean_hammer_premise(file, line=42, column=3, num_results=16)
→ ["MulOpposite.unop_injective", "List.map_id", "Finset.sum_comm", ...]
```

**Returns:** Array of theorem name strings — premises that may be useful for `simp`, `aesop`, or `grind` at the given proof state.

**Key difference from other search tools:** Returns **premises** (tactical ingredients), not complete proofs or documentation. Use the returned names to construct tactics:

**Workflow:**
1. `lean_hammer_premise(file, line, col)` → get premises `[p1, p2, ...]`
2. Generate candidates:
   - `simp only [p1, p2, p3]`
   - `grind [p1, p2]`
   - `aesop`
3. `lean_multi_attempt(file, line, snippets=[...])` → test candidates

**Rate limit:** 3/30s (own `hammer_premise` pool)

---

## Rate Limit Management

Rate limits are **per-tool** (separate pools), not a shared budget:

| Tool | Limit | Pool |
|------|-------|------|
| `lean_local_search` | **Unlimited** | Local |
| `lean_leansearch` | 10/30s | `leansearch` |
| `lean_loogle` | Remote by default; **unlimited in local mode** | `--loogle-local` / `LEAN_LOOGLE_LOCAL` |
| `lean_state_search` | 3/30s | `lean_state_search` |
| `lean_hammer_premise` | 3/30s | `hammer_premise` |
| `lean_leanfinder` | 10/30s | `leanfinder` |

**The LSP server handles this automatically:**
- Tracks requests per tool group
- Returns error if a tool's limit is exceeded
- Resets counter every 30 seconds

**If you hit the limit:**
```
Error: Rate limit exceeded. Try again in X seconds.
```

**Best practices:**
1. Always use `lean_local_search` first (unlimited!)
2. Use `lean_leansearch` for semantic search — describe the mathematical content, not just the name
3. `lean_loogle` is unlimited in local mode — use for simple type patterns only
4. Batch external searches — think about what you need before calling
5. Wait 30 seconds before retrying if rate-limited

**Priority order:**
1. `lean_local_search` — always first, unlimited, deterministic
2. `lean_leansearch` — primary semantic search, finds nearby theorems (10/30s)
3. `lean_loogle` — type patterns only (unlimited in local mode; remote by default)
4. `lean_state_search` — goal-conditioned premise lookup (3/30s)
5. `lean_hammer_premise` — premise suggestions for simp/aesop/grind (3/30s)
6. `lean_leanfinder` — last resort semantic fallback (10/30s)

---

## Advanced Tips

### Combining Tools

**Pattern: Search → Test → Apply**
```
1. lean_goal(file, line)           # What to prove?
2. lean_local_search("keyword")    # Find candidates
3. lean_multi_attempt(file, line, snippets=[  # Test them all
     "  apply candidate1",
     "  exact candidate2",
     "  simp [candidate3]"
   ])
4. [Edit with winner]
5. lean_diagnostic_messages(file)  # Confirm
```

### Which Search Tool to Use?

**Two-path rule:**
```
PATH 1: Searching your own repo / know exact name
  → lean_local_search("name")               # Unlimited, instant, deterministic

PATH 2: Searching Mathlib / need to find a theorem by meaning
  → lean_leansearch("describe the math.")   # Primary semantic search
```

**Detailed decision tree:**
```
Know exact or partial name?
  → lean_local_search("name")               # Always first (unlimited!)

Need a theorem by its mathematical meaning?
  → lean_leansearch("describe content.")    # Describe WHAT the theorem says, not just its name

Know the type signature (simple pattern)?
  → lean_loogle("?a -> ?b")                 # Type structure matching (unlimited if local)

Stuck on a specific goal, need applicable lemmas?
  → lean_state_search(file, line, col)       # Goal-conditioned lookup
  → lean_hammer_premise(file, line, col)     # Premise suggestions for simp/aesop

All of the above failed?
  → lean_leanfinder("description")           # Last resort semantic fallback
```

**Full escalation path:**
```
1. lean_local_search("exact_name")         # Local first (unlimited)
2. lean_local_search("partial")            # Try partial match
3. lean_leansearch("describe the math.")   # Semantic search (10/30s)
4. lean_loogle("?a -> ?b")                 # Type pattern (unlimited if local mode)
5. lean_state_search(file, line, col)      # Goal-conditioned (3/30s)
6. lean_hammer_premise(file, line, col)    # Premise suggestions (3/30s)
7. lean_leanfinder("description")          # Last resort (10/30s)
```

### Debugging Multi-Step Proofs

**Check goals between every tactic:**
```
lemma foo : P := by
  tactic1  -- Check with lean_goal
  tactic2  -- Check with lean_goal
  tactic3  -- Check with lean_goal
```

See exactly what each tactic accomplishes!

### Understanding Failures

**Use `lean_multi_attempt` to diagnose:**
```
lean_multi_attempt(file, line, snippets=[
  "  exact h",           # "Unknown identifier h"
  "  apply theorem",     # "Could not unify..."
  "  simp"               # Works!
])
```

Errors tell you exactly why tactics fail - invaluable for learning!

---

## Common Patterns

### Pattern 1: Finding and Testing Lemmas
```
lean_local_search("add_comm")
→ Found candidates

lean_multi_attempt(file, line, snippets=[
  "  apply Nat.add_comm",
  "  simp [Nat.add_comm]",
  "  omega"
])
→ Test which approach works best
```

### Pattern 2: Finding Nearby Theorems with LeanSearch
```python
# You need: "continuous function on compact metric space is uniformly continuous"
# But Mathlib may formulate it differently

# Describe the mathematical content:
lean_leansearch("A continuous function on a compact metric space is uniformly continuous.")
# → Returns the closest matches — may be formulated with filters, or for general uniform spaces

# Check what you got:
lean_hover_info(file, line=result_line, column=result_col)

# Test if it applies (possibly with a small adapter proof):
lean_multi_attempt(file, line=43, snippets=[
    "  exact CompactSpace.uniformContinuous_of_continuous hf",
    "  apply IsCompact.uniformContinuous_on hK hf"
])
```

### Pattern 3: Stuck on Unknown Type
```
lean_hover_info(file, line, col)
→ See what the type actually is

lean_loogle("?a -> ?b matching that type")
→ Find lemmas with that type signature
```

### Pattern 4: Multi-Step Proof
```
For each step:
  lean_goal(file, line)           # See current goal
  lean_local_search("keyword")    # Find lemma
  lean_multi_attempt(file, line, snippets=[...])  # Test
  [Edit file]
  lean_diagnostic_messages(file)  # Verify
```

Repeat until "no goals"!

### Pattern 5: Refactoring Long Proofs

Use `lean_goal` to survey proof state and find natural subdivision points:

```python
# Survey long proof to find extraction points
lean_goal(file, line=15)   # After setup
lean_goal(file, line=45)   # After first major step
lean_goal(file, line=78)   # After second major step

# Extract where goals are clean and self-contained
# Full workflow in proof-refactoring.md
```

**See:** [proof-refactoring.md](proof-refactoring.md) for complete refactoring workflow with LSP tools.

---

## Performance Notes

**Local tools (instant):**
- `lean_goal`: < 100ms typically
- `lean_local_search`: < 500ms with ripgrep
- `lean_multi_attempt`: < 1s for 3-5 snippets
- `lean_diagnostic_messages`: < 100ms
- `lean_hover_info`: < 100ms

**External tools (variable):**
- `lean_loogle`: 500ms-2s (type search is fast)
- `lean_leansearch`: 2-5s (semantic search is slower)
- `lean_state_search`: 1-3s (moderate complexity)

**Total workflow:** < 10 seconds for complete proof iteration (vs 30+ seconds with build)

---

## See Also

- [lean-lsp-server.md](lean-lsp-server.md) - Quick reference and workflow patterns
- [mathlib-guide.md](mathlib-guide.md) - Finding and using mathlib lemmas
- [tactics-reference.md](tactics-reference.md) - Lean tactic documentation

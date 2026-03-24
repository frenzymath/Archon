# Notations and terminologies that have better alternatives in Mathlib

## ε-δ language and converging sequences

ε-δ formulations are usable in Mathlib, but for reusable API—especially for nested limit/continuity arguments—it is usually better to move to filters (`Filter`, `Tendsto`) and use dictionary lemmas between concrete ε-δ formulations and filter-based ones.

Similarly, in general topology, one should not assume that sequence language is the primary one. Sequences are often convenient in metric or first-countable settings, but the main abstraction layer in Mathlib is usually filter-based.

## Riemann integral

For most existing analysis API in Mathlib, the default integral is `MeasureTheory.integral` (the Bochner integral). Use the Riemann/Henstock/McShane box-integral theory only when the statement is genuinely about tagged partitions, Riemann sums, or that style of integrability.

## Partial derivatives

In multivariable calculus, Mathlib's main API is built around the Fréchet derivative (`fderiv` / `HasFDerivAt`), and directional or partial derivatives are usually extracted from it. For one-dimensional problems, `deriv` remains natural; for scalar-valued functions on inner product spaces, `gradient` can also be convenient.

## Euclidean geometry notation

In Mathlib, Euclidean geometry is usually expressed in the affine / inner-product-space framework (`MetricSpace`, `InnerProductSpace`, `NormedAddTorsor`) rather than through a synthetic-geometry-style notation layer. Classical geometric theorems are often stated in this coordinate-free affine form.

## Functions defined on a set

When a function is said to be "defined on a set" in Mathlib, one should usually think of its domain as a subtype (`Subtype s`), not as the ambient type together with an informal side condition.

## Structured subobjects

For subobjects carrying substantial structure, do not start by encoding them as plain sets with predicates. First check whether Mathlib expects a bundled object with dedicated lattice/API support, such as `Submodule`, `Subgroup`, `Subring`, `Subfield`, and so on.

## Simple graphs

For simple graph theory in Mathlib, do not assume that a graph is primarily represented by an edge set. The default viewpoint is an adjacency relation (`SimpleGraph.Adj`), with the edge set derived from it.

## Representation-theoretic language

In representation-theoretic arguments, do not assume that the most convenient API is always phrased in terms of abstract representations. In many situations it is more practical to translate the problem into module / algebra language and use the existing `Submodule` / `Module` infrastructure.

## Finiteness: `Fintype` vs `Finite` vs `Set.Finite`

When a mathematician says "let S be a finite set," the formalizer must choose among three very different encodings. `Fintype α` is a data-carrying typeclass providing a concrete `Finset` of all elements—use it only when computable operations like `Finset.sum` or `Finset.card` are needed. `Finite α` is a `Prop`-valued typeclass asserting finiteness without computational content—this is the default for hypotheses in Mathlib. `Set.Finite s` is a `Prop` about a `Set α` stating that the subtype `↥s` is `Finite`—use it for finite subsets of a possibly infinite type. Default to `Finite`; reach for `Fintype` only when the statement involves computable operations.

## `Finset` vs `Set` for finite combinatorics

Mathlib's `Finset α` and `Set α` have very different APIs. `Finset` is built on `Multiset` with decidable membership and supports computable `sum`, `card`, `filter`, etc.—it is the primary type for combinatorics (Hall's theorem, Turán's theorem, regularity lemma all use `Finset`). `Set α` is a predicate `α → Prop` with no decidable membership by default. For combinatorial statements (counting, pigeonhole, extremal graph theory), use `Finset`. For topology/analysis where a set happens to be finite, use `Set` with a `Set.Finite` hypothesis.

## Bundled morphisms

A mathematician writes "let f : G → H be a group homomorphism." In Mathlib, always use bundled morphism types: `MonoidHom` (notation `→*`), `RingHom` (`→+*`), `AlgHom` (`→ₐ`), `LinearMap` (`→ₗ`), `ContinuousLinearMap` (`→L`), etc. Unbundled predicate classes like `IsGroupHom` and `IsMonoidHom` are deprecated. Similarly, use bundled equivalences (`MulEquiv` `≃*`, `RingEquiv` `≃+*`, `LinearEquiv` `≃ₗ`, `OrderIso` `≃o`, `Homeomorph` `≃ₜ`) rather than a bundled hom plus a bijectivity proof.

## Coercions and enriched map types

When a map preserves multiple structures, use the richest applicable bundled type. For a linear map between normed spaces that is also continuous, use `ContinuousLinearMap` (`E →L[𝕜] F`), not `LinearMap` with a separate `Continuous` hypothesis—the latter misses the entire operator norm API. For subobjects (`Subgroup G`, `Submodule R M`), the `SetLike` coercion provides automatic `↑H : Set G` and `x ∈ H`; do not manually extract the carrier set.

## Algebraic hierarchy conventions

`Module R M` is the standard type regardless of whether `R` is a semiring, ring, or field—there is no separate `VectorSpace` type. `Algebra R A` extends `Module R A` with a ring structure on `A` and a ring hom `R →+* A`; it is the standard way to express "A is an R-algebra." Always use the most specific class: if a ring is commutative, use `CommRing`, not `Ring`.

## Topological-algebraic structure composition

Mathlib does not have a monolithic `TopologicalGroup` class in the textbook sense. Instead, it composes topology and algebra via mixin classes: `[TopologicalSpace G] [Group G] [TopologicalGroup G]` (where `TopologicalGroup` bundles `ContinuousMul` + `ContinuousInv`). For normed structures, the hierarchy is `NormedAddCommGroup` > `SeminormedAddCommGroup` > `PseudoMetricSpace` > `MetricSpace`. Use the weakest applicable class.

## Extended types: `ENNReal`, `NNReal`, `EReal`, `ENat`

Many Mathlib APIs require extended types and will not work with bare `ℝ` or `ℕ`. Measure values are in `ENNReal` (`ℝ≥0∞ = WithTop ℝ≥0`), not `ℝ`. Extended distances (`edist`) use `ENNReal`. Extended reals `EReal = WithBot (WithTop ℝ)` have the convention `⊥ + ⊤ = ⊥`. Extended naturals `ENat = WithTop ℕ` are used for potentially infinite cardinalities. Do not try to use bare `ℝ` or `ℕ` where Mathlib expects extended types.

## Quotient types

Quotient constructions in Mathlib use Lean's built-in `Quotient` type. Use the algebraic quotient type (`G ⧸ N` for groups, `R ⧸ I` for rings, `M ⧸ N` for modules) rather than raw `Quotient`. Never model a quotient as a set of equivalence classes.

## Supremum and union naming

Mathlib has a three-level naming system: binary (`sup`, `inf`), over a set (`sSup`, `sInf`), and indexed (`iSup = ⨆`, `iInf = ⨅`). For sets, `Set.iUnion = ⨆`, `Set.sUnion = ⋃₀`. The complete lattice on `Set α` has `sup = union`. Use lattice names (`iSup`, `sSup`) for abstract order theory; use `Set.iUnion`/`Set.iInter` for set-theoretic statements.

## Junk values by convention

In Mathlib, `a / 0 = 0` and `0⁻¹ = 0` by convention in any `Field` or `GroupWithZero`. Similarly, `deriv f x = 0` when `f` is not differentiable at `x`, and `tsum f = 0` when `f` is not summable. Many identities hold "for free" due to junk values, but `a * a⁻¹ = 1` requires `a ≠ 0`. Be aware of these conventions and add explicit nonzero hypotheses when needed.

## Natural number subtraction is truncated

`Nat.sub` truncates: `5 - 7 = 0`. Arithmetic identities like `(a - b) + b = a` only hold when `b ≤ a`. Prefer `a + c = b` over `b - a = c`, or work in `Int`/`Rat`/`Real`.

## Order convention: prefer `<` and `≤` over `>` and `≥`

Mathlib standardizes on `<` and `≤`. Lemma names use `lt` and `le`. Hypotheses stated with `>` or `≥` will fail to match `simp` lemmas and rewrite rules. Write `a < b`, not `b > a`.

## Polynomials are `Finsupp`-based

`Polynomial R` is implemented as `ℕ →₀ R` (finitely supported functions), not as a free algebra or quotient ring. `MvPolynomial σ R` is `(σ →₀ ℕ) →₀ R`. Use the `Polynomial` / `MvPolynomial` API directly; do not try to build polynomials from scratch.

## Star algebras

Mathlib uses a dedicated `Star` typeclass hierarchy (`StarRing`, `StarModule`, `CStarAlgebra`) for algebras with an involution. These are not ad-hoc conjugation operations—use `star x` (notation `x⋆`) rather than defining your own conjugate or adjoint. C\*-algebra arguments should use the `CStarAlgebra` class and continuous functional calculus (`cfc`), not bare normed algebra reasoning.

## Graded algebras

Mathlib has `GradedRing` and `GradedAlgebra` using `SetLike.GradedMonoid` with a `DirectSum.Decomposition`. When formalizing graded structures, use the internal grading framework (decompose into subobjects that are already subsets of the ambient ring) rather than constructing an external direct sum and then mapping it in.

## Uniform spaces

Mathlib's topological hierarchy includes a `UniformSpace` layer between `TopologicalSpace` and `MetricSpace`. Completions, Cauchy filters, and uniform continuity live here. When a result is about completeness or uniform properties (not just metric ones), use `UniformSpace` rather than `MetricSpace`.

## Formal power series and Hahn series

`PowerSeries R` (notation `R⟦X⟧`) is defined as `MvPowerSeries Unit R`. `HahnSeries Γ R` provides formal series with well-founded support over linearly ordered types, and Laurent series are `HahnSeries ℤ R`. Use `PowerSeries` for formal power series arguments; do not try to define them as limits of polynomials.

## Quaternion algebras

Mathlib has a general quaternion algebra `ℍ[R,a,b,c]` with ring, star-ring, algebra, and normed-algebra structures. When working with quaternions, use this dedicated type rather than encoding them as 4-tuples or matrices.

## Coalgebras and Hopf algebras

Mathlib has `Coalgebra R A`, `Bialgebra R A`, and `HopfAlgebra R A` (with antipode). When formalizing Hopf-algebraic arguments, use these dedicated typeclasses rather than encoding comultiplication by hand.

## Category-theoretic encoding: concrete types vs categorical types

Use concrete algebraic types (`Group G`, `Ring R`) for statements about specific structures. Use categorical types (`GroupCat`, `RingCat`, `Rep k G`) only when the statement is genuinely about functors, natural transformations, or equivalences of categories. The `ConcreteCategory` class provides a `forget` functor for bridging. Categorical composition `f ≫ g` follows diagrammatic order (f then g).

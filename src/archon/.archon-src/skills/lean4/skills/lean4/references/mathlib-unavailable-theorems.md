# Well-known methods and theorems that are hard to implement in Mathlib

> **This file is a hint, not ground truth.** Mathlib evolves; entries below may
> already be available in your project's Mathlib. Always verify a "not
> available" claim with `mcp__archon-lean-lsp__lean_local_search` /
> `lean_leansearch` before using it as a reason to abandon a proof
> route. The Lean LSP is authoritative; this document is advisory.
>
> Last verified against: **Mathlib master @ b80f227 (2026-04-20), Lean v4.30.0-rc2**

The items below are not merely "currently absent from Mathlib". The point is stronger: they are typically poor default choices in autoformalization because invoking them often drags in large missing or immature infrastructure, rather than requiring only a few local lemmas.

For each topic, we first give a short explanation, then list classical "big hammer" dependencies that should generally be avoided as default routes.

## Index

| # | Area | Line |
|---|------|------|
| 1 | Riemannian / differential geometry | 78 |
| 2 | Complex analysis | 93 |
| 3 | Algebraic topology | 108 |
| 4 | Differentiable manifolds and Lie groups | 128 |
| 5 | Number theory | 147 |
| 6 | Algebraic / arithmetic geometry | 163 |
| 7 | Partial differential equations | 181 |
| 8 | Distribution theory and harmonic analysis | 206 |
| 9 | Spectral theory and operator theory | 226 |
| 10 | Probability and stochastic processes | 250 |
| 11 | Ergodic theory | 272 |
| 12 | Homological algebra | 290 |
| 13 | Advanced commutative algebra | 304 |
| 14 | Higher category theory / homotopical algebra | 322 |
| 15 | Geometric measure theory | 337 |
| 16 | Convex geometry and optimization | 353 |
| 17 | Advanced combinatorics | 373 |
| 18 | Set theory and model theory | 390 |
| 19 | Finite group theory | 406 |
| 20 | Infinite group theory | 421 |
| 21 | Representation theory of finite groups | 437 |
| 22 | Noncommutative ring theory | 455 |
| 23 | Galois theory | 471 |
| 24 | Algebraic number theory | 484 |
| 25 | Analytic number theory | 499 |
| 26 | Modular forms and automorphic forms | 516 |
| 27 | Algebraic topology (expanded) | 534 |
| 28 | Point-set topology (advanced) | 551 |
| 29 | Topological algebra | 564 |
| 30 | Linear algebra (advanced) | 576 |
| 31 | Dynamical systems | 592 |
| 32 | Combinatorial game theory | 610 |
| 33 | Special functions | 625 |
| 34 | Real analysis (advanced) | 640 |
| 35 | Coding theory and information theory | 651 |
| 36 | Universal algebra | 667 |
| 37 | Elliptic curves | 681 |
| 38 | Nonstandard analysis | 698 |
| 39 | Several complex variables | 711 |
| 40 | Hyperbolic geometry | 727 |
| 41 | Knot theory | 740 |
| 42 | Tropical geometry | 753 |
| 43 | Graph theory (advanced) | 766 |
| 44 | Algebraic K-theory | 782 |
| 45 | Perfectoid spaces / condensed mathematics | 795 |
| 46 | p-adic Hodge theory / derived algebraic geometry | 808 |
| 47 | Design theory | 822 |
| 48 | Enumerative geometry / singularity theory | 836 |
| 49 | Numerical analysis | 849 |
| 50 | Control theory / operations research | 863 |
| 51 | Synthetic differential / non-commutative geometry | 879 |
| 52 | Well-quasi-orders (advanced) | 893 |

---

## Riemannian geometry / differential geometry

Mathlib has meaningful manifold infrastructure (`mfderiv`, smooth manifolds, some Lie-group instances), but many classical differential-geometric big hammers still depend on missing or immature infrastructure, especially differential forms and manifold-level integration. Therefore, such theorems should not be treated as default tools in autoformalization.

### Not recommended as default dependencies

- Stokes' theorem on manifolds
- de Rham theorem / de Rham cohomology arguments
- Frobenius theorem (distribution integrability)
- Hodge decomposition
- Gauss–Bonnet
- Poincaré lemma

---

## Complex analysis

Complex analysis in Mathlib has meaningful local analytic infrastructure, but classical contour-integral big hammers—residue calculus, argument principle, Rouché-type arguments, and meromorphic-function machinery—should not be treated as default tools. The same caution applies to the Riemann mapping theorem.

### Not recommended as default dependencies

- Residue theorem
- Argument principle
- Rouché's theorem
- Cauchy integral formula, especially global contour-integral versions
- Meromorphic-function toolbox
- Riemann mapping theorem

---

## Algebraic topology

Mathlib already has substantial abstract algebraic-topology infrastructure, so it would be misleading to say that algebraic topology is "almost entirely missing". However, several famous classical low-dimensional or global topological hammers remain poor default choices, since they typically require major additional infrastructure and are not the kind of results one should casually invoke in autoformalization.

### Not recommended as default dependencies

- Jordan curve theorem
- Jordan–Schoenflies theorem
- Brouwer fixed-point theorem
- Invariance of domain
- Surface classification theorem
- Borsuk–Ulam theorem
- Excision theorem
- Universal coefficient theorem (for homology and cohomology)
- Künneth formula
- Poincaré duality
- Lefschetz fixed-point theorem

---

## Differentiable manifolds and Lie groups

Mathlib has genuine smooth-manifold infrastructure and some Lie-group / Lie-algebra content, but classical low-dimensional manifold results and Lie-group classification-level theorems should not be treated as routine tools. In particular, major structural or classification results in this area are poor default choices for autoformalization.

### Not recommended as default dependencies

- Surface classification theorem
- Lie's third theorem as a black-box route
- Peter–Weyl theorem
- Killing–Cartan classification of simple Lie algebras (via Dynkin diagrams)
- Cartan's closed subgroup theorem
- Ado's theorem (faithful representation of Lie algebras)
- Iwasawa decomposition (G = KAN)
- Levi decomposition (Lie algebra = semisimple + radical)
- Weyl's complete reducibility theorem for semisimple Lie algebras
- Maximal torus theorem (conjugacy of maximal tori in compact Lie groups)

---

## Number theory

Mathlib contains meaningful number-theoretic infrastructure, but several famous arithmetic "big hammer" theories remain far beyond what should be treated as routine default support. In particular, global/local class field theory and related high-level Galois/arithmetic tools should be regarded as high-risk dependencies.

### Not recommended as default dependencies

- Global class field theory
- Local class field theory
- Chebotarev density theorem, when used via heavy class-field/Galois machinery
- Kummer theory as an off-the-shelf black box
- Neukirch–Uchida theorem (number fields determined by absolute Galois groups)
- Iwasawa's structure theorem for Λ-modules
- Riemann existence theorem–based arithmetic arguments

---

## Algebraic geometry / arithmetic geometry

Mathlib has real algebraic-geometry infrastructure, including schemes, some important higher-level constructions, an abstract **sheaf cohomology on sites** as Ext from the constant sheaf (see §12), and a definition of **ℓ-adic cohomology of schemes** via the pro-étale site (`Mathlib.AlgebraicGeometry.Sites.ElladicCohomology.AlgebraicGeometry.Scheme.EllAdicCohomology`, checked 2026-05-14). The étale topos on a scheme is set up (`Mathlib.AlgebraicGeometry.Sites.Etale`, `Sites.Proetale`). However, the *computational* cohomology toolbox (vanishing theorems, base change, comparison theorems) and the major classification machinery remain absent. Arguments that depend on actually *computing* sheaf or étale cohomology should still be treated as high-risk.

### Not recommended as default dependencies

- Riemann–Roch theorem
- Serre duality
- Sheaf cohomology *computations* on schemes as a routine black box (the definition is in Mathlib; vanishing theorems, base change, and the comparison with derived functors are not yet wired up at scheme level)
- Étale cohomology as a routine *computational* black box (the definition is in Mathlib via the pro-étale site, but base change, Poincaré duality, and the proper/smooth/finite-field comparison theorems are not)
- Birational classification of surfaces
- MMP-style theorems (cone theorem, contraction theorem, flip existence)
- Hirzebruch–Riemann–Roch theorem
- Grothendieck–Riemann–Roch theorem
- Hurwitz's formula (genus of branched covers)

---

## Partial differential equations

Mathlib has some PDE-adjacent building blocks—Picard–Lindelöf for ODEs, Lax–Milgram, a Gagliardo–Nirenberg–Sobolev inequality for smooth compactly-supported functions, a divergence theorem on rectangular boxes, and **Sobolev / Bessel-potential spaces `H^{s,p}` defined via the Fourier transform of tempered distributions** (`Mathlib.Analysis.Distribution.Sobolev.TemperedDistribution.memSobolev`, checked 2026-05-14) — but no actual PDE theory. Classical W^{k,p} defined via weak derivatives is not yet available, and there are no existence, uniqueness, or regularity results for any class of PDE. Any project that assumes PDE infrastructure would need to build virtually everything from scratch.

### Not recommended as default dependencies

- Rellich–Kondrachov compactness theorem (compact Sobolev embeddings)
- Morrey's inequality (Sobolev embedding into Hölder spaces)
- Sobolev trace theorem
- Poincaré inequality for W^{1,p}_0
- Meyers–Serrin theorem (H = W, density of smooth functions in Sobolev spaces)
- Fredholm alternative for elliptic operators
- Cauchy–Kovalevskaya theorem (local existence for analytic PDE)
- Leray–Schauder fixed-point theorem (nonlinear elliptic existence)
- Schauder interior estimates (C^{k,α} regularity)
- Calderón–Zygmund L^p estimates (W^{2,p} regularity)
- De Giorgi–Nash–Moser theorem (Hölder continuity of weak solutions)
- Hopf maximum principle (strong maximum principle)
- Hopf boundary-point lemma
- Alexandrov–Bakelman–Pucci estimate
- Dirichlet problem solvability via Perron's method
- Green's function existence for the Laplacian on bounded domains

---

## Distribution theory and harmonic analysis

Mathlib has Schwartz space, tempered distributions, and the Fourier transform on Schwartz space as a continuous linear equivalence. Core L^1/L^2 Fourier analysis is solid (Fourier inversion, Riemann–Lebesgue lemma, Parseval, Poisson summation, Plancherel for Schwartz functions). However, general distribution theory beyond tempered distributions and the deeper harmonic-analysis toolbox are absent.

### Not recommended as default dependencies

- General distributions on open subsets (not just tempered)
- Convolution of distributions
- Rellich–Kondrachov compactness theorem
- Morrey's inequality, Adams–Fournier higher-order embeddings
- Paley–Wiener theorem
- Littlewood–Paley theory
- Calderón–Zygmund singular integral theory
- Fourier multiplier theorems (Mikhlin, Hörmander–Mikhlin)
- Hardy spaces H^p
- Hausdorff–Young inequality (Fourier on L^p for 1 < p < 2)
- Restriction theorems (Stein–Tomas)

---

## Spectral theory and operator theory

Mathlib has well-developed continuous functional calculus (CFC) for C\*-algebras, Gelfand duality, compact operator theory, and spectral results for self-adjoint operators in finite dimensions and the compact case (diagonalization, orthogonal eigenspaces). The Fredholm alternative for compact operators is available (`Mathlib.Analysis.Normed.Operator.FredholmAlternative`, checked 2026-05-14). However, the full measure-theoretic spectral theorem, unbounded operators, general Fredholm theory (Fredholm index for arbitrary bounded operators), and operator semigroups are still absent.

### Not recommended as default dependencies

- Spectral theorem for bounded self-adjoint operators (full measure-theoretic version with spectral measures)
- Spectral theorem for unbounded self-adjoint operators
- Borel functional calculus
- Fredholm operators and Fredholm index
- Atiyah–Singer index theorem
- Trace-class and Schatten-class operators
- C₀-semigroups and Hille–Yosida theorem
- Stone's theorem (correspondence between unitary groups and self-adjoint operators)
- Friedrichs extension theorem
- Kato–Rellich theorem (stability of self-adjointness under perturbations)
- Von Neumann's theorem on self-adjoint extensions (deficiency indices)
- Von Neumann algebras
- Riesz–Thorin interpolation theorem
- Marcinkiewicz interpolation theorem
- Schwartz kernel theorem

---

## Probability theory and stochastic processes

Mathlib has solid discrete-time martingale theory (optional stopping, Doob's inequalities, convergence theorems), the strong law of large numbers (via Etemadi's proof), Markov kernel infrastructure with Ionescu–Tulcea, and several named distributions (Gaussian, Poisson, Exponential, Gamma, etc.). The 1-dimensional **central limit theorem** is now formalized (`Mathlib.Probability.CentralLimitTheorem.tendstoInDistribution_inv_sqrt_mul_sum_sub`, checked 2026-05-14), and **Lévy's continuity theorem** for finite-dimensional inner product spaces (`Mathlib.MeasureTheory.Measure.LevyConvergence.ProbabilityMeasure.tendsto_iff_tendsto_charFun`, checked 2026-05-14) is also available — relying on these in autoformalization is now reasonable. Sub-Gaussian random variables are formalised (`Mathlib.Probability.Moments.SubGaussian`), but specific named concentration inequalities (Hoeffding, McDiarmid, Talagrand, …) are not exported as theorems. Continuous-time theory, stochastic calculus, and CLTs in higher dimensions remain absent.

### Not recommended as default dependencies

- Central limit theorem in dimensions ≥ 2 (1-D CLT is in Mathlib; multidimensional version still missing)
- Brownian motion / Wiener process (exists in an external unmerged project only)
- Stochastic integrals, Itô calculus, Itô's formula
- Stochastic differential equations
- Girsanov theorem
- Large deviations (Cramér's theorem, Sanov's theorem)
- Hoeffding's inequality, Azuma–Hoeffding inequality, McDiarmid's inequality (sub-Gaussian framework exists, named inequalities do not)
- Bernstein's inequality, Talagrand's concentration inequality, Chernoff bound
- Continuous-time martingale theory
- Lévy's inversion formula
- Bochner's theorem (positive definite functions as Fourier transforms of measures)
- Cramér–Wold theorem
- Lévy processes, Poisson processes

---

## Ergodic theory

Mathlib has definitions for ergodic, measure-preserving, and conservative maps, Poincaré recurrence, and ergodicity of circle maps. The **Von Neumann mean ergodic theorem** is now formalised in Hilbert space (`Mathlib.Analysis.InnerProductSpace.MeanErgodic.ContinuousLinearMap.tendsto_birkhoffAverage_orthogonalProjection`, checked 2026-05-14). However, Birkhoff's pointwise ergodic theorem, mixing, entropy, and symbolic dynamics remain absent.

### Not recommended as default dependencies

- Birkhoff's pointwise ergodic theorem
- Mixing (weak mixing, strong mixing)
- Kolmogorov–Sinai measure-theoretic entropy
- Topological entropy
- Ergodic decomposition
- Symbolic dynamics (shift spaces, subshifts of finite type)
- Ruelle–Perron–Frobenius theorem (equilibrium states for Hölder potentials)
- Variational principle for topological pressure
- Sinai–Ruelle–Bowen (SRB) measure existence for Axiom A attractors

---

## Homological algebra

Mathlib has chain/cochain complexes with functorial homology, homotopy categories, derived categories (with triangulated structure and long exact homology sequences, due to Riou's work), and basic Ext/Tor functors. Local cohomology is defined. As of 2026, abstract **spectral sequence** infrastructure (`Mathlib.Algebra.Homology.SpectralSequence.*`, `Mathlib.Algebra.Homology.SpectralObject.*`) and **sheaf cohomology** defined as Ext from the constant sheaf (`Mathlib.CategoryTheory.Sites.SheafCohomology.Basic.Sheaf.H`) exist, with a Mayer–Vietoris exact sequence (`Mathlib.CategoryTheory.Sites.SheafCohomology.MayerVietoris`) and the **long exact sequence for Ext** (`Mathlib.Algebra.Homology.DerivedCategory.Ext.ExactSequences`). However, the *named* spectral sequences (Leray, Lyndon–Hochschild–Serre, Grothendieck), and most Tor/Ext computations beyond the long exact sequence remain to be built on top of this scaffolding.

### Not recommended as default dependencies

- Named spectral sequences (Grothendieck composition, Leray, Lyndon–Hochschild–Serre, …) — the abstract framework exists but specific named instances are not constructed
- Tor = Tor' isomorphism (explicitly incomplete)
- Koszul complex and Koszul homology
- Delta functors / universal delta functors
- Grothendieck duality

---

## Advanced commutative algebra

Mathlib has Noetherian/Artinian rings and modules, localization, local rings, adic completion (with exactness and flatness for finite modules over Noetherian rings), regular sequences, and basic scheme theory. However, depth, Cohen–Macaulay theory, Gorenstein rings, and homological dimension theory exist only in external unmerged projects and should not be treated as available.

### Not recommended as default dependencies

- Depth of modules
- Cohen–Macaulay rings and modules
- Gorenstein rings
- Projective, injective, and global dimension
- Auslander–Buchsbaum formula (external project only)
- Auslander–Buchsbaum–Serre theorem (regular local ring iff finite global dimension; external project only)
- Koszul complex acyclicity theorem (Koszul complex on a regular sequence is a free resolution)
- Auslander–Buchsbaum formula (pd(M) + depth(M) = depth(R))
- Catenary and universally catenary rings

---

## Higher category theory and homotopical algebra

Mathlib has abelian categories (with Freyd–Mitchell embedding), triangulated categories, monoidal/braided/symmetric monoidal categories, localization of categories, basic bicategories, enriched categories, simplicial sets, Kan complexes, and Dold–Kan correspondence. **Model categories** are now defined and developed (`Mathlib.AlgebraicTopology.ModelCategory.Basic`, with cylinder/path objects, Brown's lemma, fundamental lemma, fibrant/cofibrant homotopy theory; checked 2026-05-14). **Quasi-categories** are defined (`Mathlib.AlgebraicTopology.Quasicategory.Basic`, with strict Segal, nerve, two-truncated, strict bicategory variants). The **subobject classifier** is formalized (`Mathlib.CategoryTheory.Subobject.Classifier.Defs`), and there is t-structure work in the derived category (`Mathlib.Algebra.Homology.DerivedCategory.TStructure`, `Mathlib.Algebra.Homology.DerivedCategory.Ext.TStructure`). However, the *higher* homotopy theory built on these foundations — stable ∞-categories, perverse sheaves, full topos-theoretic logic — remains absent.

### Not recommended as default dependencies

- Stable infinity-categories
- Perverse sheaves
- Operads (no general framework)
- Topos theory beyond subobject classifier — internal logic / Kripke–Joyal semantics / Mitchell–Bénabou language
- Higher categories beyond bicategories (no general (∞,n)-category framework)
- Quillen equivalences / model-category-style transfer theorems (the model-category definitions exist but the structural classification results are still being built)

---

## Geometric measure theory

Mathlib has Hausdorff measure (with generalized gauge functions), Hausdorff dimension with basic properties, the change-of-variables formula for Lebesgue integrals, Vitali and Besicovitch covering theorems, and the Lebesgue differentiation theorem (with density points). However, the deeper geometric measure theory (rectifiability, currents, area/coarea formulas) is entirely absent.

### Not recommended as default dependencies

- Rectifiability (k-rectifiable sets and measures)
- Currents (normal, integral, flat chains)
- Area formula for Lipschitz maps
- Coarea formula
- Preiss's theorem (positive finite density a.e. implies rectifiability)
- Marstrand's density theorem
- Plateau's problem

---

## Convex geometry and optimization

Mathlib has convex sets/functions, Jensen's inequality, Carathéodory's theorem, convex cones with dual cones, Farkas' lemma, and Minkowski's convex body theorem (geometry of numbers). However, the deeper theory of convex bodies and optimization duality is absent.

### Not recommended as default dependencies

- Brunn–Minkowski inequality
- Alexandrov–Fenchel inequality (log-concavity of mixed volumes)
- Minkowski's first inequality for mixed volumes
- Minkowski's existence theorem (prescribing surface area measure)
- Isoperimetric inequality (via Brunn–Minkowski)
- John ellipsoid theorem (maximal volume ellipsoid in a convex body)
- Blaschke selection theorem (compactness in Hausdorff metric)
- Alexandrov's theorem (a.e. second-order differentiability of convex functions)
- Linear programming duality (strong duality theorem)
- KKT conditions / Slater's constraint qualification
- Prékopa–Leindler inequality

---

## Advanced combinatorics

Mathlib has Turán's theorem, Szemerédi regularity lemma, Van der Waerden's and Hales–Jewett theorems, Roth's theorem on 3-term APs, Cauchy–Davenport, Hall's marriage theorem, basic matroid definitions (independence axioms, duality, closure, rank), Young diagrams, and semistandard Young tableaux (`Mathlib.Combinatorics.Young.SemistandardTableau`, checked 2026-05-14). However, the algebraic and probabilistic-method theory built on these objects remains absent.

### Not recommended as default dependencies

- Symmetric functions (Schur functions, power sums, ring of symmetric functions)
- Standard Young tableaux and Robinson–Schensted–Knuth correspondence (semistandard tableaux are defined, but RSK and standard tableaux beyond basic shape are not)
- Specht modules and representation theory of symmetric groups
- Lovász Local Lemma and probabilistic method tools
- Ramsey numbers and explicit Ramsey bounds
- Matroid minors, connectivity, and representability (external project only)
- Erdős–Stone theorem
- Chromatic polynomial theory

---

## Set theory and model theory

Mathlib has ordinal and cardinal arithmetic, basic ZFC sets, Polish spaces with analytic sets and Lusin's separation/Souslin theorems, first-order languages with completeness and compactness, **model-theoretic ultraproducts with Łoś's theorem** (`Mathlib.ModelTheory.Ultraproducts.FirstOrder.Language.Ultraproduct.sentence_realize`, checked 2026-05-14), Fraïssé limits (`Mathlib.ModelTheory.Fraisse`), and Presburger arithmetic with definability/semilinear decompositions. However, forcing, large cardinals, advanced classification model theory, and deeper descriptive set theory remain absent. The Flypitch project (independence of CH via Boolean-valued models) was done in Lean 3 and has not been ported.

### Not recommended as default dependencies

- Forcing and independence results
- Boolean-valued models
- Large cardinal axioms (inaccessible, measurable, Woodin, etc.)
- Inner models (L, core models)
- Model-theoretic stability theory, Morley's theorem, o-minimality
- Borel determinacy, projective determinacy
- Wadge hierarchy, effective descriptive set theory

---

## Finite group theory

Mathlib has fully formalized Sylow theorems, solvable and nilpotent groups, group actions with Burnside's lemma, Jordan–Hölder theorem (in a lattice-theoretic setting), p-groups, the transfer homomorphism (including Burnside's normal p-complement theorem), and Schur–Zassenhaus. However, classification-level results and several structural theorems are absent.

### Not recommended as default dependencies

- Classification of finite simple groups (CFSG)
- Feit–Thompson (odd order) theorem (formalized in Coq, not ported to Lean)
- Hall's theorem for solvable groups (existence of Hall π-subgroups)
- Burnside's p^a q^b theorem
- Fitting's theorem (product of normal nilpotent subgroups is nilpotent)
- Gaschütz's theorem (complementation of chief factors)

---

## Infinite group theory

Mathlib has free groups, group presentations (as quotients of free groups), and Schreier's lemma (subgroups of free groups are free). However, combinatorial and geometric group theory are essentially absent.

### Not recommended as default dependencies

- Bass–Serre theory (groups acting on trees, amalgamated products, HNN extensions)
- Stallings' theorem (finitely generated groups with more than one end split over a finite subgroup)
- Grushko's theorem (rank of free product = sum of ranks)
- Gromov's polynomial growth theorem (polynomial growth implies virtually nilpotent)
- Tits alternative (linear groups are virtually solvable or contain free subgroups)
- Mostow rigidity theorem
- Dunwoody's accessibility theorem

---

## Representation theory of finite groups

Mathlib has representations (`Representation k G V`), finite-dimensional representations (`FDRep k G`), Maschke's theorem, Schur's lemma (both categorical and module-theoretic), character theory with orthogonality of irreducible characters, induced representations with Frobenius reciprocity, and group cohomology (H^n with Hilbert's Theorem 90). However, modular representation theory and several structural results are absent.

### Not recommended as default dependencies

- Brauer theory / modular representation theory (blocks, defect groups, decomposition matrices)
- Number of irreducibles = number of conjugacy classes
- Character tables as a computational framework
- Burnside's theorem via characters
- Clifford's theorem (restriction to normal subgroups decomposes into conjugate irreducibles)
- Mackey's irreducibility criterion
- Projective representations

*Note: Artin–Wedderburn for semisimple Artinian rings is now in Mathlib — see §22.*

---

## Noncommutative ring theory

Mathlib has simple modules and semisimple modules/rings (with Schur's lemma), Jacobson radical (as intersection of maximal ideals), Artinian ring theory (including "prime = maximal" and "reduced Artinian = product of fields"), Ore localization, Jacobson rings, and the **Wedderburn–Artin theorem** (`Mathlib.RingTheory.SimpleModule.WedderburnArtin.IsSemisimpleRing.exists_algEquiv_pi_matrix_end_mulOpposite`, checked 2026-05-14). However, several core noncommutative tools are still missing.

### Not recommended as default dependencies

- Morita equivalence
- Density theorem (Jacobson/Chevalley)
- Goldie's theorem
- Krull–Schmidt theorem for modules
- Hopkins–Levitzki theorem (Artinian implies Noetherian for modules)
- Levitzki's theorem (nil ideals are nilpotent in Noetherian rings)
- Amitsur's theorem (Jacobson radical of R[x])

---

## Galois theory

Mathlib has splitting fields, algebraic closures, the fundamental theorem of Galois theory (finite case), Abel–Ruffini (one direction: solvable by radicals implies solvable Galois group), Krull topology on automorphism groups, and group cohomology with Hilbert's Theorem 90. However, the infinite Galois correspondence and deeper cohomological tools are absent.

### Not recommended as default dependencies

- Infinite Galois correspondence (closed subgroups ↔ intermediate fields) as a theorem
- Full Abel–Ruffini (converse: solvable Galois group implies solvable by radicals)
- Galois cohomology for profinite groups (continuous/profinite group cohomology)
- Brauer groups via Galois cohomology

---

## Algebraic number theory

Mathlib has Dedekind domains (with three equivalent characterizations), class groups, finiteness of class number for global fields, Dirichlet's unit theorem, class number formula, p-adic numbers with Hensel's lemma, completions of number fields at infinite places, and the **adele ring** of a number field (`Mathlib.NumberTheory.NumberField.AdeleRing`, checked 2026-05-14). The **product formula** for number fields is now formalized (`Mathlib.NumberTheory.NumberField.ProductFormula`). Basic ramification/inertia is available (`Mathlib.NumberTheory.RamificationInertia.*`, `Mathlib.RingTheory.Valuation.RamificationGroup`). However, higher ramification and explicit computational tools are largely absent.

### Not recommended as default dependencies

- Hasse–Arf theorem (upper numbering jumps at integers for abelian extensions)
- Higher ramification groups beyond basic definitions
- Different-discriminant theorem, conductor-discriminant formula
- Strong approximation theorem (adele ring is in Mathlib but strong approximation is not yet)
- Explicit class number computations for specific fields
- Local/global class field theory

---

## Analytic number theory

Mathlib has arithmetic functions (Euler's totient, Möbius, von Mangoldt), the L-series framework with convergence theory, the Riemann zeta function (functional equation, non-vanishing on Re(s) ≥ 1, Basel problem), Dirichlet L-functions, Dirichlet's theorem on primes in arithmetic progressions, and the Selberg sieve (upper bound version). The prime number theorem exists in an external project (PNT+ by Kontorovich/Tao) being merged into Mathlib. However, deeper analytic methods are absent.

### Not recommended as default dependencies

- Prime number theorem (external project, not yet fully in Mathlib proper)
- Circle method (Hardy–Littlewood)
- Large sieve, Bombieri–Vinogradov theorem
- Quantitative zero-free regions beyond Re(s) ≥ 1
- Perron's formula (recovering partial sums via contour integration)
- Landau's theorem (singularity at abscissa of convergence for non-negative coefficients)
- Phragmén–Lindelöf convexity principle for L-functions
- Approximate functional equation for zeta and L-functions

---

## Modular forms and automorphic forms

Mathlib has modular forms and cusp forms (as extensions of slash-invariant forms), Eisenstein series for weight k and level Γ(N), the upper half-plane with SL(2,ℤ) action and fundamental domain, the graded ring of modular forms, and **q-expansions** for modular forms of level Γ(n) as analytic functions on the open unit disc and as power series (`Mathlib.NumberTheory.ModularForms.QExpansion.ModularFormClass.qExpansion`, checked 2026-05-14). The q-expansion of weight-k Eisenstein series in terms of divisor sums and Bernoulli numbers is also formalized (`Mathlib.NumberTheory.ModularForms.EisensteinSeries.QExpansion`). However, Hecke operators, the structural dimension theory, and most of the Hecke-eigenform-based theory remain absent.

### Not recommended as default dependencies

- Hecke operators and Hecke eigenforms
- Petersson inner product
- Dimension formula for M_k(Γ) (via Riemann–Roch on modular curves)
- Valence formula (weighted zero count = k/12)
- Sturm's bound (modular form determined by finitely many Fourier coefficients)
- Newforms / Atkin–Lehner theory
- Modular curves as algebraic curves
- L-functions attached to modular forms
- Automorphic forms (general definition deliberately deferred)

---

## Algebraic topology (expanded)

Beyond the items listed above (Jordan curve, Brouwer, etc.), Mathlib has the fundamental groupoid and fundamental group, singular homology with homology of spheres and homotopy invariance (`Mathlib.AlgebraicTopology.SingularHomology.HomotopyInvariance`, checked 2026-05-14), basic covering space definitions, **higher homotopy groups** `π_n X x` for all `n` with the group structure (`Mathlib.Topology.Homotopy.HomotopyGroup.HomotopyGroup.Pi`), and **CW complexes** in both abstract and classical formulations (`Mathlib.Topology.CWComplex.Abstract.Basic`, `Mathlib.Topology.CWComplex.Classical.Basic`/`Finite`/`Graph`/`Subcomplex`). However, the global theorems built on this scaffolding remain absent.

### Not recommended as default dependencies

- Singular cohomology
- Cellular homology of CW complexes (CW complexes are defined, cellular homology is not derived)
- Mayer–Vietoris sequence for singular homology (a Mayer–Vietoris sequence for sheaf cohomology exists separately; see §12)
- Hurewicz theorem
- Eilenberg–Steenrod axioms as a structural theorem
- Galois correspondence for covering spaces (connected covers ↔ conjugacy classes of subgroups of π₁)
- Whitehead's theorem (weak homotopy equivalence between CW complexes is a homotopy equivalence)
- Freudenthal suspension theorem

---

## Point-set topology (advanced)

Mathlib has paracompactness, Lindelöf spaces, Urysohn metrization theorem, Stone–Čech compactification, fiber bundles, and basic covering spaces. However, dimension theory is entirely absent.

### Not recommended as default dependencies

- Nagata–Smirnov metrization theorem
- Menger–Urysohn theorem (inductive dimension of ℝ^n is n)
- Hurewicz dimension-raising theorem
- Alexandroff's embedding theorem (compact metric spaces of dimension ≤ n embed in ℝ^{2n+1})

---

## Topological algebra

Mathlib has Haar measure (existence and uniqueness on locally compact Hausdorff groups), the Pontryagin dual (definition as continuous homomorphisms to the circle), locally convex spaces with seminorm-based topologies and Banach–Steinhaus, profinite spaces, and topological group completions. **Pontryagin duality** is proved in the **finite abelian** case (`Mathlib.Analysis.Fourier.FiniteAbelian.PontryaginDuality`, checked 2026-05-14). The full duality theorem for general locally compact abelian groups is still absent.

### Not recommended as default dependencies

- Pontryagin duality theorem for general locally compact abelian groups (finite-abelian case is in Mathlib)
- Profinite completion functor for groups
- Peter–Weyl theorem (also listed under Lie groups)

---

## Linear algebra (advanced)

Mathlib has eigenvalues/eigenvectors, spectral decomposition for self-adjoint operators (finite-dimensional), bilinear and quadratic forms, Clifford algebras, determinants, Cayley–Hamilton, minimal polynomial, the structure theorem for finitely generated modules over PIDs (via Smith normal form), Jordan–Chevalley–Dunford decomposition, tensor products, exterior algebra/powers, symmetric powers, and **Grassmannians** parametrising locally-free quotients (`Mathlib.RingTheory.Grassmannian.Module.Grassmannian`, checked 2026-05-14). However, several canonical form results and structural tools are absent.

### Not recommended as default dependencies

- Jordan normal form (Jordan–Chevalley–Dunford exists, but Jordan blocks do not)
- Rational canonical form
- Schur decomposition theorem (every matrix is unitarily triangularizable)
- Simultaneous diagonalization theorem (commuting diagonalizable matrices)
- Witt groups of quadratic forms
- Geometric structure of Grassmannians (the underlying set/module is defined, but smooth/projective-variety structure and the Schubert decomposition are not)
- Polar decomposition in GL(n)

---

## Dynamical systems

Mathlib has rotation numbers for circle homeomorphisms, omega-limit sets, fixed/periodic points, integral curves of vector fields on Banach manifolds (via Picard–Lindelöf), and basic ergodic theory definitions. However, the qualitative theory of dynamical systems is absent.

### Not recommended as default dependencies

- Hartman–Grobman theorem (topological conjugacy near hyperbolic fixed points)
- Stable manifold theorem
- Poincaré–Bendixson theorem (long-time behavior in planar systems)
- Smale's horseshoe theorem
- Center manifold theorem
- KAM theorem (persistence of quasi-periodic orbits)
- Sharkovskii's theorem (ordering of periods for interval maps)
- Morse inequalities and Morse lemma
- Lefschetz fixed-point theorem (external projects only)

---

## Combinatorial game theory

Combinatorial game theory has been **removed from core Mathlib** (`PGame`/`Surreal`/`Game`/`Nim` no longer appear in `Mathlib/` as of the 2026-04-20 master; the surviving file is `Mathlib.Order.GameAdd`, an order-theoretic well-foundedness helper). The infrastructure now lives in a separate downstream project (combinatorial-games). Classical zero-sum and equilibrium-style game theory is also absent. (checked 2026-05-14, grep for `Surreal`/`PGame`/`IGame` returned empty inside Mathlib.)

### Not recommended as default dependencies

- Surreal numbers, surreal arithmetic, surreal-as-ordered-field (no longer in Mathlib core)
- Pre-games (`PGame`), Conway induction, game arithmetic (no longer in Mathlib core)
- Sprague–Grundy theorem (impartial games are equivalent to nimbers)
- Von Neumann's minimax theorem (two-player zero-sum games)
- Nash's existence theorem (Nash equilibria in finite games)
- Zermelo's theorem (determinacy of finite games of perfect information)

---

## Special functions

Mathlib has a thorough Gamma function formalization (Euler's integral, recurrence, reflection formula, Legendre duplication, Bohr–Mollerup uniqueness), Beta function, Riemann zeta function (with functional equation), Hurwitz zeta function (`Mathlib.NumberTheory.LSeries.HurwitzZeta`), Bernstein polynomials, and the **ordinary (Gaussian) hypergeometric function ₂F₁** in Banach algebras (`Mathlib.Analysis.SpecialFunctions.OrdinaryHypergeometric.ordinaryHypergeometric`, checked 2026-05-14). However, most other classical special functions are absent.

### Not recommended as default dependencies

- Bessel functions
- Spherical harmonics
- Elliptic functions and elliptic integrals
- Generalised hypergeometric functions ₚFq for `(p, q) ≠ (2, 1)`
- Airy functions, Whittaker functions
- Legendre's relation for elliptic integrals

---

## Real analysis (advanced)

Mathlib has monotone/dominated convergence, Fatou's lemma, bounded variation with a.e. differentiability, Egorov's theorem, Vitali and Besicovitch covering theorems, Lebesgue differentiation theorem (with density points), Vitali convergence theorem, the **Vitali–Carathéodory theorem** (`Mathlib.MeasureTheory.Integral.Bochner.VitaliCaratheodory`, checked 2026-05-14), the **Lebesgue FTC for absolutely continuous functions** (`Mathlib.MeasureTheory.Integral.IntervalIntegral.AbsolutelyContinuousFun.AbsolutelyContinuousOnInterval.integral_deriv_eq_sub`), and **Stirling's formula** (`Mathlib.Analysis.SpecialFunctions.Stirling`). Some classical function-space concepts remain absent.

### Not recommended as default dependencies

- Banach–Zaretsky theorem (BV + maps null sets to null sets ↔ absolutely continuous)
- Lusin's theorem (measurable functions are continuous on large sets) (checked 2026-05-14, confirmed absent)

---

## Coding theory and information theory

The `Mathlib.InformationTheory` directory now exists. Mathlib has Hamming distance and Hamming norm (`Mathlib.InformationTheory.Hamming`), the **Kraft–McMillan inequality** for uniquely decodable codes (`Mathlib.InformationTheory.Coding.KraftMcMillan.kraft_mcmillan_inequality`, checked 2026-05-14), uniquely decodable codes (`Mathlib.InformationTheory.Coding.UniquelyDecodable`), and **Kullback–Leibler divergence** with basic chain-rule lemmas (`Mathlib.InformationTheory.KullbackLeibler.*`). However, the bulk of classical coding theory and Shannon information theory is still absent.

### Not recommended as default dependencies

- Singleton bound and MDS codes
- Hamming bound (sphere-packing bound)
- Gilbert–Varshamov bound
- MacWilliams identity (weight enumerator duality)
- Shannon's noisy channel coding theorem
- Shannon's source coding theorem (the Kraft–McMillan inequality is a prerequisite that exists; the full theorem does not)
- Reed–Solomon code construction and minimum distance

---

## Universal algebra

Mathlib has congruence relations for specific structures (groups, rings) and free objects for specific algebraic theories (free groups, free modules, free algebras). However, there is no general universal-algebraic framework.

### Not recommended as default dependencies

- Varieties in the sense of universal algebra (classes closed under HSP)
- Birkhoff's HSP theorem
- General equational logic
- General congruence lattices for arbitrary algebraic structures
- General notion of signature + algebra over a signature + term algebra

---

## Elliptic curves

Mathlib has elliptic curves in Weierstrass form with all five coefficients, the j-invariant (with constructors `ofJ0`, `ofJ1728`, `ofJNe0Or1728`), and the group law on nonsingular projective points proved to form an abelian group. However, the deeper arithmetic of elliptic curves is absent.

### Not recommended as default dependencies

- Mordell–Weil theorem (finite generation of rational points)
- Hasse's theorem (|#E(𝔽_q) − q − 1| ≤ 2√q)
- Lutz–Nagell theorem (torsion points over ℚ have integer coordinates)
- Mazur's torsion theorem (classification of torsion subgroups of E(ℚ))
- Isogenies and the Tate module
- Heights and the Néron–Tate height pairing
- Tate's algorithm (reduction type at a prime)
- Modularity theorem

---

## Nonstandard analysis

Mathlib has hyperreal numbers (`ℝ*`) constructed as an ultraproduct of real sequences, with infinitesimals, infinite elements, and the standard part function. However, the transfer principle (Łoś's theorem in full generality) is not formalized, making systematic nonstandard analysis proofs impossible.

### Not recommended as default dependencies

- Transfer principle (general form of Łoś's theorem)
- Internal set theory
- Loeb measure construction
- Nonstandard characterizations of compactness, continuity, integrability

---

## Several complex variables

Mathlib has complex analysis in one variable (holomorphic functions, Cauchy's theorem for disks, power series, analytic continuation). However, several complex variables is a complete gap—no multivariable holomorphy theory exists.

### Not recommended as default dependencies

- Hartogs' extension theorem (holomorphic functions extend across compact singularities in ℂ^n, n ≥ 2)
- Oka's coherence theorem (sheaf of holomorphic functions is coherent)
- Cartan's theorems A and B (coherent sheaves on Stein manifolds)
- Levi problem (equivalence of pseudoconvexity and domain of holomorphy)
- Weierstrass preparation theorem (several variables)
- Grauert's direct image theorem
- Hörmander's L² estimates for ∂̄

---

## Hyperbolic geometry

Mathlib has no hyperbolic geometry. There is no Poincaré disk or half-plane model, no hyperbolic metric, no hyperbolic isometries, and no models of non-Euclidean geometry.

### Not recommended as default dependencies

- Poincaré disk or half-plane models
- Hyperbolic metric and geodesics
- Hyperbolic isometry groups
- Hyperbolic trigonometry

---

## Knot theory

Mathlib has quandles and racks (`Mathlib.Algebra.Quandle`) as pure algebraic structures, but all knot-theoretic applications are absent.

### Not recommended as default dependencies

- Knot diagrams and Reidemeister moves
- Knot invariants (Jones polynomial, Alexander polynomial, HOMFLY-PT)
- Knot groups
- Fox coloring and quandle colorings of knots

---

## Tropical geometry

Mathlib has the tropical semiring (`Mathlib.Algebra.Tropical.Basic`) where addition is min and multiplication is addition. However, there is no tropical geometry—no tropical polynomials, varieties, or curves.

### Not recommended as default dependencies

- Tropical polynomials and tropical hypersurfaces
- Tropical varieties and their polyhedral structure
- Tropical curves and tropical intersection theory
- Kapranov's theorem (tropicalization of varieties)

---

## Graph theory (advanced)

Mathlib has `SimpleGraph` with extensive basic theory (connectivity, subgraphs, matchings, coloring, Hamiltonian paths, Turán's theorem, regularity lemma), and now also **Tutte's theorem** characterising graphs with perfect matchings via Tutte violators (`Mathlib.Combinatorics.SimpleGraph.Tutte.SimpleGraph.tutte`, checked 2026-05-14). However, planarity, graph minors, and several advanced structural theorems are absent.

### Not recommended as default dependencies

- Kuratowski's theorem (planar iff no K₅ or K₃,₃ subdivision)
- Wagner's theorem (planar iff no K₅ or K₃,₃ minor)
- Robertson–Seymour graph minor theorem (WQO under minors)
- Max-flow min-cut theorem (Ford–Fulkerson)
- Menger's theorem (disjoint paths = min vertex cut)
- Brooks' theorem (χ(G) ≤ Δ(G) unless complete or odd cycle)
- Vizing's theorem (edge chromatic number is Δ or Δ+1)

---

## Algebraic K-theory

Completely absent from Mathlib. No K₀, K₁, higher K-groups, or Quillen's construction.

### Not recommended as default dependencies

- K₀ of a ring (Grothendieck group of projective modules)
- K₁ and Whitehead's lemma
- Higher algebraic K-theory (Quillen, Waldhausen)
- K-theory of topological spaces or C\*-algebras

---

## Perfectoid spaces and condensed mathematics

Mathlib now has a substantial **`Mathlib.Condensed`** namespace with condensed sets and abelian groups (`Mathlib.Condensed.Basic`-style files in `Discrete`, `Light`, `Module`, `Solid`, `TopComparison`, `Functors`, `Limits`, `Equivalence`, etc.; checked 2026-05-14). **Solid abelian groups** (`Mathlib.Condensed.Solid`), light condensed objects, and condensed modules are formalised. On the perfectoid side, `Mathlib.RingTheory.Perfectoid` exists with the **untilt function** (`Mathlib.RingTheory.Perfectoid.Untilt.PreTilt.untilt`), the **Fontaine theta map** (`Mathlib.RingTheory.Perfectoid.FontaineTheta`), and the **de Rham period ring** `B_dR⁺`/`B_dR` (`Mathlib.RingTheory.Perfectoid.BDeRham`). The big classification theorems still depend on additional structure that isn't there yet.

### Not recommended as default dependencies

- Scholze's tilting equivalence as a theorem (perfectoid spaces over K ≃ perfectoid spaces over K♭) — the untilt map exists, the equivalence does not
- Almost purity theorem (Faltings/Scholze)
- Fontaine–Winterberger theorem (isomorphism of absolute Galois groups)
- Clausen–Scholze main theorem on liquid modules

---

## p-adic Hodge theory and derived algebraic geometry

Mathlib has p-adic numbers, basic valuation theory, the **de Rham period ring** `B_dR⁺` and `B_dR` (`Mathlib.RingTheory.Perfectoid.BDeRham`, checked 2026-05-14), and the Fontaine theta map (`Mathlib.RingTheory.Perfectoid.FontaineTheta`). However, the crystalline and semistable period rings, comparison theorems, derived frameworks, and motivic theory are absent. Quasi-categories are defined (see §14), but the rest of derived algebraic geometry is not.

### Not recommended as default dependencies

- Fontaine's crystalline and semistable period rings B_cris and B_st (B_dR is in Mathlib)
- p-adic comparison theorems (Hodge–Tate, crystalline, de Rham)
- Derived schemes and derived stacks
- Stable ∞-categories (only basic quasi-categories are in Mathlib)
- Motivic cohomology and motivic homotopy theory

---

## Design theory

Combinatorial design theory is completely absent from Mathlib. No block designs, Steiner systems, or Latin squares.

### Not recommended as default dependencies

- Fisher's inequality (b ≥ v in 2-designs)
- Bruck–Ryser–Chowla theorem (necessary conditions for symmetric designs)
- Ray-Chaudhuri–Wilson theorem (generalization of Fisher's inequality)
- Wilson's existence theorem (asymptotic existence of (v,k,1)-designs)
- Bose–Shrikhande–Parker theorem (disproof of Euler's conjecture on orthogonal Latin squares)

---

## Enumerative geometry and singularity theory

Both areas are completely absent from Mathlib. No intersection theory, Schubert calculus, or singularity classification.

### Not recommended as default dependencies

- Schubert calculus and intersection numbers on Grassmannians
- Chern classes and characteristic classes
- Resolution of singularities
- Milnor numbers and singularity classification

---

## Numerical analysis

Mathlib has no numerical analysis. There are no convergence theorems for numerical methods, no finite element theory, and no error analysis for discretizations.

### Not recommended as default dependencies

- Convergence of iterative methods (Newton's method, fixed-point iteration)
- Finite element method (FEM) theory
- Numerical linear algebra (LU, QR, SVD as algorithms with error bounds)
- Quadrature rules and error estimates
- Stability analysis of numerical schemes

---

## Control theory and operations research

Both areas are completely absent from Mathlib.

### Not recommended as default dependencies

- Kalman controllability/observability rank conditions
- Lyapunov's direct method (stability via Lyapunov functions)
- Pontryagin maximum principle
- Hamilton–Jacobi–Bellman equation
- Strong duality theorem for linear programming
- Simplex method correctness and termination
- König's theorem (bipartite matching and minimum vertex cover)

---

## Synthetic differential geometry and non-commutative geometry

Both are completely absent from Mathlib.

### Not recommended as default dependencies

- Kock–Lawvere axiom and nilpotent infinitesimals
- Microlinearity and synthetic tangent bundles
- Spectral triples (Connes)
- Cyclic homology and noncommutative differential forms
- Noncommutative tori

---

## Well-quasi-orders (advanced)

Mathlib has a `WellQuasiOrder` typeclass and Dickson-like results for product orderings. However, the major structural theorems are absent.

### Not recommended as default dependencies

- Kruskal's tree theorem
- Higman's lemma (WQO on finite sequences)
- Nash-Williams' minimal bad sequence argument
- Robertson–Seymour graph minor theorem (also listed under graph theory)

---

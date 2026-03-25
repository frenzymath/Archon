# Well-known methods and theorems that are hard to implement in Mathlib

The items below are not merely "currently absent from Mathlib". The point is stronger: they are typically poor default choices in autoformalization because invoking them often drags in large missing or immature infrastructure, rather than requiring only a few local lemmas.

For each topic, we first give a short explanation, then list classical "big hammer" dependencies that should generally be avoided as default routes.

## Index

| # | Area | Line |
|---|------|------|
| 1 | Riemannian / differential geometry | 51 |
| 2 | Complex analysis | 66 |
| 3 | Algebraic topology | 81 |
| 4 | Differentiable manifolds and Lie groups | 101 |
| 5 | Number theory | 120 |
| 6 | Algebraic / arithmetic geometry | 136 |
| 7 | Partial differential equations | 154 |
| 8 | Distribution theory and harmonic analysis | 179 |
| 9 | Spectral theory and operator theory | 199 |
| 10 | Probability and stochastic processes | 223 |
| 11 | Ergodic theory | 246 |
| 12 | Homological algebra | 265 |
| 13 | Advanced commutative algebra | 281 |
| 14 | Higher category theory / homotopical algebra | 299 |
| 15 | Geometric measure theory | 316 |
| 16 | Convex geometry and optimization | 332 |
| 17 | Advanced combinatorics | 352 |
| 18 | Set theory and model theory | 369 |
| 19 | Finite group theory | 386 |
| 20 | Infinite group theory | 401 |
| 21 | Representation theory of finite groups | 417 |
| 22 | Noncommutative ring theory | 434 |
| 23 | Galois theory | 451 |
| 24 | Algebraic number theory | 464 |
| 25 | Analytic number theory | 479 |
| 26 | Modular forms and automorphic forms | 496 |
| 27 | Algebraic topology (expanded) | 515 |
| 28 | Point-set topology (advanced) | 533 |
| 29 | Topological algebra | 546 |
| 30 | Linear algebra (advanced) | 558 |
| 31 | Dynamical systems | 574 |
| 32 | Combinatorial game theory | 592 |
| 33 | Special functions | 606 |
| 34 | Real analysis (advanced) | 621 |
| 35 | Coding theory and information theory | 635 |
| 36 | Universal algebra | 651 |
| 37 | Elliptic curves | 665 |
| 38 | Nonstandard analysis | 682 |
| 39 | Several complex variables | 695 |
| 40 | Hyperbolic geometry | 711 |
| 41 | Knot theory | 724 |
| 42 | Tropical geometry | 737 |
| 43 | Graph theory (advanced) | 750 |
| 44 | Algebraic K-theory | 767 |
| 45 | Perfectoid spaces / condensed mathematics | 780 |
| 46 | p-adic Hodge theory / derived algebraic geometry | 794 |
| 47 | Design theory | 808 |
| 48 | Enumerative geometry / singularity theory | 822 |
| 49 | Numerical analysis | 835 |
| 50 | Control theory / operations research | 851 |
| 51 | Synthetic differential / non-commutative geometry | 865 |
| 52 | Well-quasi-orders (advanced) | 879 |

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

Mathlib has real algebraic-geometry infrastructure, including schemes and some important higher-level constructions, so one should not describe the area as absent. However, many classical cohomological and birational "big hammer" tools remain far from being routine defaults. In particular, arguments that depend on sheaf cohomology or major classification machinery should be treated as high-risk.

### Not recommended as default dependencies

- Riemann–Roch theorem
- Serre duality
- Sheaf cohomology on schemes as a routine black box
- Étale cohomology as a routine black box
- Birational classification of surfaces
- MMP-style theorems (cone theorem, contraction theorem, flip existence)
- Hirzebruch–Riemann–Roch theorem
- Grothendieck–Riemann–Roch theorem
- Hurwitz's formula (genus of branched covers)

---

## Partial differential equations

Mathlib has some PDE-adjacent building blocks—Picard–Lindelöf for ODEs, Lax–Milgram, a Gagliardo–Nirenberg–Sobolev inequality for smooth compactly-supported functions, and a divergence theorem on rectangular boxes—but no actual PDE theory. Sobolev spaces (W^{k,p}) with weak derivatives are not defined, and there are no existence, uniqueness, or regularity results for any class of PDE. Any project that assumes PDE infrastructure would need to build virtually everything from scratch.

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

Mathlib has well-developed continuous functional calculus (CFC) for C\*-algebras, Gelfand duality, compact operator theory, and spectral results for self-adjoint operators in finite dimensions and the compact case (diagonalization, orthogonal eigenspaces). However, the full measure-theoretic spectral theorem, unbounded operators, Fredholm theory, and operator semigroups are entirely absent.

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

Mathlib has solid discrete-time martingale theory (optional stopping, Doob's inequalities, convergence theorems), the strong law of large numbers (via Etemadi's proof), Markov kernel infrastructure with Ionescu–Tulcea, and several named distributions (Gaussian, Poisson, Exponential, Gamma, etc.). However, continuous-time theory, stochastic calculus, and many classical probabilistic tools are absent. The central limit theorem is not formalized.

### Not recommended as default dependencies

- Central limit theorem
- Brownian motion / Wiener process (exists in an external unmerged project only)
- Stochastic integrals, Itô calculus, Itô's formula
- Stochastic differential equations
- Girsanov theorem
- Large deviations (Cramér's theorem, Sanov's theorem)
- Hoeffding's inequality, Azuma–Hoeffding inequality, McDiarmid's inequality
- Bernstein's inequality, Talagrand's concentration inequality, Chernoff bound
- Continuous-time martingale theory
- Lévy's continuity theorem (characteristic functions and convergence in distribution)
- Lévy's inversion formula
- Bochner's theorem (positive definite functions as Fourier transforms of measures)
- Cramér–Wold theorem
- Lévy processes, Poisson processes

---

## Ergodic theory

Mathlib has definitions for ergodic, measure-preserving, and conservative maps, Poincaré recurrence, and ergodicity of circle maps. However, the fundamental convergence theorems of ergodic theory are absent, as are mixing, entropy, and symbolic dynamics.

### Not recommended as default dependencies

- Birkhoff's pointwise ergodic theorem
- Von Neumann's mean ergodic theorem
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

Mathlib has chain/cochain complexes with functorial homology, homotopy categories, derived categories (with triangulated structure and long exact homology sequences, due to Riou's work), and basic Ext/Tor functors. Local cohomology is defined. However, spectral sequences are entirely absent, sheaf cohomology is not yet connected to derived functors, and many Ext/Tor computations remain incomplete.

### Not recommended as default dependencies

- Spectral sequences (Grothendieck, Leray, Lyndon–Hochschild–Serre, etc.)
- Sheaf cohomology as right-derived functor of global sections
- Long exact sequence for Ext (not yet proven)
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

Mathlib has abelian categories (with Freyd–Mitchell embedding), triangulated categories, monoidal/braided/symmetric monoidal categories, localization of categories, basic bicategories, enriched categories, simplicial sets, Kan complexes, and Dold–Kan correspondence. However, model categories, infinity-categories, t-structures, and topos theory are absent.

### Not recommended as default dependencies

- Model categories (no definitions exist)
- Quasi-categories / infinity-categories
- Stable infinity-categories
- t-structures on triangulated or derived categories
- Perverse sheaves
- Operads
- Topos theory (subobject classifier, internal logic)
- Higher categories beyond bicategories

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

Mathlib has Turán's theorem, Szemerédi regularity lemma, Van der Waerden's and Hales–Jewett theorems, Roth's theorem on 3-term APs, Cauchy–Davenport, Hall's marriage theorem, and basic matroid definitions (independence axioms, duality, closure, rank). However, symmetric functions, Young tableaux, matroid minors, Ramsey numbers, and probabilistic-method tools are absent.

### Not recommended as default dependencies

- Symmetric functions (Schur functions, power sums, ring of symmetric functions)
- Young tableaux and Robinson–Schensted–Knuth correspondence
- Specht modules and representation theory of symmetric groups
- Lovász Local Lemma and probabilistic method tools
- Ramsey numbers and explicit Ramsey bounds
- Matroid minors, connectivity, and representability (external project only)
- Erdős–Stone theorem
- Chromatic polynomial theory

---

## Set theory and model theory

Mathlib has ordinal and cardinal arithmetic, basic ZFC sets, Polish spaces with analytic sets and Lusin's separation/Souslin theorems, first-order languages with completeness and compactness. However, forcing, large cardinals, advanced model theory, and deeper descriptive set theory are absent. The Flypitch project (independence of CH via Boolean-valued models) was done in Lean 3 and has not been ported.

### Not recommended as default dependencies

- Forcing and independence results
- Boolean-valued models
- Large cardinal axioms (inaccessible, measurable, Woodin, etc.)
- Inner models (L, core models)
- Model-theoretic stability theory, Morley's theorem, o-minimality
- Ultraproducts (model-theoretic)
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

- Artin–Wedderburn theorem (semisimple Artinian ring ≅ product of matrix rings over division rings)
- Brauer theory / modular representation theory (blocks, defect groups, decomposition matrices)
- Number of irreducibles = number of conjugacy classes
- Character tables as a computational framework
- Burnside's theorem via characters
- Clifford's theorem (restriction to normal subgroups decomposes into conjugate irreducibles)
- Mackey's irreducibility criterion
- Projective representations

---

## Noncommutative ring theory

Mathlib has simple modules and semisimple modules/rings (with Schur's lemma), Jacobson radical (as intersection of maximal ideals), Artinian ring theory (including "prime = maximal" and "reduced Artinian = product of fields"), Ore localization, and Jacobson rings. However, the fundamental structure theorem and several core tools are missing.

### Not recommended as default dependencies

- Wedderburn–Artin theorem (structure of semisimple Artinian rings)
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

Mathlib has Dedekind domains (with three equivalent characterizations), class groups, finiteness of class number for global fields, Dirichlet's unit theorem, class number formula, p-adic numbers with Hensel's lemma, completions of number fields at infinite places, and partial adele ring infrastructure. However, ramification theory and explicit computational tools are largely absent.

### Not recommended as default dependencies

- Hilbert's ramification theory (decomposition/inertia/higher ramification groups)
- Hasse–Arf theorem (upper numbering jumps at integers for abelian extensions)
- Different-discriminant theorem, conductor-discriminant formula
- Full adeles/ideles with product formula and strong approximation (partial port from Lean 3 still in progress)
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

Mathlib has modular forms and cusp forms (as extensions of slash-invariant forms), Eisenstein series for weight k and level Γ(N), the upper half-plane with SL(2,ℤ) action and fundamental domain, and the graded ring of modular forms. However, the computational and structural heart of the theory is absent.

### Not recommended as default dependencies

- q-expansions (Fourier expansions of modular forms)
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

Beyond the items listed above (Jordan curve, Brouwer, etc.), Mathlib has the fundamental groupoid and fundamental group, singular homology with homology of spheres, and basic covering space definitions. However, the broader computational apparatus of algebraic topology remains absent.

### Not recommended as default dependencies

- Higher homotopy groups (π_n for n ≥ 2)
- Singular cohomology
- CW complexes and cellular homology
- Mayer–Vietoris sequence
- Hurewicz theorem
- Eilenberg–Steenrod axioms
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

Mathlib has Haar measure (existence and uniqueness on locally compact Hausdorff groups), the Pontryagin dual (definition as continuous homomorphisms to the circle), locally convex spaces with seminorm-based topologies and Banach–Steinhaus, profinite spaces, and topological group completions. However, the main duality theorem is absent.

### Not recommended as default dependencies

- Pontryagin duality theorem (G ≅ G^∧∧ for locally compact abelian groups)
- Profinite completion functor for groups
- Peter–Weyl theorem (also listed under Lie groups)

---

## Linear algebra (advanced)

Mathlib has eigenvalues/eigenvectors, spectral decomposition for self-adjoint operators (finite-dimensional), bilinear and quadratic forms, Clifford algebras, determinants, Cayley–Hamilton, minimal polynomial, the structure theorem for finitely generated modules over PIDs (via Smith normal form), Jordan–Chevalley–Dunford decomposition, tensor products, exterior algebra/powers, and symmetric powers. However, several canonical form results and structural tools are absent.

### Not recommended as default dependencies

- Jordan normal form (Jordan–Chevalley–Dunford exists, but Jordan blocks do not)
- Rational canonical form
- Schur decomposition theorem (every matrix is unitarily triangularizable)
- Simultaneous diagonalization theorem (commuting diagonalizable matrices)
- Witt groups of quadratic forms
- Grassmannians as geometric objects
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

Mathlib has pre-games (`PGame`) following Conway, Conway induction, ordering and arithmetic on games, and surreal numbers as a linearly ordered commutative group with dyadic rationals embedded. However, surreal multiplication and the field structure are still incomplete, and classical game theory is absent.

### Not recommended as default dependencies

- Surreal numbers as a complete ordered field (multiplication not yet complete)
- Sprague–Grundy theorem (impartial games are equivalent to nimbers)
- Von Neumann's minimax theorem (two-player zero-sum games)
- Nash's existence theorem (Nash equilibria in finite games)
- Zermelo's theorem (determinacy of finite games of perfect information)

---

## Special functions

Mathlib has a thorough Gamma function formalization (Euler's integral, recurrence, reflection formula, Legendre duplication, Bohr–Mollerup uniqueness), Beta function, Riemann zeta function (with functional equation), and Bernstein polynomials. However, most other classical special functions are absent.

### Not recommended as default dependencies

- Bessel functions
- Spherical harmonics
- Elliptic functions and elliptic integrals
- Hypergeometric functions
- Airy functions, Whittaker functions
- Legendre's relation for elliptic integrals

---

## Real analysis (advanced)

Mathlib has monotone/dominated convergence, Fatou's lemma, bounded variation with a.e. differentiability, Egorov's theorem, Vitali and Besicovitch covering theorems, Lebesgue differentiation theorem (with density points), and Vitali convergence theorem. However, some classical function-space concepts are absent.

### Not recommended as default dependencies

- Banach–Zaretsky theorem (BV + maps null sets to null sets ↔ absolutely continuous)
- Lebesgue's fundamental theorem of calculus for absolutely continuous functions
- Lusin's theorem (measurable functions are continuous on large sets)
- Vitali–Carathéodory theorem (approximation of integrable functions by semicontinuous functions)
- Stirling's formula

---

## Coding theory and information theory

Mathlib has Hamming distance and Hamming norm with full metric space structure. However, essentially no coding theory or information theory exists beyond this.

### Not recommended as default dependencies

- Singleton bound and MDS codes
- Hamming bound (sphere-packing bound)
- Gilbert–Varshamov bound
- MacWilliams identity (weight enumerator duality)
- Shannon's noisy channel coding theorem
- Shannon's source coding theorem
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

Mathlib has `SimpleGraph` with extensive basic theory (connectivity, subgraphs, matchings, coloring, Hamiltonian paths, Turán's theorem, regularity lemma). However, planarity, graph minors, and advanced structural theory are absent.

### Not recommended as default dependencies

- Kuratowski's theorem (planar iff no K₅ or K₃,₃ subdivision)
- Wagner's theorem (planar iff no K₅ or K₃,₃ minor)
- Robertson–Seymour graph minor theorem (WQO under minors)
- Max-flow min-cut theorem (Ford–Fulkerson)
- Menger's theorem (disjoint paths = min vertex cut)
- Brooks' theorem (χ(G) ≤ Δ(G) unless complete or odd cycle)
- Vizing's theorem (edge chromatic number is Δ or Δ+1)
- Tutte's theorem (characterization of graphs with perfect matchings)

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

The perfectoid spaces project (Buzzard–Commelin–Massot) was completed in Lean 3 but has not been ported to Lean 4 or Mathlib4. The Liquid Tensor Experiment (condensed mathematics) was also a standalone Lean 3 project. Neither is available in Mathlib4.

### Not recommended as default dependencies

- Scholze's tilting equivalence (perfectoid spaces over K ≃ perfectoid spaces over K♭)
- Almost purity theorem (Faltings/Scholze)
- Fontaine–Winterberger theorem (isomorphism of absolute Galois groups)
- Condensed sets and condensed abelian groups
- Clausen–Scholze main theorem on liquid modules

---

## p-adic Hodge theory and derived algebraic geometry

Mathlib has p-adic numbers and basic valuation theory, but the deeper p-adic and derived frameworks are entirely absent.

### Not recommended as default dependencies

- Fontaine's period rings (B_dR, B_cris, B_st)
- p-adic comparison theorems
- Derived schemes and derived stacks
- ∞-categories (no framework exists in Mathlib)
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

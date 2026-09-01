# leanq

`leanq` is the elaborator-backed half of this repository. It asks Lean about the environment that a project actually builds instead of inferring theorem structure from source text.

Use it when the answer depends on declaration identity, declaration kind, `Prop`-valuedness, direct or transitive dependencies, reverse dependencies, axiom closure, `sorryAx`, or the declaration graph after elaboration. Use `aiq-lean source ...` for structural source audits that do not need elaboration.

## Project discovery

Run commands from a Lean project or pass `--project /path/to/project` before the subcommand. `leanq` reads the Lake project, discovers local `lean_lib` declarations, source roots, build artifacts, and module ownership.

```bash
leanq libs
leanq --lib MyLibrary libs
```

`--lib` narrows commands that operate on one library. Glob-built libraries are handled by their live source modules rather than assuming that the library root imports every file.

## Declaration index

Build the local declaration index once and query it repeatedly:

```bash
leanq --lib MyLibrary index
leanq --lib MyLibrary stats
leanq --lib MyLibrary query --kind theorem
leanq --lib MyLibrary query --name SinTheta --json
leanq --lib MyLibrary query --sorried
leanq --lib MyLibrary query --uses MyLibrary.someDefinition
```

The index is derived from Lean's environment. Multi-line theorem statements, notation, generated proof terms, and namespace layout do not need to be reconstructed with regular expressions.

The default cache lives under `.leanq/`. Use `--refresh` on query-like commands when the build has changed and a fresh export is required.

## Dependencies and axiom closure

```bash
leanq --lib MyLibrary deps MyLibrary.mainTheorem
leanq --lib MyLibrary deps MyLibrary.mainTheorem --transitive
leanq --lib MyLibrary deps MyLibrary.mainTheorem --local
leanq --lib MyLibrary rdeps MyLibrary.helper
leanq --lib MyLibrary axioms MyLibrary.mainTheorem
```

`deps --local` answers the extraction question: which declarations from the selected local library would have to move with this declaration? `rdeps` answers the consumer question: which declarations depend on this result?

## Statement closures

A proof graph answers what a theorem *uses*.  A source-to-Lean audit needs a
different question answered: what does the theorem's statement *mean*?  Compact
predicates hide hypotheses behind a name, and the hand-written dictionary that
explains each name is exactly what an auditor cannot take on trust.

```bash
leanq statement MyLibrary.mainTheorem
leanq statement MyLibrary.mainTheorem --hide-boundary --docstrings
leanq statement MyLibrary.mainTheorem MyLibrary.otherTheorem --json
leanq statement --lib MyLibrary --all          # whole-library sidecar; slow
```

The exporter's `statement` mode walks the constants a statement means: a
definition is unfolded through its body, a structure or class through its
constructor fields, and a theorem is a leaf (its value is a proof, not part of
what it says).  Constants in a boundary library -- Mathlib and the Lean core by
default, `--boundary` overrides the prefix list -- are emitted with their type
and docstring but never unfolded.  Every record carries:

- `signature`, the `#check`-style form a reader compares with the paper;
- `type`, the elaborated type as a term, and `typeExprHash`, the elaborator's
  structural hash of it, which ignores binder names and notation;
- `typeTextSha256`, a hash of the printed type, which moves whenever what a
  reader sees moves;
- `typeDeps` and `bodyDeps`, so each closure edge is labelled `type` or `body`;
- `docstring`, and for structures the projection type of every field.

The sidecar lives next to the ordinary index under `.leanq/` and is keyed by its
seeds, so several reviewed sets coexist.  It is rebuilt when the imported sources,
the seeds, or the boundary change.  Pretty-printing is the expensive part, which is
why this is a separate optional artifact rather than a column of the index.

Every index mode now also records `typeDeps` beside the merged `deps`, so a graph
consumer can tell an edge that shapes a statement from one that only supports a
proof (`Decl.dependency_role`).

## Reusable semantic graph

For a holistic formalization, export one broad semantic index and derive multiple views from that file:

```bash
leanq graph-index \
    --out build/leanq/project-semantic-graph.json
```

The graph uses dependency-to-consumer edge direction. The saved artifact contains declaration metadata and direct elaborated dependencies. Downstream slicing and HTML generation are Lean-free.

A target-specific slice is then cheap:

```bash
leanq graph-slice \
    build/leanq/project-semantic-graph.json \
    MyLibrary.mainTheorem \
    --out build/leanq/main-theorem.json
```

Use `--transitive-reduction` when a reachability-preserving reduced edge set is useful for presentation. The unreduced direct graph remains available in the same payload.

## Census-aware dependency union

`graph-headlines` can combine a semantic index with one or more census files. The default `dependencies` view keeps the exact union of dependency closures for the selected census claims.

```bash
leanq graph-headlines \
    build/leanq/project-semantic-graph.json \
    --census dev/paper-a-source-census.json \
    --census dev/paper-b-source-census.json \
    --importance headline \
    --out build/leanq/headline-dependencies.json
```

Reviewed `semantic_review.canonical_declarations` are treated as the primary realization of a claim. `supporting_declarations` remain labeled separately. Older census rows without explicit realization metadata fall back to `lean_declarations`.

`--include-supporting` promotes reviewed supporting realizations to graph seeds as well. By default they are metadata on the canonical graph rather than extra roots.

## Compact target-consumption view

The `consumption` view answers a different question: which census claims actually lie on a selected target's dependency closure, and through which named integration seams do they flow?

Integration seams are configured, not hard-coded:

```bash
leanq graph-headlines \
    build/leanq/project-semantic-graph.json \
    --view consumption \
    --target Application.mainTheorem \
    --census dev/foundation-source-census.json \
    --census dev/application-source-census.json \
    --landmark 'Spectral integration=SpectralLibrary' \
    --landmark 'Configuration bridge=ConfigLibrary' \
    --out build/leanq/headline-consumption.json
```

Each `--landmark LABEL=LIBRARY` names a public library seam and is repeatable in route order. The analysis records the exact witness path used to reach each seam.

The downstream census family is inferred from claims whose declarations live in the selected target's library. If package layout does not encode that relationship, specify it explicitly:

```bash
--terminal-family 'Application paper'
```

The resulting JSON contains a dynamic `stagePlan`; the HTML viewer uses that data rather than assuming a fixed number or identity of paper stages.

## Whole-project HTML explorer

A semantic index can be annotated directly with census landmarks and rendered without creating an intermediate headline JSON:

```bash
leanq graph-html \
    build/leanq/project-semantic-graph.json \
    --census dev/paper-a-source-census.json \
    --census dev/paper-b-source-census.json \
    --out build/leanq/project-semantic-graph.html
```

The resulting HTML is self-contained. It can be opened from disk and includes search, grouping, declaration metadata, headline realization metadata, and exact dependency edges.

Optional external-boundary modes control whether Mathlib/Lean dependencies are embedded:

```bash
--boundary none
--boundary headline
--boundary project
```

`project` can be very large. `headline` is useful when the report only needs the external dependencies touched by selected census claims.

## Foundation grouping

If a project has a mathematical-foundation taxonomy that cuts across package boundaries, pass a CSV with `module` and `theory_id` columns:

```bash
leanq graph-html \
    build/leanq/project-semantic-graph.json \
    --census dev/paper-source-census.json \
    --foundations dev/formalization_module_theories.csv \
    --out build/leanq/project.html
```

If `formalization_basic_theories.csv` is next to the map and contains `theory_id` and `theory`, those labels are used automatically.

## Comparison publisher

`graph-compare-html` publishes package-first comparisons of census ancestry from the same semantic index:

```bash
leanq graph-compare-html \
    build/leanq/project-semantic-graph.json \
    --census dev/paper-a-source-census.json \
    --census dev/paper-b-source-census.json \
    --family 'Reference paper' \
    --family 'Application paper' \
    --overlay-family 'Intermediate paper' \
    --out build/leanq/comparison.html
```

Family names come from census metadata. If no initial families are specified, the publisher chooses from the loaded families rather than relying on project-specific names.

## Direct graph command

For a one-off target graph, `leanq graph` combines indexing, slicing, and optional HTML generation:

```bash
leanq graph MyLibrary.mainTheorem \
    --out build/leanq/main.json \
    --html build/leanq/main.html
```

The staged `graph-index` workflow is preferable when several agents or reports will reuse the same project graph because only the first stage needs Lean.

## Promotion analysis

`promotions` identifies declarations in tagged module segments that are actually consumed by declarations outside those segments:

```bash
leanq --lib MyLibrary promotions \
    --root MyLibrary \
    --tag Experimental \
    --tag MathAhead
```

A direct use crossing from an untagged module into tagged code is reported as a boundary crossing; tagged declarations required transitively by those boundary declarations are supporting dependencies. This distinguishes true semantic use from a file merely being imported by an aggregate module.

`--consumer-prefix` limits the consumers being considered, and `--kind`, `--boundary-only`, and `--names` control presentation.

## Generated equation attribution

Lean can attach generated equation lemmas such as `X.eq_1` to the module that first forces generation rather than the module that declares `X`. In a multi-library graph that can create a false package-level direction even though the declaration edge itself is real.

`leanq` reattributes those generated equation declarations to the library and module of their parent declaration before package-level presentation. The underlying dependency remains unchanged.

## JSON as an interchange layer

The graph commands are intentionally staged around JSON artifacts:

```text
Lean/Lake build
    -> graph-index
    -> semantic-index JSON
        -> graph-slice
        -> graph-headlines
        -> graph-html
        -> graph-compare-html
```

This lets agents iterate on census selection, graph projections, HTML, labels, and report layout without rebuilding Lean.

## Python API

The main modules are also importable:

- `leanq.project` — Lake project/library discovery;
- `leanq.index` — declaration export and cached index operations;
- `leanq.graph` — semantic graph construction and slicing;
- `leanq.headlines` — census-aware graph analysis;
- `leanq.presentation` — curated presentation specs;
- `leanq.promotion` — experimental-to-production dependency analysis;
- `leanq.viewer` — standalone HTML rendering.

For tests that should not invoke Lean, construct `Decl` rows and feed them to the pure graph/headline functions. Compiler invocation belongs at the index/export boundary.

## Limits

A source scanner and an elaborated environment answer different questions. `leanq` should be preferred whenever notation expansion, generated declarations, theorem constants, imported declarations, or proof-term dependencies affect the answer. It still depends on a buildable Lean environment for fresh indexing; saved semantic-index JSON is the seam for Lean-free downstream tests and report generation.

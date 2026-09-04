# Architecture

## Boundaries

`aiq_lean_tools` is organized around what kind of evidence a tool can actually provide.

### Census and review documents

`census.py`, `coverage.py`, `semantic_review.py`, and `literature.py` own maintained tracking documents, validation, mutation, summaries, and generated views. `coverage.py` keeps the hard formalization denominator separate from the finer source-fidelity atom inventory and validates their cross-links without hard-coding a paper-specific terminality policy. Validation is definition-driven: a census may define its own status, verification, completion, importance, gap, blocker, and semantic-alignment vocabularies.

`source_model.py`, `correspondence.py`, and `source_pins.py` own the source-to-Lean correspondence model: which checked-in document a locator points at, what passage it resolves to, how that passage's mathematics is parsed for rendering, what relation a clause claims, and optional structured Lean-side targets into an elaborated binder or result. Source excerpts are checked against source text; Lean targets are resolved against `leanq` statement records. A private source document may be configured out of tree and is never serialized. `docs/source-model.md` is the reference.

`alignment.py` builds a reviewer-oriented packet from curated `semantic_review` material embedded in census rows. It can add compiler evidence without mixing compiler success with source-equivalence judgments. With a `leanq` statement sidecar it also shows each canonical declaration's elaborated signature, its structured top-level binders/result, and statement closure. Clause targets are resolved against that structure so selecting a correspondence can highlight both the source passage and the exact Lean binder/result. `statement_pins.py` records the elaborated-type hashes a review accepted and reports declaration-level drift; `correspondence.py` reports localized target drift. `alignment check` runs both against fresh elaborator evidence.

### Lean execution seam

`lean_backend.py` is the shared Lean-process boundary for census/alignment probes, exact signature preflights, foundation probes, and staging compilation.

- `SubprocessLeanBackend` runs generated probe modules through `lake env lean`.
- `MockLeanBackend` makes those workflows deterministic in unit tests.
- `LeanBackend` is a protocol, so a remote executor or cached runner can be added without changing census code.

### Elaborated-environment introspection

`leanq` is shipped intact as a sibling package. Its exporter is Lean code bundled in `leanq/lean/decl_index.lean`; its Python side caches and queries the elaborated declaration environment. This is the semantic authority for declaration categories, axiom closure, dependencies, reverse dependencies, and project proof graphs.

### Python-only structural audits

`lean_source.py`, `audits.py`, `source_candidates.py`, `symbol_census.py`, `checklist.py`, `hygiene.py`, `aggregates.py`, `import_policy.py`, `namespace_policy.py`, `module_coverage.py`, `roadmap.py`, `module_export.py`, and `ratchet.py` operate on source/build-tree structure. They do not infer theorem meaning.

This layer covers:

- source declarations, modules, imports, admissions, and docstrings;
- policy-driven root coverage for staged module subtrees, including reasoned named/subtree exclusions;
- structural roadmap delivery candidates based on declaration-name matches, with ambiguity surfaced rather than hidden;
- proof-length/scaffolding census;
- normalized theorem-statement and definition-body duplicate candidates, with forwarding aliases distinguished;
- dead-definition, naming, long-statement/body, and definitional-escape review candidates;
- per-file review profiles and rename-preserving review checklists;
- raw-versus-code symbol-use censuses for migration gates;
- admission closure;
- duplicate qualified names and private names shadowing imported public names;
- declaration snapshots and drift;
- recursive aggregate-module generation;
- merge-conflict markers;
- orphan Lake build products after file moves/deletions;
- configurable import-layer, namespace-placement, and regex-count ratchet policies.

`gates.py` handles a different structural problem: it discovers repository `check_*.py` scripts, determines which accept the conventional `--check` strictness switch without executing them, and applies explicit configuration for slow, unavailable, advisory, or stronger-than-regression modes. This keeps a repository's named checks local while making the runner reusable.

A suite may also *declare* gates as argv commands. Once the generic engines live in this package, most of a repository's gates are installed commands plus a project policy file, and filename discovery cannot see those. Without declared gates a project has to keep writing wrapper scripts purely so the runner can find them, which is the duplication the extraction exists to remove.

### Campaign tracking and reproducible evidence

`foundations.py` models recursive human-tracked foundation campaigns while keeping those edges distinct from elaborated Lean dependencies. `staging.py` models staging-root state with optional compiler checks. `module_plan.py` validates ordered module-topic partitions and derives source-import prerequisites and dependency-closed submission rungs from project-supplied editorial seeds. `signatures.py` performs exact Lean interface preflights across module pairs. `certification.py` builds reproducible evidence bundles from declarative command/input plans; it records evidence but does not define scientific completion.

### Holistic workspace

`workspace.py` discovers censuses, semantic reviews, result inventories, linked source-fidelity atoms, literature inventories, and foundation maps and produces an aggregate view across source papers. It intentionally aggregates evidence rather than inventing a single completion score. Source implementation status, compiler verification, semantic review, and completion certification remain distinguishable axes.

### Build diagnostics

`lake_report.py` is the reusable Lake diagnostic parser/reporter developed in the source formalization. `warning_fixer.py` applies only the mechanical Lean warning rewrites it knows how to verify syntactically; it is dry-run by default.

## Project source scope

Every structural audit needs the same answer to one question: which Lean files in
this checkout are the project's own source? A formalization checkout usually also
carries vendored donors, retired trees, reference checkouts of other repositories,
and submitted copies of itself. Scanning those is not merely slow -- duplicate-name,
docstring, namespace, and import audits then report them as findings, so the answer
is wrong rather than late.

`SourceScope` reads that answer once from `source_scope` in `formalization.yaml`
(`roots`, `exclude_dirs`) and `lean_source.scan_lean_project`, `namespace_policy`,
and the candidate audits in `source_candidates` honour it. An explicit argument
still wins, so external trees such as an upstream roadmap checkout are scanned on
their own terms.

A root may also carry a `module_root`, because a Lake library with `srcDir` names
its modules relative to that directory rather than to the checkout. Deriving module
names from the checkout instead renames every module in such a library, after which
none of its imports resolve against the index and every import-derived rule quietly
stops applying to it.

## Accepted-finding baselines

`baseline.py` backs the `--baseline` / `--write-baseline` options on the structural
checks whose count a live repository cannot hold at zero. A baseline names the
findings a project has accepted and why. It is deliberately not a count threshold:
a threshold accepts any finding once the number is high enough, so retiring one
accepted case silently makes room for a new defect. A baseline entry that no longer
matches any finding fails the check for the same reason -- left in place it
pre-accepts whatever next takes that name.

## Data versus policy

Architecture-specific layer names and namespace rules belong in YAML/JSON policy files. Source-paper facts belong in census/review JSON. Python implements mechanisms and validation invariants.

This keeps a new formalization from inheriting Davis--Kahan-specific paths such as `ForTauCeti`, `Spectra`, or a fixed set of paper libraries.

## Structural grounding, import closure, and provenance

Three additional source-level layers cover recurring formalization maintenance tasks without claiming compiler semantics:

- `grounding.py` reads YAML/JSON contracts for required files and declarations and optional bans on admissions, axioms, or project-defined source regexes. It replaces named-paper grounding scripts with data.
- `import_graph.py` computes conservative local source-import closures. Its resolver deliberately accepts suffix matches so Lake libraries using `srcDir` can be inspected from a superproject checkout. The result is an availability upper bound; declaration proof dependence remains `leanq` territory.
- `provenance.py` inventories recorded `## Provenance` sections in Lean module documentation. Marker regexes are caller configuration, so donor names and source projects are not hard-coded into the package.

`module_migration.py` contains the mechanical Lean module/public-import conversion used during the source project migration. It supports dry/check and explicit write workflows.

## Repository history

`history.py` extracts auditable Git commit metadata, co-author trailers, file churn, and Lean-only churn with no model-specific taxonomy. It is intended as a stable substrate for formalization provenance and effort accounting. Historical token ledgers, model pricing, extrapolation, and manuscript resource valuation remain downstream analyses because their assumptions change independently of the formalization tooling.

# Architecture

## Boundaries

`aiq_lean_tools` is organized around what kind of evidence a tool can actually provide.

### Census and review documents

`census.py`, `coverage.py`, and `semantic_review.py` own JSON validation, mutation, summaries, Markdown rendering, and standalone HTML rendering. `coverage.py` keeps the hard formalization denominator separate from the finer source-fidelity atom inventory and validates their cross-links without hard-coding a paper-specific terminality policy. Validation is definition-driven: a census may define its own status, verification, completion, importance, gap, blocker, and semantic-alignment vocabularies.

`alignment.py` builds a reviewer-oriented packet from curated `semantic_review` material embedded in census rows. It can add compiler evidence without mixing compiler success with source-equivalence judgments.

### Lean execution seam

`lean_backend.py` is the only external-process boundary used by the new census/alignment machinery.

- `SubprocessLeanBackend` runs generated probe modules through `lake env lean`.
- `MockLeanBackend` makes those workflows deterministic in unit tests.
- `LeanBackend` is a protocol, so a remote executor or cached runner can be added without changing census code.

### Elaborated-environment introspection

`leanq` is shipped intact as a sibling package. Its exporter is Lean code bundled in `leanq/lean/decl_index.lean`; its Python side caches and queries the elaborated declaration environment. This is the semantic authority for declaration categories, axiom closure, dependencies, reverse dependencies, and project proof graphs.

### Python-only structural audits

`lean_source.py`, `audits.py`, `source_candidates.py`, `symbol_census.py`, `checklist.py`, `hygiene.py`, `aggregates.py`, `import_policy.py`, `namespace_policy.py`, `module_coverage.py`, `roadmap.py`, and `ratchet.py` operate on source/build-tree structure. They do not infer theorem meaning.

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

### Holistic workspace

`workspace.py` discovers censuses, semantic reviews, result inventories, and linked source-fidelity atom inventories and produces an aggregate view across source papers. It intentionally aggregates evidence rather than inventing a single completion score. Source implementation status, compiler verification, semantic review, and completion certification remain distinguishable axes.

### Build diagnostics

`lake_report.py` is the reusable Lake diagnostic parser/reporter developed in the source formalization. `warning_fixer.py` applies only the mechanical Lean warning rewrites it knows how to verify syntactically; it is dry-run by default.

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

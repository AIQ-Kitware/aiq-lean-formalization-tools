# Agent guide

This repository is a reusable tools package. Keep source-paper facts and project-specific completion policy out of Python modules; put them in census/review documents, formalization metadata, or explicit YAML/JSON policy files.

Use the evidence layer that matches the question:

- `leanq` for elaborated declaration identity, axioms, dependencies, reverse dependencies, and semantic project graphs.
- `aiq-lean census` / `review` / `alignment` for source ledgers and semantic-review artifacts.
- `aiq-lean source` for structural candidates, staging/export/module-plan policy, and repository hygiene. Static duplicate, dead-definition, naming, and size scans are review prioritizers, not theorem-equivalence claims.
- `aiq-lean signatures`, `foundations`, `literature`, and `certify` for exact interface preflights, human campaign tracking, source-literature ledgers, and reproducible evidence bundles. Keep scientific completion criteria in the consuming project.
- `LeanBackend` for compiler probes. Tests should normally use `MockLeanBackend` rather than requiring Lean.

Before changing a public workflow, run `pytest`. For a packaging change, also build a wheel and inspect its contents so HTML assets, schemas, and `leanq/lean/decl_index.lean` remain included.

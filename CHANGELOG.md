# Changelog

## 0.1.0 first-consumer pass

Changes driven by migrating the `aiq-dkps-formalization` repository onto the package.

- Parse Lean 4 module-system `public` / `private` / `meta` import modifiers. They
  were silently unparsed, which dropped 902 real import edges in the first
  repository migrated and made every import-derived view -- layer policy,
  admission closure, module plans, coverage -- report a cleaner architecture than
  the source has.
- Add `source_scope` (`roots`, `exclude_dirs`) in `formalization.yaml`, honoured by
  `scan_lean_project` and the candidate audits. Without it a checkout carrying
  vendored donors, retired trees, or submitted copies of itself reports them as
  findings; the first repository migrated produced 10,781 phantom duplicate names.
- Honour Lake `srcDir` libraries: a `source_scope` root may carry a `module_root`,
  without which every module in such a library is renamed and none of its imports
  resolve against the index. The reverse direction matters too -- `source
  orphan-artifacts` recovers a build product's source path through the same
  mapping, and derives which top-level module names are the project's rather than
  a dependency's.
- Inventory anonymous `instance : C α` declarations, which no name-keyed scan could
  see, and keep them out of every name-keyed view.
- Add accepted-finding baselines (`--baseline`, `--write-baseline`) to
  `source duplicates`, `source docstrings`, and `source private-shadows`, with stale
  entries reported as failures. A baseline document may carry other top-level keys,
  which are preserved when it is rewritten.
- Add `--prefix` / `--exclude-prefix` module scoping to the same three checks. A
  conformance or challenge library restates library statements on purpose, so a
  repository-wide duplicate-name check otherwise reports its whole point as a defect.
- Skip attributes, `--` lines, and the other commands Lean allows between a docstring
  and its declaration when deciding whether a declaration is documented. Stopping at
  the first non-blank line called 237 documented declarations undocumented in the
  first repository migrated.
- Add `--check` to every Markdown/LaTeX render command (`census`, `review`,
  `coverage`, `literature`, `foundations`, `alignment`, `source module-plan`).
  A generated file committed to Git and never verified goes stale silently and
  then reads as a maintained document.
- Do not import a skipped subtree's sibling root module into a generated
  aggregate: `Experimental/` skipped but `Experimental.lean` imported puts the
  whole subtree back in the aggregate, and it still compiles.
- Honour the project source scope when discovering workspace ledgers, so a reference
  checkout's example census documents do not join this workspace's totals.
- Let a gate suite declare gates as argv commands, not only discover `check_*.py`
  scripts, so a project whose gates are package commands plus policy files does not
  have to keep wrapper scripts alive for the runner to find.
- Add `deny` to namespace rules, for "nothing here may declare into that namespace"
  constraints that have no allow-list form.
- Rewrite `strip_comments` as a token scanner: byte-identical output on 5,785 real
  Lean files, and about six times faster over a whole tree. Cache the index name
  tables, which were rebuilt on every declaration lookup.

## 0.1.0

- Extract `leanq` 0.3-era semantic query and graph tooling into a standalone distribution.
- Add heterogeneous source-census and semantic-review document APIs and CLIs.
- Add standalone census, review, and holistic workspace HTML viewers.
- Add mockable Lean compiler probes and curated semantic-alignment packet generation.
- Add proof-length, admission, docstring, duplicate-name, private-shadow, declaration-drift, conflict-marker, orphan-artifact, and aggregate-module source tooling.
- Generalize the hostile-review scanner/profile/checklist into candidate audits, per-file profiles, and rename-preserving generated checklists.
- Generalize gate-suite discovery, source-count ratchets, and raw-versus-code symbol migration censuses.
- Generalize import-layer and namespace-placement gates into YAML/JSON policies.
- Package the Lake build diagnostic reporter and conservative Lean warning fixer.

## 0.1.0 extraction follow-up

- Restored strict embedded headline semantic-review validation, derived review variants, and human-written Lean header rendering.
- Added generic result/source-atom coverage inventories, cross-link validation, stable-ID patching, Markdown/HTML rendering, and workspace aggregation.
- Added `HISTORY.md` with subsystem-level DKPS provenance and cleanup/migration instructions.
- Added reproducible `tool.xcookie` project metadata while preserving the independently shipped `leanq` package.
- Added policy-driven structural grounding gates for required files/declarations and placeholder restrictions.
- Added conservative source-level import closure comparison with nested `srcDir` suffix resolution and standalone HTML output.
- Added recorded `## Provenance` block inventories with configurable markers.
- Added mechanical Lean module/public-import migration with check and write modes.
- Added Git history/co-author/Lean-churn summaries and a standalone history HTML report.
- Added generic roadmap-delivery name comparison with library filtering, deterministic preference ordering, ambiguity reporting, and an explicit semantic-equivalence warning.
- Added policy-driven Lean module-scope coverage so a root build cannot stand in for files the root never imports; named/subtree exclusions require recorded reasons.

## 0.1.0 final extraction pass

- Generalize exact Lean declaration-signature preflight from the Palomar comparator gate, including raw universe-slot and fully explicit type comparison through the shared `LeanBackend` seam.
- Generalize recursive foundation-campaign maps with source/declaration validation, cycle checks, configurable proof-escape scans, optional Lean probes, Markdown, and standalone HTML.
- Generalize experimental/staging-root registries with configurable states and optional per-module and aggregate compilation checks.
- Generalize deterministic staging-to-upstream Lean module export with cluster manifests, target overrides, import firewalls, drift checking, and explicit write mode; retain compatibility with the historical Tau Ceti manifest shape.
- Generalize reproducible certification bundles with declared input snapshots, Git/submodule state, stable source-tree hashing, tool-version probes, complete command logs, checksums, and optional ZIP archives.
- Generalize the distilled-literature inventory into a validated/editable manifest with Markdown, LaTeX, and standalone HTML views.
- Extend holistic workspace discovery and reports with literature inventories and recursive foundation maps.
- Add starter policy files for the final extracted subsystems and expand `HISTORY.md` with their DKPS correspondence and cleanup path.
- Generalize ordered Tau Ceti roadmap-topic consistency and submission-ladder closure as `source module-plan`, leaving topic/rung editorial choices in project policy.

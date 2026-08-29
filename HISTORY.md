# Extraction history and DKPS cleanup map

This repository is the reusable home for tooling that originated in the
`aiq-dkps-formalization` repository during the Davis--Kahan, Yu--Wang--Samworth,
and DKPS formalization work.  This file records the correspondence at the level
needed to remove duplicate maintenance from the DKPS repository.

The rule for cleanup is:

1. reusable mechanism lives here;
2. paper/library names, theorem selections, policy thresholds, source citations,
   and semantic judgments stay in the DKPS repository as data or thin wrappers;
3. Lean-semantic questions use `leanq` or a compiler-backed `LeanBackend` probe;
4. Python source scans remain structural audits and must not be promoted into
   semantic claims.

`docs/source-tool-inventory.md` is the path-by-path ledger.  This document groups
those paths by subsystem and records how an agent should migrate the DKPS tree.

## 1. `leanq`: elaborated Lean introspection

### DKPS origin

- `tools/leanq/src/leanq/**`
- `tools/leanq/src/leanq/lean/decl_index.lean`
- `tools/leanq/src/leanq/assets/*.html`
- `tools/leanq/tests/**`
- parts of `tools/lean_dep_trace/**`
- manuscript graph builders under `papers/formalization_draft2/scripts/`

### Reusable package

The code is installed as the top-level `leanq` package and `leanq` command.
The exporter builds an elaborated declaration index; the Python layer caches
and queries it; graph commands construct declaration dependencies, reverse
usage, slices, headline-consumption views, comparisons, architecture views, and
standalone HTML.

The extraction intentionally changed one DKPS-specific behavior.  The old
headline-consumption view encoded the YWS, Acharyya, and Quench stages in Python
and JavaScript.  The reusable version takes ordered `LABEL=LIBRARY` landmarks
and an optional terminal census family.  The old presentation can therefore be
reconstructed by DKPS configuration without keeping project names in `leanq`.

`tools/lean_dep_trace` is superseded for semantic dependency analysis.  Its
project-specific milestone list remains project data if DKPS still wants that
particular presentation.

### DKPS cleanup

- Remove the vendored `tools/leanq` package after DKPS installs this package.
- Replace `python tools/leanq/...` invocations with the installed `leanq` CLI.
- Replace `tools/lean_dep_trace/trace_deps.py` semantic dependency uses with
  `leanq graph-index`, `graph-slice`, `deps`, `rdeps`, or the graph viewers.
- Keep milestone/root selections as DKPS-owned JSON/TOML/YAML or shell command
  arguments, not as a fork of the graph engine.
- Delete copied HTML/JavaScript viewers from DKPS after all report producers use
  the installed package assets.

## 2. Source census documents

### DKPS origin

- `dev/davis-kahan-1970-full-source-census.json`
- `dev/yu-wang-samworth-2015-full-source-census.json`
- `dev/acharyya-2024-full-source-census.json`
- `dev/acharyya-2025-full-source-census.json`
- `dev/helm-2025-full-source-census.json`
- `dev/quench-2026-full-source-census.json`
- `scripts/check_davis_kahan_1970_source_census.py`
- `scripts/check_yu_wang_samworth_source_census.py`
- `scripts/check_dkps_application_source_censuses.py`
- `scripts/probe_census_declarations.py`
- `scripts/render_davis_kahan_1970_source_census.py`
- `scripts/source_census_importance.py`
- the reusable portions of `scripts/upgrade_census_schema_v4.py`

### Reusable package

`aiq_lean_tools.census.CensusDocument` owns loading, validation, stable-row
mutation, source-location checks, static declaration checks, compiler probes,
probe-result application, Markdown rendering, and standalone HTML rendering.
The `aiq-lean census` command exposes those operations.

The package supports the three census families that actually arose in DKPS:

- source census;
- source/completion census with a completion-certification axis;
- application source/semantic-alignment census with source locators and an
  explicit semantic-alignment classification.

The package does not copy the census contents.  The source citations, row IDs,
status definitions, theorem names, notes, and adjudications remain DKPS data.

### Embedded semantic-review surface

The DK/YWS censuses also introduced a compact curated review contract on
headline rows.  The extraction keeps the strong validation from
`scripts/source_census_importance.py`:

- normalized setup/hypotheses/conclusions/scope;
- canonical declarations;
- supporting declarations;
- curated context declarations with mathematical roles;
- clause-by-clause source/Lean correspondence;
- derived `semantic_review_variants` with provenance.

Primary canonical/supporting declarations must remain anchored to the parent
census row.  Derived variants may cite declarations outside that list because
they are explicitly labelled derived review targets.

### DKPS cleanup

- Keep all `dev/*-full-source-census.json` files in DKPS.
- Replace paper-specific census validators with `aiq-lean census validate` or
  `aiq-lean workspace validate`.
- Replace declaration probing with `aiq-lean census probe`.
- Replace generic census Markdown/HTML rendering with `aiq-lean census render`
  and `aiq-lean census html`.
- Delete `scripts/source_census_importance.py` after DKPS validation is routed
  through this package.
- Keep a paper-specific wrapper only when it adds a paper-specific certification
  rule that cannot be represented as census data or a policy file.

## 3. Dedicated semantic-review documents

### DKPS origin

- `dev/acharyya-2024-result-semantic-review.json`
- `dev/acharyya-2025-result-semantic-review.json`
- `dev/helm-2025-result-semantic-review.json`
- `dev/quench-2026-result-semantic-review.json`
- `dev/dkps-application-result-semantic-review.md` as an older human report
- `scripts/check_dkps_result_semantic_reviews.py`
- generic rendering behavior from `scripts/render_semantic_alignment_review.py`

### Reusable package

`SemanticReviewDocument` and `aiq-lean review` manage the application-paper
clause-by-clause review format.  It remains separate from a source census
because the application papers developed an independent review ledger with its
own semantic classifications and clause structure.

### DKPS cleanup

- Keep the review JSON files in DKPS.
- Replace the shared checker/renderer logic with `aiq-lean review validate`,
  `review render`, and `review html`.
- Generated Markdown should be treated as an output, not a second maintained
  source of truth.

## 4. Compact cross-paper semantic-alignment packets

### DKPS origin

- `scripts/render_semantic_alignment_review.py`
- `scripts/source_census_importance.py`

### Reusable package

`aiq_lean_tools.alignment` and `aiq-lean alignment render` select census rows by
importance and combine the maintained semantic-review claim with Lean evidence.
The extracted renderer preserves the key review distinction established in the
DKPS script:

- the human-written Lean declaration header is the primary readable statement;
- relevant ambient `variable` binders are shown with it;
- compiler `#check`/`#print` output is verification evidence;
- only the curated semantic dictionary is expanded;
- derived review variants keep their provenance.

The source-header lookup is more conservative than the old DKPS path-ranking
heuristic.  Exact qualified names are preferred.  If a short name is ambiguous,
all source candidates are presented instead of selecting a Davis--Kahan or
TauCeti directory by convention.

### DKPS cleanup

- Replace the renderer with `aiq-lean alignment render`.
- Keep the DK/YWS importance assignments and curated review fields in the census
  JSON.
- Do not recreate project-path ranking logic in DKPS.  Fix ambiguous census
  declaration names or use qualified names.

## 5. Formalization denominator and source-fidelity atom inventory

### DKPS origin

- `dev/davis-kahan-1970-formalization-result-inventory.json`;
- `dev/davis-kahan-1970-source-atom-inventory.json`;
- the generic cross-linking/structural parts of `scripts/check_davis_kahan_1970_result_inventory.py`;
- the source-atom/result relationships referenced by `scripts/check_davis_kahan_1970_statement_map.py`.

### Reusable package

`aiq_lean_tools.coverage` preserves the distinction between a fine-grained
source-fidelity accounting surface and the smaller set of counted formalization
results. `aiq-lean coverage` validates both documents and their cross-links,
summarizes result states, checks cited Lean declaration names structurally,
renders Markdown/standalone HTML, and supports stable-ID patching of result rows
or source atoms.

The package deliberately does not define which source constructs count as a
formalization result or which disposition/verification/semantic states are
terminal. Those are paper-level completion policies.

### DKPS cleanup

- Keep both JSON inventories in DKPS.
- Replace generic ID/count/cross-link/declaration checks and generic rendering
  with `aiq-lean coverage`.
- Keep the Davis--Kahan 29-result selection rule, boundary reason taxonomy, hash
  pinning, false-printed-result policy, and terminal certification conditions in
  the DKPS certificate layer.
- Use the coverage API from the remaining certificate instead of reparsing these
  documents independently.

## 6. Whole-workspace tracking

### DKPS origin

The behavior was spread across the individual census/review checkers, the
formalization manifest, generated overview reports, and ad-hoc scripts that had
to rediscover all ledgers.

### Reusable package

`FormalizationWorkspace` discovers maintained census and semantic-review JSON,
reads `formalization.yaml`, validates documents together, aggregates status and
semantic distributions, counts cited declarations/reuse, optionally includes
source-audit measurements, and consumes an existing `leanq` semantic graph.

`aiq-lean workspace init/status/validate/html` is the agent-facing interface.

### DKPS cleanup

- Keep `formalization.yaml` and the source ledgers in DKPS.
- Route repository-wide validation/report generation through `workspace`.
- Remove duplicate document-discovery and aggregate-summary code from paper
  scripts once the workspace report covers it.

## 7. Mockable Lean process boundary

### DKPS origin

Several scripts directly created temporary Lean files and invoked `lake env lean`
or `lake env leanq`-style probes.  Tests consequently depended on the repository
and toolchain state.

### Reusable package

`aiq_lean_tools.lean_backend` defines the process seam.  Production uses
`SubprocessLeanBackend`; tests use `MockLeanBackend`.  Census probes and semantic
alignment use this interface instead of embedding subprocess policy.

### DKPS cleanup

- New DKPS Python tools should accept/use the package backend instead of creating
  another Lean subprocess helper.
- Tests of Python bookkeeping should use the mock backend.
- End-to-end DKPS gates may still run the real Lean backend.

## 8. Python-only Lean source index and audits

### DKPS origin

- `scripts/audit_scan.py`
- `scripts/audit_profile.py`
- `scripts/audit_checklist.py`
- `scripts/check_declaration_name_drift.py`
- `scripts/check_docstring_coverage.py`
- `scripts/check_duplicate_qualified_names.py`
- `scripts/check_private_shadows_public.py`
- `scripts/inventory_admission_closure.py`
- `scripts/proof_length_census.py`
- `scripts/closed_operator_census.py`
- the generic source-scanning parts of several readiness scripts

### Reusable package

`aiq_lean_tools.lean_source` builds the structural source index used by the audit
commands.  `aiq-lean source` exposes:

- `scan`;
- `admissions`;
- `proof-length`;
- `docstrings`;
- `duplicates`;
- `private-shadows`;
- `similar`;
- `large`;
- `dead`;
- `names`;
- `definitional-escapes`;
- `profile`;
- `symbol-census`;
- `checklist`;
- declaration `snapshot`/`drift`;
- roadmap suggested-name delivery comparison with donor-library filters and ambiguity reporting;
- module-scope root coverage with reasoned module/subtree exclusions;
- conflict-marker and orphan-artifact checks.

Duplicate/dead/name/similarity results are review candidates.  They are not
proof of semantic redundancy or deadness because Python source parsing does not
see the elaborated environment.

### DKPS cleanup

- Replace the scripts above with the matching `aiq-lean source` command.
- Keep only DKPS-specific threshold/config files where a check needs policy.
- Use `leanq` before deleting a declaration based on a structural candidate.
- The old audit checklist file can remain as review state; its generator should
  come from this package.
- Replace `scripts/check_experimental_coverage.py` with `aiq-lean source module-coverage`
  and a DKPS-owned policy naming `DavisKahan.Experimental` as the root/scope. Keep
  each broken-module or deliberately unaggregated subtree reason in that policy. Pair
  the structural reachability check with the existing real Lake build gate; source
  closure alone does not prove that reached modules elaborate.
- Replace `scripts/check_roadmap_delivered.py` with `aiq-lean source roadmap`; keep
  the TauCeti roadmap path, donor-library allowlist, preference order, and actual
  semantic completion judgments in DKPS configuration/review state. Name matches
  remain candidate evidence, not proof that a suggested signature was delivered.

## 9. Import and namespace architecture policy

### DKPS origin

- `scripts/check_dependency_layers.py`
- `scripts/check_namespace_policy.py`
- `scripts/check_spectra_namespace.py`
- reusable parts of `scripts/check_library_structure.py`

### Reusable package

`aiq-lean imports check` and `aiq-lean namespaces check` read YAML/JSON policies.
The mechanism is package code; allowed/forbidden layer edges, namespace roots,
and exceptions are repository data.

### DKPS cleanup

- Move hard-coded layer/namespace tables into DKPS policy files.
- Delete the old checker implementations after equivalent gate entries call the
  package commands.
- Keep named Spectra/TauCeti/DavisKahan constraints in DKPS policy, not in this
  package.

## 10. Source import closures and dependency presentation

### DKPS origin

- `papers/formalization_draft2/scripts/build_dependency_analysis.py`
- source-level portions of other manuscript dependency builders

### Reusable package

`SourceImportGraph` and `aiq-lean source import-closure` compute conservative
source import closures, overlaps, family counts, nested `srcDir` resolution, and
standalone HTML.  Module resolution uses a precomputed suffix index, avoiding the
old repeated full-tree scans.

This is a source-level upper bound.  Elaborated theorem dependency questions use
`leanq`.

### DKPS cleanup

- Replace the generic closure engine in the manuscript script with this command
  or API.
- Keep manuscript root sets, labels, ordering, and prose in the manuscript tree.
- Do not use source import closure as a replacement for theorem-dependency data.

## 11. Grounding policies

### DKPS origin

- `YuWangSamworth2015/scripts/verify_grounding.py`

### Reusable package

`aiq_lean_tools.grounding` and `aiq-lean source grounding` evaluate a YAML/JSON
policy that names required files/declarations and forbidden admissions/axioms or
source patterns.

### DKPS cleanup

- Convert the YWS hard-coded requirements into a DKPS-owned grounding policy.
- Replace the script with the package command in gates/CI.
- Reuse the same mechanism for later papers instead of creating another named
  `verify_grounding.py`.

## 12. Regression gates

### DKPS origin

- `scripts/run_gates.py`
- its discovery/skip/advisory/slow-check conventions

### Reusable package

`aiq_lean_tools.gates` and `aiq-lean gates list/run` discover and run checks with
optional policy describing slow, advisory, unavailable, and stronger-check
cases.

### DKPS cleanup

- Keep the DKPS gate-suite configuration and any genuinely project-local check
  commands.
- Delete the generic runner after CI and agent instructions call the installed
  runner.
- Do not move paper certification policy into this package merely because it is
  invoked by the runner.

## 13. Count ratchets

### DKPS origin

- `scripts/check_expose_ratchet.py`
- count-based constraints embedded in some readiness checks

### Reusable package

`aiq_lean_tools.ratchet` and `aiq-lean ratchet check` evaluate named regex/file
count rules from YAML.  Thresholds and include/exclude paths remain project data.

### DKPS cleanup

- Convert maintained count thresholds to a DKPS ratchet policy.
- Remove checker code whose only job is counting files or matches and comparing
  against a threshold.

## 14. Aggregate `All.lean` generation

### DKPS origin

- `scripts/generate_all_aggregates.py`

### Reusable package

`aiq_lean_tools.aggregates` and `aiq-lean source aggregates` recursively build or
check aggregate import modules.  Base directory, library name, skip directories,
header text, and preserved foreign imports are parameters.

### DKPS cleanup

- Replace the generator with the installed command.
- Keep DKPS aggregate layout choices as command/config inputs.
- Delete hard-coded `DavisKahan` defaults from the DKPS maintenance path.

## 15. Lake build diagnostics

### DKPS origin

- `scripts/lake_build_report.py`
- `scripts/tests/test_lake_build_report.py`

### Reusable package

The code is `aiq_lean_tools.lake_report` and the installed
`lake-build-report` command.  It classifies Lake/Lean diagnostics into a compact
report without assuming a Davis--Kahan module.

### DKPS cleanup

Replace direct references to the old script with the installed command, then
remove the duplicate script/tests.

## 16. Lean warning fixer

### DKPS origin

- `scripts/fix_lean_warnings.py`

### Reusable package

The code is `aiq_lean_tools.warning_fixer` and the installed `lean-warning-fix`
command.

### DKPS cleanup

Use the package command and remove the duplicate script after comparing behavior
on current warnings.

## 17. Module-system migration

### DKPS origin

- `dev/hilbert-space-operator-roadmap/module-system-conversion.py`

### Reusable package

`aiq_lean_tools.module_migration` and `aiq-lean source module-migrate` provide a
check/write migration helper without the roadmap's named modules.

### DKPS cleanup

- Use the package helper for future source-module migrations.
- Keep one-off roadmap decisions in DKPS documentation.
- Remove the old conversion program when no historical replay depends on it.

## 18. Provenance blocks and repository history

### DKPS origin

- `papers/formalization_draft2/scripts/build_provenance.py`
- Git/co-author/churn portions of
  `papers/formalization_draft2/scripts/accounting_lib.py`
- source-block provenance logic reused by manuscript builders

### Reusable package

`aiq_lean_tools.provenance` inventories source blocks and their Git provenance.
`aiq_lean_tools.history` plus `aiq-lean history` reports auditable commit,
co-author, and Lean churn information and can render standalone HTML.

Pricing assumptions, model-name normalization, token-ledger joins, missing-data
models, and regression analysis remain manuscript-study code.

### DKPS cleanup

- Replace generic Git extraction/churn code with this package.
- Keep empirical-study joins, prices, cutoffs, and statistical analysis in the
  manuscript directory.
- Avoid copying another Git log parser into a paper script.

## 19. Repository hygiene

### DKPS origin

- `scripts/check_conflict_markers.py`
- `scripts/check_stale_build_artifacts.py`
- `scripts/clean_orphan_build_artifacts.py`

### Reusable package

These are exposed under `aiq-lean source conflicts` and
`source orphan-artifacts --check/--fix`.

### DKPS cleanup

Route gates to the package commands and remove duplicate implementations.

## 20. Shell helpers deliberately retained as shell

### DKPS origin

- `scripts/setup-lake-cache.sh`
- `scripts/run_git_of_theseus.sh`

### Reusable package

Copies are retained under this repository's `scripts/`.  They orchestrate host
mounts/external tools rather than Python data models.

### DKPS cleanup

DKPS may call the installed/shared copy or keep a tiny wrapper if local paths are
part of its workflow.  Avoid independent behavior changes in two copies.

## 21. Tools that stay project-local

The extraction does not turn every DKPS script into a library API.  The
following contain paper-specific scientific or release policy and should remain
in DKPS, ideally rewritten as thin compositions of the reusable primitives:

- `scripts/audit_full_paper_sine_theta.py`;
- `scripts/certify_davis_kahan_1970.py`;
- Davis--Kahan result-inventory/statement-map/final-certification rules;
- hidden-foundations criteria and semantic roadmap completion rules beyond structural name matching;
- comparator signature/install/run policy;
- Palomar and TauCeti readiness/export/submission scripts;
- experimental-tree staging policy;
- distilled-literature-index and submission-prose policy;
- manuscript resource valuation, LLM token accounting, pricing, and statistical
  analysis;
- post-hoc prompt-analysis research tooling.

The cleanup target for these scripts is duplication removal, not deletion of the
paper-specific policy.  Their generic filesystem, census, graph, Git, source
scan, and Lean-query work should call this package.

## 22. Suggested DKPS migration sequence

Use this order so cleanup is reviewable and failures can be attributed to one
subsystem.

### Phase A: install and compare

Install this repository editable in the DKPS development environment and keep
all old scripts temporarily.  For each replacement, run old and new commands on
the same commit and compare exit status plus generated data.

### Phase B: switch read-only reports

Switch first:

- source census rendering/summary;
- semantic-review rendering;
- alignment packet rendering;
- source audit reports;
- import-closure HTML;
- history/provenance reports;
- `leanq` graph viewers.

These changes do not mutate DKPS source ledgers.

### Phase C: switch validation gates

Replace census, semantic-review, grounding, import, namespace, hygiene, ratchet,
and generic gate-runner checks.  Preserve DKPS policy data and thresholds.

### Phase D: switch mutation helpers

Move census patch/add/probe-write, aggregate generation, warning fixes, orphan
cleanup, and module migration only after read-only parity is established.

### Phase E: delete duplicate engines

Delete the DKPS implementations listed as generalized or retained in
`docs/source-tool-inventory.md`.  Thin wrappers may remain when they select
DKPS-specific inputs, but wrappers should contain no copied parser, renderer,
graph engine, schema validator, or source scanner.

### Phase F: simplify agent instructions

Update `AGENTS.md`, paper handoffs, and gate documentation to name the installed
commands.  An agent working in DKPS should discover one maintained implementation
for each generic operation.

## 23. Parity criteria before deleting a DKPS tool

A generalized source script is safe to remove when all of the following hold:

- the package command/API covers the behavior DKPS still relies on;
- project-specific constants have been moved to DKPS data/configuration or a
  thin wrapper;
- representative old/new outputs were compared on the same commit;
- the DKPS gate uses the package implementation;
- package tests cover the reusable behavior;
- no agent documentation points at the old implementation.

For Lean-semantic tools, parity also requires a real-toolchain smoke test in
DKPS.  Mock-backed package tests establish the Python contract but do not replace
an elaboration check in the source formalization repository.

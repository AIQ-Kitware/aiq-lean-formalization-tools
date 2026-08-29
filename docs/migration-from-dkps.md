# Migration map from the DK/YWS/DKPS repository

This package extracts mechanisms that can serve another formalization. Source-paper facts, campaign state, one-off submission gates, and repository-specific architecture decisions stay in the formalization repository.

## Directly retained

| Original tool | Reusable form |
| --- | --- |
| `tools/leanq/**` | `leanq` package and CLI, including graph index/slice/HTML viewers |
| `scripts/lake_build_report.py` | `lake-build-report` and `aiq_lean_tools.lake_report` |
| `scripts/fix_lean_warnings.py` | `lean-warning-fix` and `aiq_lean_tools.warning_fixer` |

## Generalized into document tooling

| Original family | Reusable form |
| --- | --- |
| `check_*_source_census.py` | `CensusDocument.validate`, `aiq-lean census validate`, `workspace validate` |
| `render_*_source_census.py` | `census render`, `census html` |
| `probe_census_declarations.py` | `CensusDocument.probe` with mockable `LeanBackend`; `census probe` |
| `check_dkps_result_semantic_reviews.py` | `SemanticReviewDocument.validate`; `review validate` |
| `render_semantic_alignment_review.py` | `alignment render`, optionally compiler-probed |
| `source_census_importance.py` | normalized importance ordering used by alignment packets and workspace summaries |
| `upgrade_census_schema_v4.py` | no one-shot migration baked in; current documents are read heterogeneously and edited with stable generic APIs |
| `certify_davis_kahan_1970.py` | reusable pieces split across census validation/probing, alignment packets, workspace reports, and `leanq`; the DK-specific certificate composition remains project-local |

## Generalized into source/build audits

| Original tool | Reusable form |
| --- | --- |
| `proof_length_census.py` | `aiq-lean source proof-length` |
| `inventory_admission_closure.py` | `aiq-lean source admissions` |
| `check_declaration_name_drift.py` | `source snapshot` + `source drift` |
| `check_duplicate_qualified_names.py` | `source duplicates` |
| `check_docstring_coverage.py` | `source docstrings` |
| `check_private_shadows_public.py` | `source private-shadows` |
| `check_conflict_markers.py` | `source conflicts` |
| `check_stale_build_artifacts.py`, `clean_orphan_build_artifacts.py` | `source orphan-artifacts [--fix]` |
| `generate_all_aggregates.py` | configurable `source aggregates` |
| `check_dependency_layers.py` | YAML/JSON-driven `imports check` |
| `check_namespace_policy.py` | YAML/JSON-driven `namespaces check` |
| `tools/lean_dep_trace/**` | superseded for maintained workflows by `leanq` direct dependency export and graph stages |
| `audit_scan.py` | `source similar`, `large`, `dead`, `names`, `definitional-escapes`, and `profile`; all are candidate generators, while semantic dependency questions go through `leanq` |
| `audit_profile.py` | `source profile`, composed from the same maintained candidate detectors rather than reimplementing them |
| `audit_checklist.py` | configurable `source checklist`, preserving checked marks across regeneration and Git-detected renames |
| `run_gates.py` | `gates list/run` plus optional `gate-suite.yaml` policy |
| `check_expose_ratchet.py` | generic regex/count `ratchet check` with YAML policy |
| `closed_operator_census.py` | generic `source symbol-census`, reporting raw and code-only counts |
| `setup-lake-cache.sh` | generalized auxiliary `scripts/setup-lake-cache.sh` |
| `run_git_of_theseus.sh` | auxiliary `scripts/run-git-of-theseus.sh` |

## Intentionally project-local

The following scripts encode a particular paper, extraction campaign, submission target, comparator, or repository policy. Their reusable primitives are present above, but their named policy is not installed globally:

- Davis--Kahan/YWS paper certification, result-inventory, statement-map, hidden-foundation, full-paper sine-theta, and audit-packet scripts;
- Tau Ceti, Palomar, Spectra, extraction-roadmap, and submission-readiness gates;
- comparator-signature checks tied to specific generated comparator files;
- experimental-tree status and coverage policy tied to the Davis--Kahan staging layout;
- distilled literature index and submission-prose checks, whose groups, note requirements, and prose markers are themselves project content rather than a stable cross-project schema;
- project-specific export/refresh scripts for Tau Ceti;
- DK/YWS/DKPS census contents and semantic judgments themselves.

A new repository can express its import and namespace architecture in policy files, then keep source-specific certification composition next to its own source ledgers.

## Aggregate replacement for `generate_all_aggregates.py`

The original generator encoded `DavisKahan`, fixed skip directories, a copyright header, and migration-era foreign re-exports. The reusable generator takes all of those as arguments. Existing cross-library imports can still be preserved and dangling re-exports are reported.

## Graph replacement for `lean_dep_trace`

`leanq graph-index` exports the elaborated project declaration graph once. `graph-slice`, `graph-headlines`, `graph-html`, project explorer, comparison viewer, and architecture viewer then operate on cached JSON without invoking Lean again. That staged graph design is the preferred replacement for the older standalone dependency tracer.

## Headline-consumption generalization

The original compact headline publisher encoded the YWS, Acharyya, and Quench integration stages directly in `leanq.headlines` and its JavaScript viewer. The extracted package replaces those fixed names with repeatable `--landmark LABEL=LIBRARY` stages and an optional `--terminal-family`. Existing projects can reproduce their prior lane structure through CLI configuration, while new projects can use arbitrary library seams and census families.

For a path-by-path accounting of the extraction snapshot, see [`source-tool-inventory.md`](source-tool-inventory.md).

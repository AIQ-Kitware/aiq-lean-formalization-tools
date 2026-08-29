# Source-tool disposition ledger

This ledger records the disposition of the reusable tooling found in the source formalization snapshot used for the extraction. "Project-local" means the script's mechanism is mostly a composition of reusable primitives around named papers, libraries, roadmaps, comparator files, or submission policy; those facts should stay with the formalization rather than becoming package defaults.

| Source path | Disposition in this package |
| --- | --- |
| `tools/leanq/**` | Retained as the installable `leanq` package, with its Lean exporter, cached index/query layer, graph pipeline, and five HTML viewers. The fixed project headline lanes were generalized to configurable landmarks. |
| `tools/lean_dep_trace/**` | Superseded by `leanq graph-index`, graph slicing/headline analysis, and cached dependency JSON. The old milestone file and DKPS wrapper are project configuration. |
| `scripts/_external_checkouts.py` | Project-local. Its specific external checkout registry is not reusable; the generic "input unavailable" exit-code handling is supported by `gates`. |
| `scripts/audit_checklist.py` | Generalized as `aiq-lean source checklist`, including mark preservation and Git rename carry-forward. |
| `scripts/audit_full_paper_sine_theta.py` | Project-local paper audit composition; use census/review/alignment plus `leanq` for a new source. |
| `scripts/audit_profile.py` | Generalized as `aiq-lean source profile`. |
| `scripts/audit_scan.py` | Generalized as `source similar`, `large`, `dead`, `names`, and `definitional-escapes`. |
| `scripts/certify_davis_kahan_1970.py` | Project-local certificate composition. Generic ingredients are census validation/probing, alignment packets, workspace reports, source audits, and `leanq`. |
| `scripts/check_comparator_signatures.py` | Project-local comparator contract. |
| `scripts/check_conflict_markers.py` | Generalized as `source conflicts`. |
| `scripts/check_davis_kahan_1970_result_inventory.py` | Generic result/source-atom cross-linking is extracted as `coverage`; Davis--Kahan result-kind taxonomy, terminality, boundary reason codes, semantic adjudications, and hash-pinned certification policy remain project-local. |
| `scripts/check_davis_kahan_1970_source_census.py` | Generalized by `census validate` and `workspace validate`. |
| `scripts/check_davis_kahan_1970_statement_map.py` | Project-local organizational statement map. Its source-atom/result cross-links are now understood by `coverage`; statement grouping, TeX marker policy, and named source boundaries remain DKPS data. |
| `scripts/check_davis_kahan_hidden_foundations.py` | Project-local foundation criterion; use `leanq deps`, graph slices, and configurable landmarks for a new project. |
| `scripts/check_declaration_name_drift.py` | Generalized as `source snapshot` and `source drift`. |
| `scripts/check_dependency_layers.py` | Generalized as YAML/JSON-driven `imports check`. |
| `scripts/check_distilled_literature_index.py` | Project-local literature corpus policy. Its fixed groups, required note markers, and completion definitions are source-project content. |
| `scripts/check_dkps_application_source_censuses.py` | Generalized by heterogeneous `CensusDocument` validation and workspace aggregation. |
| `scripts/check_dkps_result_semantic_reviews.py` | Generalized by `SemanticReviewDocument` and workspace validation. |
| `scripts/check_docstring_coverage.py` | Generalized as `source docstrings`. |
| `scripts/check_duplicate_qualified_names.py` | Generalized as `source duplicates`. |
| `scripts/check_experimental_coverage.py` | Generalized as policy-driven `source module-coverage`; the DavisKahan.Experimental root, scope, and exception reasons remain DKPS policy data. |
| `scripts/check_experimental_root_status.py` | Project-local staging/build policy. |
| `scripts/check_expose_ratchet.py` | Generalized as YAML-driven `ratchet check` with file or match counts. |
| `scripts/check_library_structure.py` | The reusable mechanisms are `imports check`, `namespaces check`, `source aggregates`, admission/source audits, and `leanq`; the named library architecture remains project-local policy. |
| `scripts/check_namespace_policy.py` | Generalized as YAML/JSON-driven `namespaces check`. |
| `scripts/check_palomar_readiness.py` | Project-local submission/comparator readiness composition. |
| `scripts/check_private_shadows_public.py` | Generalized as `source private-shadows`. |
| `scripts/check_roadmap_delivered.py` | Generalized name-delivery scan is `source roadmap`; the TauCeti roadmap checkout, donor-library selection, preference order, and semantic completion decisions remain project-local data/policy. |
| `scripts/check_spectra_namespace.py` | Generalized mechanism is `namespaces check`; the Spectra-specific rule stays project-local. |
| `scripts/check_stale_build_artifacts.py` | Generalized as `source orphan-artifacts --check`. |
| `scripts/check_submission_prose.py` | Project-local submission prose policy. |
| `scripts/check_tauceti_readiness.py` | Project-local readiness composition over reusable source/build primitives. |
| `scripts/check_tauceti_roadmap_topics.py` | Project-local roadmap taxonomy. |
| `scripts/check_yu_wang_samworth_source_census.py` | Generalized by `census validate` and `workspace validate`. |
| `scripts/clean_orphan_build_artifacts.py` | Generalized as `source orphan-artifacts --fix`. |
| `scripts/closed_operator_census.py` | Generalized as `source symbol-census`, with raw and code-only counts. |
| `scripts/derive_tauceti_submission_ladder.py` | Project-local roadmap/submission composition. |
| `scripts/export_for_tauceti.py` | Project-local extraction/export policy. |
| `scripts/fix_lean_warnings.py` | Retained/generalized as `lean-warning-fix` and `aiq_lean_tools.warning_fixer`. |
| `scripts/generate_all_aggregates.py` | Generalized as configurable `source aggregates`. |
| `scripts/install_comparator_tools.sh` | Project-local external comparator installation. |
| `scripts/inventory_admission_closure.py` | Generalized as `source admissions`. |
| `scripts/inventory_davis_kahan_debt.py` | Project-local debt taxonomy; generic candidate/profile/audit commands supply the measurements. |
| `scripts/lake_build_report.py` | Retained/generalized as `lake-build-report` and `aiq_lean_tools.lake_report`. |
| `scripts/probe_census_declarations.py` | Generalized as `CensusDocument.probe` / `census probe` through a mockable `LeanBackend`. |
| `scripts/proof_length_census.py` | Generalized as `source proof-length`. |
| `scripts/refresh_tauceti_pr1_consistency.py` | Project-local generated consistency data. |
| `scripts/render_davis_kahan_1970_audit_packet.py` | Project-local rendering composition; generic census/review/alignment/workspace HTML renderers are installed. |
| `scripts/render_davis_kahan_1970_source_census.py` | Generalized as `census render` and `census html`. |
| `scripts/render_distilled_literature_index.py` | Project-local literature index presentation because its grouping and prose are source-corpus content. |
| `scripts/render_semantic_alignment_review.py` | Generalized as `review render/html` and `alignment render`. |
| `scripts/run_challenge_comparator.sh` | Project-local comparator invocation. |
| `scripts/run_gates.py` | Generalized as `gates list/run` with optional YAML policy for slow, unavailable, advisory, and stronger-check cases. |
| `scripts/run_git_of_theseus.sh` | Retained as auxiliary `scripts/run-git-of-theseus.sh`. |
| `scripts/setup-lake-cache.sh` | Retained/generalized as auxiliary `scripts/setup-lake-cache.sh`. |
| `scripts/source_census_importance.py` | Folded into census/alignment validation and ordering rather than shipped as a separate source-project module. |
| `scripts/upgrade_census_schema_v4.py` | One-shot project migration is not shipped. The package reads the maintained census families directly and exposes stable mutation APIs. |
| `scripts/verify_palomar.sh` | Project-local submission/comparator verification. |
| `YuWangSamworth2015/scripts/verify_grounding.py` | Generalized as policy-driven `source grounding`; required files/declarations and forbidden admissions/axioms now live in YAML/JSON. |
| `dev/hilbert-space-operator-roadmap/module-system-conversion.py` | Generalized as `source module-migrate` with check/write modes and a reusable Python API. |
| `dev/posthoc-prompt-analysis/tools/{extract_prompts,analyze,merge_findings,mistakes}.py` | Project-local research instrumentation. It studies agent/human interaction transcripts rather than formalization state, and its raw-session layout/taxonomy is not a package default. |
| `docs/presentation/mathlib_contribution_dependencies.py` | Project-local presentation data. Generic dependency graphing is handled by `leanq` and source import closures; the named candidate nodes are presentation content. |
| `papers/formalization_draft2/scripts/accounting_lib.py` | Generic Git commit/co-author/churn extraction is generalized as `history`; token-ledger joins, model-name normalization, regression models, and pricing assumptions remain manuscript-study code. |
| `papers/formalization_draft2/scripts/build_accounting.py` | Project-local empirical-study accounting composition over a specific LLM ledger, cutoff, pricing file, and missing-data model. |
| `papers/formalization_draft2/build_resource_valuation_20260817.py` | Project-local resource/cost valuation analysis; depends on manuscript-specific service assumptions and historical pricing. |
| `papers/formalization_draft2/scripts/build_dependency_analysis.py` | Generalized as `source import-closure`, including conservative source-import closures, family counts, overlaps, nested-source-root suffix resolution, and standalone HTML. |
| `papers/formalization_draft2/scripts/build_formalization_prerequisite_trace.py` | Superseded by elaborated `leanq graph-index` plus `graph-slice`; its paper-specific root groups and manuscript exports remain data. |
| `papers/formalization_draft2/scripts/build_formalization_theory_graph.py` | Superseded by the reusable `leanq` semantic graph pipeline and viewers; theory labels/overrides used by the manuscript remain project data. |
| `papers/formalization_draft2/scripts/build_quench_reuse_trace.py` | Superseded by `leanq graph-slice`, headline/landmark consumption analysis, and comparison views; the Quench root selections are project data. |
| `papers/formalization_draft2/scripts/build_provenance.py` | Generalized as `source provenance`, which inventories recorded module-doc provenance blocks and configurable relationship markers. |
| `papers/formalization_draft2/scripts/build_formalization_provenance_credit.py` | Project-local manuscript credit attribution joining specific generated evidence tables. Generic source and Git provenance inventories are packaged separately. |
| `papers/formalization_draft2/scripts/build_source_census.py` | Superseded by census/workspace summaries and generated Markdown/HTML views; paper-specific LaTeX macros and row-classification prose remain manuscript code. |
| `papers/formalization_draft2/scripts/build_interactions.py` | Project-local empirical interaction-study report; its reusable concern is outside formalization-state tracking. |
| `papers/formalization_draft2/scripts/build_all.py` | Project-local manuscript build orchestrator. Package commands are composable rather than assuming one paper-output pipeline. |
| `papers/formalization_draft2/scripts/fetch_openalex_bibliometrics.py` | Project-local bibliography snapshot tooling. It requires network/API policy and is not part of Lean/formalization introspection. |
| `setup_lean.sh` | Deliberately not installed: it mutates host toolchains and performs network installation. The package detects/uses Lean when present but does not own system bootstrap. |
| `setup_lean_lsp_mcp.sh` | Deliberately not installed: host/agent-specific MCP registration and network installation should remain outside a portable Python package. |
| `viz-proof-structure.sh` | Superseded by documented `leanq graph-*` commands; its selected censuses/families are project configuration. |
| `scripts/historical/**` | Historical/obsolete gates stay archived in the source project rather than becoming maintained package behavior. |

## What is deliberately not copied

Paper statements, discrepancy judgments, source locators, blocker tables, roadmap topics, submission decisions, external checkout paths, comparator fixtures, and fixed library names are data from the original formalization. Copying those into a general package would turn historical project state into accidental defaults. The package instead exposes schemas, APIs, policies, and mockable execution seams that let a new formalization express its own facts.

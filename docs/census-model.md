# Census and semantic-review model

The tools accept several related source-ledger shapes. This is intentional: the Davis--Kahan, YWS, and DKPS application ledgers evolved different extra axes while retaining the same core job.

## Census core

A census has an `items` array. Each item needs a stable `id`; most projects also use:

- `source_anchor`, `source_kind`, `title`, and a source summary/claim;
- `status`: implementation/source-facing state;
- `verification`: compiler/declaration-resolution state;
- `importance`: headline, major, supporting, or technical;
- `lean_declarations` and optional `planned_declarations`;
- `gap_refs` or `blocked_by` references;
- optional `source_locator` with a repository-relative file and line interval;
- optional `semantic_alignment` with a classification and written rationale.

The document's `status_definitions`, `verification_definitions`, `importance_definitions`, `semantic_alignment_definitions`, `gaps`, and `blockers` tables define the valid vocabulary. This allows a project to make its distinctions explicit without modifying package code.

A completion-oriented census may add `completion_certification_definitions`, `completion_certification`, and `completion_holes`. These remain separate from compiler verification.

## Dedicated semantic review

A dedicated semantic review has a `rows` array and `relation_definitions`. Each row can map individual source clauses to Lean clauses with a declared relation and reviewer prose. This is appropriate when one census-row-level semantic classification is too coarse.

## Embedded curated review

A census row may carry `semantic_review` with normalized source setup/hypotheses/conclusions/scope, canonical and supporting Lean declarations, a clause map, and review notes. `aiq-lean alignment render` turns those records into a compact review packet and can attach `#check` / `#print` evidence.

## Editing

Use stable row IDs and dotted fields:

```bash
aiq-lean census patch dev/paper-full-source-census.json \
    --id theorem-2 \
    --set semantic_alignment.classification=exact \
    --set 'semantic_alignment.detail=Matches the printed theorem.'
```

Values that begin with `{`, `[`, or `"`, and the literals `true`, `false`, and `null`, are parsed as JSON-compatible scalars. Complex rows can be added from a JSON file with `census add --from-json`.

## Compiler probing

`census probe` collects cited Lean declaration names, infers imports from the source index when possible, generates one temporary Lean module, and probes all names. `--write` updates only verification states that the census itself defines (`proved_in_build`, `partially_in_build`, `absent`). It does not change source-fidelity or semantic-alignment fields.

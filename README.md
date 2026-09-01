# aiq-lean-formalization-tools

Reusable tooling for source-faithful Lean formalization projects. The package turns the tooling developed around Davis--Kahan, Yu--Wang--Samworth, and four DKPS formalizations into project-independent Python APIs and CLIs.

The package has two complementary layers:

- **`leanq`** queries the elaborated Lean environment. Use it for declaration kinds, axiom closure, dependency and reverse-dependency queries, project semantic graphs, graph slicing, and the interactive graph viewers.
- **`aiq_lean_tools` / `aiq-lean`** manages source censuses, hard result/source-atom coverage inventories, semantic reviews, compiler probes, workspace summaries, HTML reports, source audits, import/namespace policies, aggregate modules, Lake diagnostics, and repository hygiene.

Source-level Python scans are structural audits. They are useful for names, imports, admissions, docstrings, review prioritization, migration counts, and file bookkeeping. The `similar`, `dead`, `names`, and `large` commands intentionally report candidates for human review. Questions that depend on elaboration belong in `leanq` or a compiler-backed probe.

## Install

```bash
python -m pip install -e .
# or
uv pip install -e .
```

The runtime dependency is PyYAML. A Lean toolchain is only required for commands that actually invoke Lake/Lean or build a `leanq` index.

## Main commands

```bash
# Bootstrap and inspect a whole formalization effort
aiq-lean workspace init --root /path/to/new-project --name "My formalization" \
    --source "Primary paper citation"
aiq-lean workspace status --root /path/to/formalization
aiq-lean workspace validate --root /path/to/formalization
aiq-lean workspace html --root /path/to/formalization

# Source census lifecycle
aiq-lean census init dev/paper-full-source-census.json --title "Paper source census"
aiq-lean census add dev/paper-full-source-census.json --from-json dev/new-result.json
aiq-lean census show dev/paper-full-source-census.json --id theorem-2
aiq-lean census validate dev/paper-full-source-census.json
aiq-lean census summary dev/paper-full-source-census.json
aiq-lean census patch dev/paper-full-source-census.json \
    --id theorem-2 --set verification=proved_in_build
aiq-lean census probe dev/paper-full-source-census.json --write
aiq-lean census render dev/paper-full-source-census.json -o build/paper-census.md
aiq-lean census html dev/paper-full-source-census.json -o build/paper-census.html

# Hard completion denominator + fine-grained source-fidelity atoms
aiq-lean coverage validate dev/paper-result-inventory.json --static-declarations
aiq-lean coverage summary dev/paper-result-inventory.json
aiq-lean coverage show dev/paper-result-inventory.json --id theorem-2
aiq-lean coverage show dev/paper-result-inventory.json --atom source-2.1-hypothesis
aiq-lean coverage render dev/paper-result-inventory.json -o build/paper-coverage.md
aiq-lean coverage html dev/paper-result-inventory.json -o build/paper-coverage.html

# Dedicated clause-by-clause semantic reviews
aiq-lean review init dev/paper-result-semantic-review.json --title "Paper semantic review"
aiq-lean review add dev/paper-result-semantic-review.json --from-json dev/new-review-row.json
aiq-lean review show dev/paper-result-semantic-review.json --id theorem-2
aiq-lean review validate dev/paper-result-semantic-review.json
aiq-lean review html dev/paper-result-semantic-review.json

# Curated source-to-Lean review packet from embedded semantic-review data
aiq-lean alignment render dev/paper-full-source-census.json --importance headline
aiq-lean alignment render dev/paper-full-source-census.json --statements
# Pin the elaborated statement each review accepted, and fail when it moves
aiq-lean alignment pin dev/paper-full-source-census.json --id theorem-2
aiq-lean alignment check dev/paper-full-source-census.json
aiq-lean alignment check dev/paper-result-semantic-review.json --sidecar .leanq/MyLibrary.statements-v1.jsonl

# Python-only source audits
aiq-lean source scan --root .
aiq-lean source admissions --root .
aiq-lean source proof-length --root . --library MyLibrary --min 100
aiq-lean source docstrings --root . --check
aiq-lean source duplicates --root . --check
aiq-lean source private-shadows --root . --check
aiq-lean source similar --root . --library MyLibrary
aiq-lean source similar --root . --library MyLibrary --definitions
aiq-lean source large --root . --library MyLibrary
aiq-lean source dead --root . --library MyLibrary
aiq-lean source names --root . --library MyLibrary
aiq-lean source definitional-escapes --root . --check
aiq-lean source profile --root . --library MyLibrary --details
aiq-lean source symbol-census ClosedOperator --root . --include 'MyLibrary/**/*.lean' --verbose
aiq-lean source checklist --root .
aiq-lean source module-coverage dev/module-coverage.yaml --root .
aiq-lean source roadmap --roadmap-root /path/to/roadmap --root . \
    --library MyLibrary --prefer MyLibrary.Core --missing --map
aiq-lean source grounding dev/grounding-policy.yaml --root .
aiq-lean source import-closure --root . \
    --target 'Core=MyLibrary.Core' --target 'Application=MyApplication'
aiq-lean source import-closure --root . \
    --target 'Core=MyLibrary.Core' --target 'Application=MyApplication' \
    --html build/import-closures.html
aiq-lean source provenance --root . --include 'MyLibrary/**/*.lean' \
    --marker 'adaptation=adapted|ported|donor' --details
aiq-lean source module-migrate MyLibrary/Foo.lean MyLibrary/Bar.lean --check
aiq-lean source module-migrate MyLibrary/Foo.lean MyLibrary/Bar.lean --write
aiq-lean source staging dev/staging-policy.yaml --root .
aiq-lean source staging dev/staging-policy.yaml --root . --compile
aiq-lean source module-plan dev/module-plan.yaml --root .
aiq-lean source export dev/module-export.yaml --source-root . \
    --target-root /path/to/upstream --cluster core

# Exact module-pair signature compatibility before an external comparator
aiq-lean signatures check dev/signature-policy.yaml --root .

# Recursive foundation campaign status and visualization
aiq-lean foundations validate dev/foundation-map.yaml --root .
aiq-lean foundations html dev/foundation-map.yaml --root . -o build/foundations.html

# Literature / source-reference inventory
aiq-lean literature validate prose/literature/source_manifest.json --root .
aiq-lean literature summary prose/literature/source_manifest.json
aiq-lean literature patch prose/literature/source_manifest.json \
    --id DavisKahan1970 --set distilled_status=complete
aiq-lean literature html prose/literature/source_manifest.json -o build/literature.html

# Reproducible evidence bundle defined entirely by a project plan
aiq-lean certify build dev/certification.yaml --root . --out build/certification --archive
aiq-lean source snapshot --root . -o dev/declaration-snapshot.json
aiq-lean source drift dev/declaration-snapshot.json --root . --check
aiq-lean source conflicts --root . --check
aiq-lean source orphan-artifacts --root . --check

# Import and namespace architecture live in data, not Python conditionals
aiq-lean imports check dev/import-policy.yaml --root .
aiq-lean namespaces check dev/namespace-policy.yaml --root .

# Regression gates and count ratchets are also policy-driven
aiq-lean gates list --root . --config dev/gate-suite.yaml
aiq-lean gates run --root . --config dev/gate-suite.yaml --fast
aiq-lean ratchet check dev/source-ratchets.yaml --root .

# Recursive All.lean-style aggregate generation
aiq-lean source aggregates --root . --base MyLibrary --library MyLibrary --check

# Build diagnostics and conservative mechanical warning fixes
lake-build-report --root . MyLibrary.All
lean-warning-fix build.log --root .

# Auditable repository-history / co-author provenance
aiq-lean history summary --root .
aiq-lean history html --root . -o build/formalization-history.html

# Elaborator-backed semantic introspection
leanq libs
leanq index
leanq stats
leanq query --name SinTheta --json
leanq axioms MyLibrary.someTheorem
leanq deps MyLibrary.someTheorem --local
leanq statement MyLibrary.someTheorem --hide-boundary
leanq graph-index --out build/leanq/project-semantic-graph.json
leanq graph-headlines build/leanq/project-semantic-graph.json \
    --view consumption --target MyApplication.mainTheorem \
    --census dev/paper-full-source-census.json \
    --landmark 'Spectral bridge=MySpectralLibrary' \
    --out build/leanq/headline-consumption.json
leanq graph-html build/leanq/project-semantic-graph.json \
    --census dev/paper-full-source-census.json \
    --out build/leanq/project-semantic-graph.html
```

## Python API

```python
from aiq_lean_tools import (
    CensusDocument, CertificationPlan, FormalizationWorkspace, FoundationMap,
    LiteratureDocument, ModuleExportPolicy, SignaturePolicy, StagingPolicy,
)
from aiq_lean_tools.census import load_census
from aiq_lean_tools.lean_backend import MockLeanBackend

census = load_census("dev/paper-full-source-census.json")
findings = census.validate()

backend = MockLeanBackend({
    "MyPaper.main_theorem": "MyPaper.main_theorem : Proposition",
})
probe = census.probe(
    backend=backend,
    imports=["MyPaper"],
)
census.apply_probe(probe)

workspace = FormalizationWorkspace.discover(".")
print(workspace.overview())
```

The mock backend is the test seam for submodule calls to Lean. Production probes use `SubprocessLeanBackend`; callers can supply another backend implementing the same protocol. The package's own tests do not require a Lean installation.

## Data model

The package deliberately supports the census families that arose during the DK/YWS/DKPS work instead of forcing them into one narrow schema:

- source census with status and compiler verification;
- completion census with a third completion-certification axis;
- application source/semantic-alignment census with source locators and explicit semantic classification;
- dedicated clause-by-clause semantic review documents;
- a separate hard result inventory linked to fine-grained source-fidelity atoms when a project wants an explicit completion denominator.

The authoritative artifact is JSON. Markdown and HTML are generated views. The validators preserve paper-specific status vocabularies by reading each document's `*_definitions` tables rather than hard-coding one project's states.

See `docs/census-model.md` for the shared conventions and `src/aiq_lean_tools/schemas/` for permissive interchange schemas.

## Workspace discovery

`FormalizationWorkspace` discovers census, semantic-review, result/source-atom coverage, literature inventories, and foundation maps recursively, ignores build/vendor metadata directories, reads `formalization.yaml` when present, and reports:

- census/review row counts plus counted-result and source-atom totals;
- literature-work and tracked-foundation-node totals with their maintained status summaries;
- status, verification, importance, and semantic-alignment distributions;
- unique Lean declarations cited by the source ledgers;
- declarations reused across source papers;
- optional structural source audit counts;
- the existing `leanq` project graph when `build/leanq/project-semantic-graph.json` exists.

Its HTML report embeds all data in one file and needs no server. Review checklists are generated separately because they are mutable human workflow state; checked marks survive regeneration and Git-detected renames.

## Architecture

See `docs/architecture.md` for the package boundaries, `HISTORY.md` for the DKPS-to-package correspondence and cleanup sequence, `docs/migration-from-dkps.md` for the extraction rationale, and `docs/source-tool-inventory.md` for the path-by-path disposition of the original tooling snapshot.

The `leanq` CLI and Python package are retained as their own top-level package so existing agent instructions and command lines continue to work.

## Auxiliary repository tools

`scripts/setup-lake-cache.sh` carries the reusable bind-mount cache workflow for Lean repositories on slow/shared filesystems. `scripts/run-git-of-theseus.sh` carries the optional repository-history visualization wrapper. They remain shell scripts because their behavior is fundamentally host/mount/tooling orchestration rather than a portable Python library API.

See `examples/` for starter formalization, gate, grounding, ratchet, signature, foundation, staging, module-export, module-plan, certification, and literature policy/metadata files.

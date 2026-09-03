# Changelog

## Unreleased

### The audit browser answers for what it is showing

Review of the served browser found that a page could keep showing Lean evidence
the repository no longer held, and that the private-source half stopped one step
short of the thing it was built for.

- Cache what a payload is *made of*, not how much of it there is.
  `AlignmentService` keyed its cache on a record count and a node count, so
  re-elaborating a theorem whose type had changed, or rebuilding a graph after a
  rename, left both counts identical and the browser went on serving the previous
  statement. `DeclarationService` now publishes `statement_revision()`,
  `graph_revision()` and `source_revision()`, and the payload cache, the page
  cache and the ETag are keyed on all three. Proof closures are invalidated on
  the graph revision rather than on its (constant) path.
- Rebuild the Lean source scan when the sources change. It was cached for the
  life of the process and nothing refreshed it. The server's watcher now
  recomputes the source revision on a slow cadence, in a thread, so requests
  never pay the tree walk.
- A private document may declare `overlay_for` and a `locator_map`, making it an
  alternate *rendition* of a public document's passages rather than a separate
  document. Configuring a lawful local transcription now puts the printed
  passage beside the checked-in reconstruction on every row that cites it, behind
  a **Reconstruction | Original** toggle, with independent hashes and with no
  checked-in review naming a machine-local file. Private TeX macros travel with
  private text: excluded from a public payload, included under
  `--include-private`, where without them a transcription in its own notation
  renders as broken formulas.
- Show the semantic certification on the row. A census row's `status` and
  `verification` say nothing about whether hostile semantic review accepted the
  correspondence, so a blocked row read as `compiled_exact · proved_in_build`
  until somebody expanded it. A census may point at its result inventory with
  `result_inventory`, and the page states disposition, compiler evidence and
  semantic review together, with an unaccepted certification as the loudest badge
  on the row.
- An uncurated row's fallback clause is `open`, not `claimed_exact`. It said in
  one breath that no correspondence was registered and that the correspondence
  was exact.
- Require a quoted `source_excerpt` on a clause whose relation is a claim about
  specific printed words (`representation_change`, `via_theorem`,
  `inherited_standing_assumption`, `refutation`) and on any clause left `open`.
  Without one the browser can reach the passage but not mark the sentence under
  dispute.
- Enforce *exactly* one primary source fragment. The message had always said
  "exactly one"; the check accepted any number greater than zero.
- Decide private-source containment on the resolved path. A relative path, a
  `..` segment or a symlink back into the checkout defeated a rule applied to
  the spelling.

### The literature half of a semantic review

A semantic review claims a Lean declaration says what a paper says. The Lean half
was mechanical -- elaborated type, structural hash, statement closure, and a pin
that fails when any of it moves. The paper half was a paragraph the reviewer
typed into the ledger: not connected to the checked-in source document, not
rendered as mathematics, and not noticed when the source document was edited
underneath the review that had accepted it. `docs/source-model.md` describes what
replaces it.

- Add `aiq_lean_tools.source_model`: source documents, stable locators, resolved
  fragments with their mathematics parsed into renderable blocks, and content
  hashes. A document is declared by a `source_document` block on a work in the
  literature manifest the repository already keeps; markers (`DK-CERT-CLAIM-BEGIN
  …` / `-SOURCE-BEGIN` / `-SOURCE-END`) and line ranges both resolve, and a
  locator citing a file no manifest declares is read anyway. `\newcommand`
  definitions are collected from the document and its preamble so the page can
  render the paper's own notation.
- Add `aiq_lean_tools.correspondence`: a review's clause map read as edges, with
  a small relation vocabulary a document may extend. `via_theorem`,
  `representation_change` and `refutation` are assertions about *another*
  theorem, so they must name it -- a representation change with no correspondence
  theorem is a prose claim and now fails validation.
- A row says how it reads its passage. Declaring `local` while citing a standing
  condition inherited from elsewhere in the source is an error; declaring nothing
  is a warning; `nonlocal` without a rationale is a warning. The classification
  rests on link structure rather than on a boolean somebody typed. Davis--Kahan
  Theorem 8.2 is the case this exists for: read as a self-contained passage it is
  false, and only (1.5) and (3.5), standing since Sections 1 and 3, make it true.
- A quoted `source_excerpt` is checked against the passage it cites, folding TeX
  ties, `--` and typographic quotes so a quote copied from the `.tex` and one
  copied off the rendered page both match.
- Add source pins: `aiq-lean alignment pin` records the content hash of every
  cited passage beside the elaborated-type hashes, and `alignment check` reports
  `source-drift` when the reconstruction has been edited and
  `source-pin-unresolved` when a marker is gone. The two are separate evidence
  layers and are reported separately.
- Add `aiq-lean alignment adopt-source`, which turns a legacy single
  `source_locator` into a declared primary fragment. Until it is run, that
  locator is read as an implicit primary passage, so every existing ledger --
  including rows with no curated review at all -- shows its real source text
  rather than nothing.
- Rebuild `alignment html` as a three-lane comparison surface: the literature
  passage with its mathematics rendered, the clause-by-clause correspondence, and
  the Lean declaration with `Source` / `Elaborated` / `Expanded` / `Dependencies`
  tabs. Clicking a clause highlights the fragment it came from, marks the quoted
  excerpt inside it, and highlights the declarations and correspondence lemmas
  that realize it. Inherited passages are shown apart from the printed one; each
  lane says whether it is mechanically established or human-reviewed, and a lane
  can be widened to full width for a long signature. Rows are built on demand, so
  a page with many rows does not render hundreds of formulas nobody asked for.
- Vendor KaTeX (MIT) and inline it, fonts included, into the alignment page. A
  review packet opened from an archive, a file share or an offline checkout
  renders its own mathematics; nothing on the page references anything outside
  it. About 700 KB, paid only by pages that ask for mathematics.
- Serve the alignment view: `/view/alignment/<census-slug>` appears beside every
  census in `aiq-lean serve`, reusing the source scan, statement sidecars and
  dependency graph the server already holds rather than rebuilding them.
- Support an optional private source provider, configured **outside** the
  repository by `--private-sources` or `AIQ_PRIVATE_SOURCES`. A private path
  resolving inside the checkout is refused. Private fragments keep their
  identity, locator and hash in every serialization and withhold their text
  unless `--include-private` is passed for a local session.
- Add `--row` to `alignment render`/`html`, so a packet can be built around a
  result whose source-fidelity importance is not `headline` without promoting it.

### Fixed

- **`leanq` re-read every source file on every declaration lookup.**
  `candidate_declaration_modules` cost 2.2 seconds each, which is fine for the
  handful of names a graph target needs and catastrophic for the several hundred
  an alignment packet seeds: 643 seeds spent twenty-three minutes re-reading the
  tree before Lean was invoked at all. Indexed once per library; the same job now
  reaches Lean in about two seconds.
- **The Lean syntax highlighter mangled its own markup.** Keyword colouring ran
  as a second `replace` over the string the first had just written into, and
  `class` is a Lean keyword, so every `<span class="t">` came out broken. One
  pass, two alternatives.
- **A served alignment page did not notice its source documents changing.** The
  ETag was derived from the census and the statements only, so editing the
  reconstruction left the cached page showing the passage the review had been
  accepted against -- exactly the drift a source pin exists to catch.
- **An embedded review on a non-headline row was never validated.** Only headline
  rows were obliged to carry the contract, and the check was skipped entirely for
  everyone else, so a review written on Theorem 8.2 or Proposition 4.4 was
  accepted unread. A review that asserts a clause-by-clause correspondence is now
  held to the whole surface wherever it appears; a lighter one that only records
  which declarations a row cites is checked for what it contains.
- **`assets/theme.css` was not in the package data.** Editable installs hid it;
  a wheel would have shipped viewers with no theme.
- `_proof_payload` resolves the graph index's spelling of a name, as the
  declaration audit page already did. Alignment pages were silently showing no
  dependency panel for every declaration a census cites without its outer
  namespace, and a declaration the graph does not know now says so instead of
  quietly omitting the tab.

- Record `typeDeps` -- the constants a declaration's *type* uses -- in every
  `leanq` index mode beside the merged `deps` list, and bump the graph cache
  version. A dependency graph that cannot tell a statement edge from a proof
  edge cannot say which hypotheses a theorem imposes, only what its proof used.
- Add `leanq statement`, an optional statement-closure sidecar. It unfolds a
  declaration's type through project definitions and structure fields down to a
  boundary library, and records the `#check`-style signature, the printed type,
  the elaborator's structural type hash, a text hash, docstrings, and structure
  fields. The first repository to use it had a hand-curated "local semantic
  dictionary" of what each compact predicate meant; that list was the one thing
  an auditor could not verify.
- Add statement pins: `aiq-lean alignment pin` records the structural and
  printed-type hashes of every declaration a semantic review claims, `alignment
  check` fails when a pinned elaborated type has changed, and both review
  validators reject a pin that names a declaration the review does not claim.
  A review is a claim about a type on the day it was read; nothing in a green
  build noticed when that type changed afterwards.
- `aiq-lean alignment render --statements` reads the statement sidecar instead
  of ad-hoc `#check` probes: elaborated signatures, pin status, and for each
  canonical declaration the project constants in its statement closure that the
  hand-written local semantic dictionary does not disclose.
- Add `aiq-lean alignment html`: one self-contained page per review surface
  with the normalized source statement, the clause map, each canonical
  declaration's elaborated signature and pin status, its statement closure as a
  collapsible tree with the undisclosed constants called out, and -- given a
  saved `leanq graph-index` -- its project-local proof dependencies with every
  direct edge labelled `type` or `proof`.
- Load environment extensions when the exporter pretty-prints. Without
  `loadExts` no notation unexpander runs, so `z + w` prints as
  `instHAdd.hAdd z w` and `ℂ` as `Complex`.

## 0.1.0 first-consumer pass

Changes driven by migrating the `aiq-dkps-formalization` repository onto the package.

- Anchor the `namespace` / `section` / `end` / `import` patterns to horizontal
  whitespace. `\s` matches a newline, so `end` on one line and `section` on the
  next parsed as a single `end section`, popping the wrong stack entry and
  silently mis-qualifying every declaration in the rest of the file. The
  namespace-policy module now shares the one set of patterns instead of carrying
  its own.
- Parse Lean 4 module-system `public` / `private` / `meta` import modifiers. They
  were silently unparsed, which dropped 902 real import edges in the first
  repository migrated and made every import-derived view -- layer policy,
  admission closure, module plans, coverage -- report a cleaner architecture than
  the source has.
- Add `source_scope` (`roots`, `exclude_dirs`) in `formalization.yaml`, honoured by
  `scan_lean_project` and the candidate audits. Without it a checkout carrying
  vendored donors, retired trees, or submitted copies of itself reports them as
  findings; the first repository migrated produced 10,781 phantom duplicate names.
- Treat a Lake library's root module as part of that library's scope. `Foo.lean`
  sits beside `Foo/`, not inside it, so a scope listing only the directory drops
  the one module every consumer of the library actually imports -- and with it
  every reachability answer that starts from the library root.
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
- Read a module's signature evidence in one Lean run instead of two per
  declaration. `signatures check` cost 2 x declarations x sides Lean invocations,
  which is why the gate it backs was one people avoided running.
- Probe every Lean query set with a canary that must not resolve. A probe whose
  parser cannot fail proves nothing, and the failure is silent: every declaration
  reads as resolved and the census reports full coverage.
- Report an unresolved census declaration that exists as a `private` one, and say
  which module declares it. "Gone" and "present but private" look identical to a
  `#check` and mean opposite things: a missing name is lost mathematics, a private
  name is mathematics that exists and cannot be cited as evidence.
- Add `--check` to every Markdown/LaTeX render command (`census`, `review`,
  `coverage`, `literature`, `foundations`, `alignment`, `source module-plan`).
  A generated file committed to Git and never verified goes stale silently and
  then reads as a maintained document.
- Place a module that depends on its own directory's aggregate in the nearest
  ancestor aggregate instead. Listing it in its own is a Lake `build cycle
  detected`, reported far from the file that caused it -- so a maintainer fixes it
  by hand, after which the generator calls that file permanently stale and
  regenerating it breaks the build. Six aggregates in the first repository migrated
  were in exactly that state.
- Preserve a hand-written directory note in a generated aggregate. The import list
  is the only part a generator can derive; overwriting the whole trailer deletes
  the note silently, because the file still compiles.
- Cross-validate a semantic review against its `companion_census`: unreviewed rows,
  and locator or declaration-list drift, are errors. The two documents answer
  different questions about the same rows, so drift means one of them describes
  something that is no longer there.
- Validate a review's `source_locator` line range against the file, as census
  locators already were. Line citations into prose rot silently when that prose is
  edited.
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

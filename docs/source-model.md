# The literature source model

A semantic review claims that a Lean declaration says what a paper says. That
claim has two halves, and until now only one of them was real.

The Lean half was mechanical: `leanq` elaborates the declaration, records its
type, its structural hash and its statement closure, and `aiq-lean alignment
check` fails when any of that moves. The paper half was a paragraph the reviewer
typed into the ledger. Nothing connected it to the checked-in source document,
nothing rendered its mathematics, and nothing noticed when the source document
was edited underneath the review that had accepted it.

This document describes the model that supplies the missing half.

## What the pieces are

| concept | what it is |
| --- | --- |
| `SourceDocument` | a checked-in (or locally configured) file holding source material |
| `SourceLocator` | stable coordinates into one: a named marker, or a line range, plus the section/result/page/equation labels a reader needs |
| `SourceFragment` | what a locator resolves to: the text, its mathematics parsed into renderable blocks, and a content hash |
| `SourceLibrary` | every document a repository can resolve locators against |
| correspondence edge | one clause of a review, read as an edge from a source fragment to Lean declarations |
| source pin | the content hash of the fragment a review accepted |

Nothing here is specific to one paper. Davis–Kahan resolves markers in a TeX
reconstruction; Yu–Wang–Samworth, Acharyya, Helm and Quench resolve line ranges
in Markdown transcriptions. Both go through the same model.

## Declaring source documents

Documents come from the literature manifest a repository already keeps — the one
`aiq-lean literature validate` checks. A work becomes a source document when its
`target_note` exists on disk, and a `source_document` block configures how to
read it:

```json
"DavisKahan1970": {
  "title": "The Rotation of Eigenvectors by a Perturbation. III",
  "target_note": "DavisKahan1970_part_III.tex",
  "source_document": {
    "marker_prefix": "DK-CERT",
    "format": "tex",
    "macro_files": ["preamble.tex"]
  }
}
```

`marker_prefix` turns on marker resolution. With prefix `DK-CERT`, the passage
for marker `T` is the text between `DK-CERT-SOURCE-BEGIN` and
`DK-CERT-SOURCE-END` inside the `DK-CERT-CLAIM-BEGIN T` … `DK-CERT-CLAIM-END T`
block — the source mathematics, without the heading and anchor around it. A
document with no `marker_prefix` resolves line ranges only.

`macro_files` are where the `\newcommand` definitions live. They are read and
handed to the page's math renderer, so `\norm{\sin\Theta}` renders as the paper
writes it rather than as a failed macro.

A locator citing a file no manifest declares is still read, so a census row
pointing at a transcription works with no manifest change at all.

## Locators, and what a review stores

A review stores coordinates and hashes, never a copy of the prose:

```json
"source_fragments": [
  {"id": "printed", "role": "primary",
   "locator": {"document": "DavisKahan1970", "marker": "DK-8.2-thm",
               "section": "8", "result": "Theorem 8.2"}},
  {"id": "standing-3-5", "role": "standing_assumption",
   "why": "Section 3 puts (3.5) in force for the rest of the paper.",
   "locator": {"document": "DavisKahan1970", "marker": "S3-standing-scope",
               "equations": ["(3.5)", "(1.5)"]}}
]
```

`role` is what the fragment is to this review:

| role | meaning |
| --- | --- |
| `primary` | the printed passage the result states (exactly one per review) |
| `standing_assumption` | a condition imposed earlier that this result inherits |
| `definition` | a definition established elsewhere that the statement uses |
| `convention` | a convention that changes how the statement is read |
| `context` | adjacent material a reviewer needs in order to judge the passage |

A review that has no `source_fragments` but does have the older single
`source_locator` is shown through that locator, marked implicit.
`aiq-lean alignment adopt-source` turns it into a declared fragment; nothing has
to be migrated before the browser is useful.

## Correspondence as structure

The clause map already paired a source clause with a Lean realization. Each
clause may now say where it came from and what it claims:

```json
{
  "source_clause": "The two crossed subspaces have equal dimension — condition (3.5).",
  "lean_realization": "…takes CrossedDefectsEquivalent P Q and no dimension hypothesis.",
  "status": "claimed_exact",
  "kind": "hypothesis",
  "relation": "inherited_standing_assumption",
  "source_fragment": "standing-3-5",
  "source_excerpt": "(3.5) is assumed as well as (1.5), except where the contrary is stated",
  "lean_declarations": ["…theorem8_2_branch_source_maximalAngle_lt_of_crossedDefects"],
  "lean_targets": [{"kind": "binder", "name": "hCross"}],
  "correspondence_declarations": ["…subspaceGap_eq_directedGap_of_crossedDefects"]
}
```

`relation` is a small vocabulary, not an ontology: `literal`, `definitional`,
`via_theorem`, `representation_change`, `specialization`, `generalization`,
`stronger_than_source`, `weaker_than_source`,
`inherited_standing_assumption`, `object_representation`, `refutation`. A
document's own `relation_definitions` extend it.

`via_theorem`, `representation_change` and `refutation` are assertions *about
another theorem*, so they must name it in `correspondence_declarations`. A
representation change with no correspondence theorem is a prose claim, and
validation says so.

`kind` groups clauses into `setup` / `hypothesis` / `conclusion` / `scope` /
`object` / `note`, so the browser can show hypotheses apart from conclusions.

### Source-side clause targets

`source_targets` gives the literature side the same explicit focus model as the
Lean side without copying whole source sentences into the review ledger.  A
clause can point at a compact TeX token inside a declared fragment:

```json
"source_fragment": "block-residual",
"source_targets": [
  {"kind": "math", "text": "A"},
  {"kind": "math", "text": "A_0"},
  {"kind": "math", "text": "E_0"}
]
```

The available kinds are `math`, `excerpt`, and `fragment`.  `math` resolves a
small TeX token against the parsed math spans of the freshly located fragment;
`excerpt` is the structured form of the older `source_excerpt` field; and
`fragment` focuses the entire passage.  `occurrence` is a zero-based disambiguator
when a token occurs more than once in the same fragment.

This pointer is deliberately not another source pin.  The fragment's content
hash remains the authority for whether the accepted source passage moved.  A
source target only records where, inside the current resolved fragment, the
reviewer meant to look.  Consequently the viewer can distinguish a moved source
fragment from a still-current fragment in which a particular focus token no
longer resolves.  The first is source drift; the second is source-target drift.

`source_excerpt` remains supported and is treated as a one-element `excerpt`
target, so existing reviews do not need to duplicate data.

### Lean-side clause targets

`lean_targets` is the Lean analogue of pointing at the exact source words, but it
usually does **not** copy Lean text. `leanq statement` exports the top-level
elaborated binders and final result as structure. A clause can point at them:

```json
"lean_targets": [
  {"kind": "binder", "name": "hgap"},
  {"kind": "result"}
]
```

The target's `declaration` may be omitted when the clause has exactly one
`lean_declarations` entry or the review has exactly one canonical declaration.
This keeps the common case non-redundant. Available target kinds are:

- `binder`: a top-level elaborated binder by its user name;
- `binder_type_dep`: binder(s) whose elaborated type uses a named constant, useful
  for generated instance binders such as an `RCLike` assumption;
- `result`: the final proposition after the top-level telescope;
- `declaration`: the whole declaration;
- `text`: an exact-text escape hatch for a subterm with no structural address.

The browser keeps the reviewer's current Lean tab selected and projects the
same structured target into that view. In **Source**, named binders are located
in the handwritten declaration and the top-level result clause is located
lexically; in **Elaborated**, the exported binder/result segment is highlighted
directly. Other views are never replaced just to satisfy a correspondence click:
if the target has a visible textual representative there it is marked, otherwise
the declaration reports that the selected clause is not rendered in that view.
These source-view locations are derived at render time and are not persisted as
a second copy of Lean text.

`alignment check` resolves the pointer against a fresh statement sidecar. A
removed/renamed binder or result that can no longer be resolved is reported as
`lean-target-drift`. The existing statement pin remains the declaration-level
guard: structural type drift is an error, printed-only drift is a warning. Thus
a target pointer is not a second copy of the theorem and does not replace
statement pins.

## What this makes checkable

Three things that used to be invisible:

**A quoted excerpt is checked against the passage.** `source_excerpt` must occur
in the fragment it cites. TeX ties, `--` and typographic quotes are folded, so a
quote copied from the `.tex` and one copied off the rendered page both match.
This runs in `aiq-lean alignment check`, which has the documents; schema
validation does not read them and skips it.

**A local reading cannot quietly inherit a standing condition.** A review says
how it reads its passage:

```json
"source_interpretation": "nonlocal",
"nonlocal_rationale": "The theorem is printed in Section 2 and proved in Section 6, where (3.5) has been standing since Proposition 3.2."
```

A row that declares `local` while citing a `standing_assumption` fragment or an
`inherited_standing_assumption` clause is an **error**. A row that declares
nothing is a **warning**. A `nonlocal` row with no rationale is a warning, and so
is one that declares `nonlocal` and cites no inherited passage. The
classification is therefore backed by link structure, not by a boolean somebody
typed.

**A source passage that moves invalidates the review.** `aiq-lean alignment pin`
records the content hash of every declared fragment beside the elaborated-type
hashes; `alignment check` compares both. Editing the reconstruction is
`source-drift`; a marker that no longer exists is `source-pin-unresolved`.

**A source focus pointer that moves is localized drift.** `source_targets` are
resolved against the freshly parsed source fragment.  If the fragment pin is
current but a selected math token or excerpt no longer lands, the clause reports
source-target drift.  If the target still lands but the fragment content hash
moved, it reports source drift instead.  This keeps focus coordinates separate
from the evidence hash that guards the passage as a whole.

**A Lean clause pointer that moves invalidates that pointer.** With a current
statement sidecar, `alignment check` resolves each `lean_targets` selector against
the elaborated telescope/result. If the declaration still compiles but the
reviewed binder was renamed, removed, or moved behind a different abstraction, the
localized target reports drift. This is deliberately separate from the whole-type
statement pin so the report can say both *the statement moved* and *this specific
review pointer no longer lands*.

## The three evidence layers

They are separate, and the browser keeps them separate:

```
source fragment   pinned / current / moved      mechanical (file + hash)
Lean statement    pinned / current / moved      mechanical (compiler)
correspondence    the reviewer's claim          human judgment
```

Mechanically established: Lean elaborated *this* type; this passage has *this*
hash; this statement's closure contains *this* predicate; this proof depends on
*these* declarations. Human-reviewed: that the source clause and the Lean clause
say the same thing; that a representation theorem is sufficient; that a nonlocal
reading is faithful. `claimed_exact` is a claim, and the page never renders it as
if the compiler had agreed.

A clause whose relation is a claim about *specific printed words* — a
representation change, an inherited standing assumption, a correspondence
carried by another theorem, a refutation — must quote them in `source_excerpt`,
and so must a clause whose status is `open`. Naming the passage is not enough:
the browser can then jump to the passage but cannot mark the sentence under
dispute, which is what the reader opened the row for.

## The three completion axes

A row carries three independent judgements, and a browser that shows only the
first two can advertise `compiled_exact · proved_in_build` for a row whose
hostile semantic review is blocked. The formal disposition and the compiler
evidence live on the census row; whether the printed result has survived hostile
semantic review is a different judgement, kept in a result inventory. A census
says where that lives:

```json
"result_inventory": {"path": "<paper>-formalization-result-inventory.json",
                     "collection": "results", "key": "id"}
```

Its rows are read for `disposition`, `verification`, `semantic_certification`
and `semantic_certification_note` (renameable through an optional `fields` map),
and the alignment page states all three on the collapsed row, with an
unaccepted semantic certification as its loudest badge. A census's own
`completion_certification` is shown separately and labelled as passage-level
bookkeeping: it is a coarser thing than a printed result's certification. The
pointer is optional, and a broken one costs an axis rather than the page.

## Public reconstruction and private source

A repository may lawfully hold a distributable reconstruction of a paper while a
transcription or publisher PDF stays local. Documents therefore carry a
visibility, and only public ones are reachable by default.

A private document is declared **outside the repository**, in a file named by
`--private-sources` or `AIQ_PRIVATE_SOURCES`:

```json
{"documents": {"DavisKahan1970-original": {
  "path": "/home/me/private/dk1970-transcription.md",
  "citation": "local transcription",
  "format": "markdown"}}}
```

A path resolving inside the checkout is refused: a file the repository can see is
a file the repository can commit.

Private fragments are pinned and cited like any other — the hash is derived from
the text but is not the text, so recording it publishes nothing — and their text
is withheld from every serialization. The payload keeps the fragment's identity,
locator, hash and visibility, and replaces its blocks with a note. Only
`--include-private` (on `alignment html`/`render`, or `aiq-lean serve`) embeds
the text, and that is for an ephemeral local session, never for a file that is
committed, published or shared.

Tests for private behaviour use invented prose. Never put publisher text in a
fixture.

### Reading the printed paper beside the reconstruction

A private document may declare itself an **overlay** of a public one. It is then
an alternate rendition of the same logical passages rather than a separate
document, and every review row that cites the reconstruction gains the printed
passage beside it — without any checked-in review naming a machine-local file,
which is what would make the review unshareable.

```json
{"documents": {"DavisKahan1970-original": {
  "path": "/home/me/private/dk1970-transcription.tex",
  "citation": "printed paper, local transcription",
  "format": "tex",
  "marker_prefix": "DK-CERT",
  "overlay_for": "DavisKahan1970",
  "locator_map": {"S2-sin-theta": {"lines": [214, 240]}}}}}
```

A transcription carrying the same markers as the reconstruction needs no
`locator_map` at all; one that spells passages differently maps them, and a
passage it does not cover simply has no counterpart. The two renditions are
separate texts with separate hashes; the browser shows a
**Reconstruction | Original** toggle and neither hash substitutes for the other.

Overlays are disclosed even in a public payload — a reviewer must be able to see
that a private provenance source was consulted — while their text, and their TeX
macro definitions, travel only under `--include-private`.

## Reading it

```bash
# a self-contained review page: passages, correspondence, elaborated Lean
aiq-lean alignment html dev/<paper>-full-source-census.json \
    --statements --graph build/leanq/project-semantic-graph.json \
    -o build/alignment.html

# one result, whatever its source-fidelity importance
aiq-lean alignment html dev/<paper>-full-source-census.json --row DK-8.2-thm -o /tmp/p.html

# in the workspace browser, beside every census
aiq-lean serve --root .

# with a local transcription available, for this session only
aiq-lean serve --root . --private-sources ~/private/sources.json --include-private
```

The page is self-contained: KaTeX and its fonts are vendored and inlined, so a
review packet opened from an archive, a file share or an offline checkout renders
its mathematics. That costs about 700 KB, paid only by pages that ask for it.

## Opting a project in

1. Add `source_document` to the work in the literature manifest, if it needs a
   marker prefix or macro files. A Markdown transcription cited by line range
   needs nothing.
2. Run `aiq-lean alignment adopt-source <census-or-review>` to turn existing
   `source_locator` rows into declared primary fragments.
3. Add the inherited passages a result actually depends on, with a `role` and a
   `why`, and set `source_interpretation`.
4. Add `relation`, `kind`, `source_fragment` and `source_excerpt` to the clauses
   that carry weight — a representation change or an inherited condition first.
5. Add `source_targets` for source math/phrases that should be highlighted from a clause.
6. Add `lean_targets` where a reviewer should be able to click through to a
   specific binder or result. Prefer structural selectors; use `text` only when no
   structural address describes the intended subterm.
6. `aiq-lean alignment pin <document>` and commit what it writes.

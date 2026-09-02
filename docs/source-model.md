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
5. `aiq-lean alignment pin <document>` and commit what it writes.

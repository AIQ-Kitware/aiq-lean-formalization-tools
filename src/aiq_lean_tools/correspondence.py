"""The source-to-Lean correspondence, as structure rather than prose.

A semantic review already carried a clause map: rows of "this source clause"
against "this Lean realization", both free text.  That is a reviewer's sentence,
and a browser can do nothing with it but print it.  It also hides the two
distinctions that matter most to a hostile reviewer:

* whether a clause is realized *literally*, or through a change of
  representation that some other theorem has to justify -- the paper writes
  ``tan(2 Theta_0)``, the Lean theorem concludes on a corner of a reflection,
  and something must say those agree;
* whether a hypothesis is *printed in the passage* or *inherited* from a
  standing condition imposed earlier in the paper.

Both used to disappear into a review note.  Here a clause names the fragment it
came from, the relation it claims, the Lean declarations that realize it, and
the correspondence theorems that carry the relation.  The relation vocabulary is
small and extends the ``status`` field already in use; it is not an ontology.

Every judgment in this module is a *human* claim.  What can be checked
mechanically is checked -- that a quoted excerpt really occurs in the fragment,
that a declared relation carries the evidence its kind requires, that a row
claiming a self-contained reading does not quietly cite an inherited condition
-- and the rest is reported as reviewed, never as proved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .common import Finding

#: The coarse status already in use on clause maps.  Kept, and still the default.
#: `open` is for a clause whose correspondence is not yet established: the
#: strongest evidence that exists may be recorded, but the clause is not being
#: claimed.  Without it a census could only either assert a correspondence it
#: did not have, or drop the evidence it did have.
CLAUSE_STATUSES = {"claimed_exact", "derived", "scope_companion", "open"}

#: What a clause claims about its two sides.  ``requires_evidence`` marks the
#: relations that are assertions about *other theorems*, and so must name them.
RELATIONS: dict[str, dict[str, Any]] = {
    "literal": {
        "label": "literal",
        "description": "The Lean clause states the source clause in the same vocabulary.",
        "requires_evidence": False,
    },
    "definitional": {
        "label": "definitionally equal",
        "description": "The Lean clause unfolds to the source clause; no theorem is needed.",
        "requires_evidence": False,
    },
    "via_theorem": {
        "label": "equivalent via theorem",
        "description": "A named theorem establishes that the two clauses agree.",
        "requires_evidence": True,
    },
    "representation_change": {
        "label": "representation change",
        "description": "The Lean statement uses a different representation of the same object; "
                       "a correspondence theorem must carry the relevant semantics.",
        "requires_evidence": True,
    },
    "specialization": {
        "label": "specialization",
        "description": "The Lean clause is a special case of the source clause.",
        "requires_evidence": False,
    },
    "generalization": {
        "label": "generalization",
        "description": "The Lean clause is more general than the source clause.",
        "requires_evidence": False,
    },
    "stronger_than_source": {
        "label": "stronger than source",
        "description": "The Lean statement concludes more, or assumes less, than the source.",
        "requires_evidence": False,
    },
    "weaker_than_source": {
        "label": "weaker than source",
        "description": "The Lean statement concludes less, or assumes more, than the source.",
        "requires_evidence": False,
    },
    "inherited_standing_assumption": {
        "label": "inherited standing assumption",
        "description": "The Lean hypothesis realizes a condition imposed earlier in the source, "
                       "not one printed in this passage.",
        "requires_evidence": False,
    },
    "object_representation": {
        "label": "source object represented by",
        "description": "A source object is carried by a Lean definition.",
        "requires_evidence": False,
    },
    "refutation": {
        "label": "refuted",
        "description": "The Lean development refutes the printed clause; the row is terminal by "
                       "counterexample rather than by proof.",
        "requires_evidence": True,
    },
}

#: Clause roles, so a browser can group hypotheses apart from conclusions.
CLAUSE_KINDS = ("setup", "hypothesis", "conclusion", "scope", "object", "note")

#: How a row says it reads its source passage.
INTERPRETATIONS = {
    "local": "Every hypothesis and conclusion is printed in the cited passage.",
    "nonlocal": "The reading depends on source material outside the cited passage.",
}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _names(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence):
        return [str(x) for x in value if isinstance(x, str) and x.strip()]
    return []


@dataclass(frozen=True)
class CorrespondenceEdge:
    """One clause of a review, read as an edge from source to Lean."""

    index: int
    source_clause: str
    lean_realization: str
    status: str = "claimed_exact"
    relation: str = ""
    kind: str = ""
    source_fragment: str = ""
    source_excerpt: str = ""
    lean_declarations: tuple[str, ...] = ()
    lean_binder: str = ""
    correspondence_declarations: tuple[str, ...] = ()
    note: str = ""

    @classmethod
    def parse(cls, index: int, clause: Mapping[str, Any]) -> "CorrespondenceEdge":
        return cls(
            index=index,
            source_clause=_text(clause.get("source_clause")),
            lean_realization=_text(clause.get("lean_realization") or clause.get("lean_clause")),
            status=_text(clause.get("status")) or "claimed_exact",
            relation=_text(clause.get("relation")),
            kind=_text(clause.get("kind")),
            source_fragment=_text(clause.get("source_fragment")),
            source_excerpt=_text(clause.get("source_excerpt")),
            lean_declarations=tuple(_names(clause.get("lean_declarations"))),
            lean_binder=_text(clause.get("lean_binder")),
            correspondence_declarations=tuple(_names(clause.get("correspondence_declarations"))),
            note=_text(clause.get("note")),
        )

    @property
    def inherited(self) -> bool:
        return self.relation == "inherited_standing_assumption"

    def as_json(self) -> dict[str, Any]:
        out = {
            "index": self.index,
            "sourceClause": self.source_clause,
            "leanRealization": self.lean_realization,
            "status": self.status,
            "relation": self.relation,
            "relationLabel": RELATIONS.get(self.relation, {}).get("label", self.relation),
            "kind": self.kind,
            "sourceFragment": self.source_fragment,
            "sourceExcerpt": self.source_excerpt,
            "leanDeclarations": list(self.lean_declarations),
            "leanBinder": self.lean_binder,
            "correspondenceDeclarations": list(self.correspondence_declarations),
            "note": self.note,
        }
        return {k: v for k, v in out.items() if v not in ("", [], None)} | {"index": self.index}


def cited_declarations(review: Mapping[str, Any]) -> list[str]:
    """Every declaration a review's clauses name, registered or not."""
    out: list[str] = []
    for edge in edges_of(review):
        out.extend(edge.lean_declarations)
        out.extend(edge.correspondence_declarations)
    return list(dict.fromkeys(out))


def edges_of(review: Mapping[str, Any]) -> list[CorrespondenceEdge]:
    clauses = review.get("clause_map") or review.get("clauses") or []
    if not isinstance(clauses, Sequence):
        return []
    return [
        CorrespondenceEdge.parse(i, c)
        for i, c in enumerate(clauses)
        if isinstance(c, Mapping)
    ]


def declared_fragments(review: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = review.get("source_fragments")
    return [dict(x) for x in value if isinstance(x, Mapping)] if isinstance(value, Sequence) else []


def display_fragments(container: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The fragments a viewer should show, including the legacy single locator.

    Reviews written before this model carry one ``source_locator`` and no
    fragment list.  Rather than showing those rows nothing until every ledger is
    rewritten, the locator is read as an implicit primary fragment and marked as
    such, so a reviewer can tell a migrated row from one that still relies on the
    old shape.  ``aiq-lean alignment adopt-source`` turns it into a real one.
    """
    explicit = declared_fragments(container)
    if explicit:
        return explicit
    locator = container.get("source_locator")
    if not locator:
        return []
    return [{"id": "source", "locator": locator, "role": "primary", "implicit": True}]


#: TeX writes ``--`` and ``~`` where a reader sees an en dash and a space.  A
#: quote copied from the ``.tex`` and a quote copied from the rendered page must
#: both match, so both sides are folded to the same plain form.
_FOLD = str.maketrans({"\u2013": "-", "\u2014": "-", "\u00a0": " ", "~": " ",
                       "\u201c": '"', "\u201d": '"', "\u2019": "'"})


def _normalized(text: str) -> str:
    return " ".join(text.translate(_FOLD).replace("--", "-").split()).lower()


def validate_correspondence(
    review: Mapping[str, Any],
    *,
    location: str,
    declarations: Sequence[str] = (),
    relations: Mapping[str, Any] | None = None,
    fragment_text: Mapping[str, str] | None = None,
    check_status: bool = True,
) -> list[Finding]:
    """Check the structured half of a review's correspondence claim.

    ``fragment_text`` maps a declared fragment id to the resolved source text.
    When it is supplied, quoted excerpts are checked against it; when it is not
    -- a validator running without the source documents -- excerpt checking is
    skipped rather than guessed at.
    """
    findings: list[Finding] = []
    vocabulary = dict(RELATIONS)
    if isinstance(relations, Mapping):
        for key, value in relations.items():
            vocabulary.setdefault(str(key), {"label": str(key), "requires_evidence": False}
                                  if not isinstance(value, Mapping) else dict(value))
    fragments = declared_fragments(review)
    fragment_ids = {str(f.get("id")) for f in fragments if f.get("id")}
    fragment_roles = {str(f.get("id")): str(f.get("role") or "primary") for f in fragments}
    registered = set(declarations)

    for index, fragment in enumerate(fragments):
        loc = f"{location}.source_fragments[{index}]"
        fid = _text(fragment.get("id"))
        if not fid:
            findings.append(Finding("error", "source-fragment-id", "fragment needs an id", loc))
        if not fragment.get("locator"):
            findings.append(Finding("error", "source-fragment-locator",
                                    "fragment needs a source_locator", loc))
        role = str(fragment.get("role") or "primary")
        from .source_model import FRAGMENT_ROLES

        if role not in FRAGMENT_ROLES:
            findings.append(Finding("error", "source-fragment-role",
                                    f"unknown fragment role {role!r}; expected one of "
                                    f"{sorted(FRAGMENT_ROLES)}", loc))
    if fragments and not any(str(f.get("role") or "primary") == "primary" for f in fragments):
        findings.append(Finding("error", "source-fragment-primary",
                                "a review with source fragments needs exactly one primary passage",
                                location))

    interpretation = _text(review.get("source_interpretation"))
    if interpretation and interpretation not in INTERPRETATIONS:
        findings.append(Finding("error", "source-interpretation",
                                f"unknown source_interpretation {interpretation!r}", location))

    inherited_edges: list[CorrespondenceEdge] = []
    for edge in edges_of(review):
        loc = f"{location}.clause_map[{edge.index}]"
        if check_status and edge.status not in CLAUSE_STATUSES:
            findings.append(Finding("error", "semantic-clause-status",
                                    f"unknown clause status {edge.status!r}", loc))
        if edge.relation and edge.relation not in vocabulary:
            findings.append(Finding("error", "clause-relation",
                                    f"unknown relation {edge.relation!r}", loc))
        if edge.kind and edge.kind not in CLAUSE_KINDS:
            findings.append(Finding("error", "clause-kind",
                                    f"unknown clause kind {edge.kind!r}; expected one of "
                                    f"{list(CLAUSE_KINDS)}", loc))
        if edge.source_fragment and edge.source_fragment not in fragment_ids:
            findings.append(Finding("error", "clause-fragment",
                                    f"clause cites undeclared source fragment "
                                    f"{edge.source_fragment!r}", loc))
        spec = vocabulary.get(edge.relation) or {}
        if spec.get("requires_evidence") and not edge.correspondence_declarations:
            findings.append(Finding(
                "error", "clause-evidence",
                f"relation {edge.relation!r} asserts that another theorem carries the "
                "correspondence, so it must name that theorem in correspondence_declarations",
                loc,
            ))
        for name in edge.lean_declarations:
            if registered and name not in registered:
                findings.append(Finding(
                    "error", "clause-declaration",
                    f"{name} is cited as this clause's Lean realization but is not registered in "
                    "lean_declarations, so it is never elaborated or pinned",
                    loc,
                ))
        for name in edge.correspondence_declarations:
            # Correspondence lemmas are evidence *about* the row rather than
            # part of it, and a row should not have to enlarge its registered
            # surface to cite one.  It is still worth saying that an unregistered
            # one carries no statement pin.
            if registered and name not in registered:
                findings.append(Finding(
                    "warning", "clause-evidence-unregistered",
                    f"{name} carries this clause's correspondence but is not registered in "
                    "lean_declarations, so no statement pin protects it",
                    loc,
                ))
        if edge.source_excerpt and fragment_text is not None:
            body = fragment_text.get(edge.source_fragment or "")
            if body is not None and _normalized(edge.source_excerpt) not in _normalized(body):
                findings.append(Finding(
                    "error", "clause-excerpt",
                    "source_excerpt does not occur in the fragment it cites; the passage has "
                    "moved under the review",
                    loc,
                ))
        if edge.inherited or fragment_roles.get(edge.source_fragment) in {
            "standing_assumption", "convention"
        }:
            inherited_edges.append(edge)

    if inherited_edges:
        detail = ", ".join(f"clause {e.index}" for e in inherited_edges[:4])
        if interpretation == "local":
            findings.append(Finding(
                "error", "source-interpretation-local",
                f"the row reads its source passage as self-contained, but {detail} realizes a "
                "condition inherited from elsewhere in the source",
                location,
            ))
        elif not interpretation:
            findings.append(Finding(
                "warning", "source-interpretation-missing",
                f"{detail} realizes an inherited standing condition; the row should declare "
                "source_interpretation \"nonlocal\" and say why that reading is faithful",
                location,
            ))
        elif not _text(review.get("nonlocal_rationale")):
            findings.append(Finding(
                "warning", "source-interpretation-rationale",
                "a nonlocal reading needs a nonlocal_rationale a reviewer can disagree with",
                location,
            ))
    elif interpretation == "nonlocal":
        findings.append(Finding(
            "warning", "source-interpretation-nonlocal",
            "the row declares a nonlocal reading but cites no inherited source fragment",
            location,
        ))
    return findings


def relation_legend(relations: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The vocabulary a viewer shows, with any document-supplied additions."""
    out = {k: dict(v) for k, v in RELATIONS.items()}
    if isinstance(relations, Mapping):
        for key, value in relations.items():
            if isinstance(value, Mapping):
                out.setdefault(str(key), {}).update(value)
            else:
                out.setdefault(str(key), {"label": str(key), "description": str(value),
                                          "requires_evidence": False})
    return out

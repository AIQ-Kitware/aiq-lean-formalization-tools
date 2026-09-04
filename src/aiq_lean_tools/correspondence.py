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
#: ``requires_excerpt`` marks the ones a reviewer cannot check without seeing the
#: exact printed words: a clause that says the Lean statement changes the source's
#: representation, borrows a condition from elsewhere, leans on another theorem,
#: or refutes what is printed, is a claim *about a specific sentence*.  Naming the
#: passage is not enough -- the browser can jump to the passage but cannot mark
#: the statement under dispute, which is the one thing the reader came for.
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
        "requires_excerpt": True,
    },
    "representation_change": {
        "label": "representation change",
        "description": "The Lean statement uses a different representation of the same object; "
                       "a correspondence theorem must carry the relevant semantics.",
        "requires_evidence": True,
        "requires_excerpt": True,
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
        "requires_excerpt": True,
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
        "requires_excerpt": True,
    },
}

#: Clause roles, so a browser can group hypotheses apart from conclusions.
CLAUSE_KINDS = ("setup", "hypothesis", "conclusion", "scope", "object", "note")

#: Stable selectors into an elaborated theorem statement.  These are deliberately
#: structural pointers rather than copied Lean text.  Binder names and the final
#: result are exported by ``leanq statement``; ``binder_type_dep`` is useful for
#: implicit typeclass binders whose generated local names are less meaningful than
#: the constant in their type.  ``text`` is an explicit escape hatch for the rare
#: subterm that has no better structural address.
LEAN_TARGET_KINDS = {"binder", "binder_type_dep", "result", "declaration", "text"}

#: Stable selectors into a resolved source fragment.  ``math`` intentionally
#: points at a compact TeX token rather than copying a sentence or equation.
#: The fragment content hash remains the authority for drift; these selectors
#: only say which part of a current fragment the reviewer meant to focus.
#: ``excerpt`` is the structured form of the legacy ``source_excerpt`` field,
#: and ``fragment`` is useful when a whole inherited passage is the evidence.
SOURCE_TARGET_KINDS = {"math", "excerpt", "fragment"}


@dataclass(frozen=True)
class SourceTarget:
    """A reviewer's pointer to a visible part of a source fragment.

    Source prose does not have an elaborator, so the target model is deliberately
    smaller than :class:`LeanTarget`.  Math tokens are resolved against the parsed
    TeX spans produced by :mod:`source_model`; the source pin decides whether the
    fragment is still the one the review accepted.  ``occurrence`` is zero-based
    and only needed when the same token occurs repeatedly in one fragment.
    """

    kind: str
    fragment: str = ""
    text: str = ""
    occurrence: int = 0

    @classmethod
    def parse(cls, value: object) -> "SourceTarget":
        if not isinstance(value, Mapping):
            return cls(kind="")
        raw_occurrence = value.get("occurrence", 0)
        try:
            occurrence = int(raw_occurrence)
        except (TypeError, ValueError):
            occurrence = -1
        return cls(
            kind=_text(value.get("kind")),
            fragment=_text(value.get("fragment")),
            text=_text(value.get("text") or value.get("excerpt")),
            occurrence=occurrence,
        )

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.fragment:
            out["fragment"] = self.fragment
        if self.text:
            out["text"] = self.text
        if self.occurrence:
            out["occurrence"] = self.occurrence
        return out


@dataclass(frozen=True)
class LeanTarget:
    """A reviewer's pointer to the part of a Lean statement realizing a clause.

    The pointer intentionally does not duplicate the theorem text.  The statement
    sidecar supplies the current elaborated binder/result text, while statement pins
    say whether the declaration is still the one the review accepted.
    """

    kind: str
    declaration: str = ""
    name: str = ""
    constant: str = ""
    excerpt: str = ""

    @classmethod
    def parse(cls, value: object) -> "LeanTarget":
        if isinstance(value, str):
            # A compact shorthand is convenient for the common case and is
            # backwards-compatible with the old single ``lean_binder`` field.
            return cls(kind="binder", name=value.strip())
        if not isinstance(value, Mapping):
            return cls(kind="")
        return cls(
            kind=_text(value.get("kind")),
            declaration=_text(value.get("declaration")),
            name=_text(value.get("name")),
            constant=_text(value.get("constant")),
            excerpt=_text(value.get("excerpt")),
        )

    def as_json(self) -> dict[str, Any]:
        out = {
            "kind": self.kind,
            "declaration": self.declaration,
            "name": self.name,
            "constant": self.constant,
            "excerpt": self.excerpt,
        }
        return {k: v for k, v in out.items() if v}


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
    source_targets: tuple[SourceTarget, ...] = ()
    lean_declarations: tuple[str, ...] = ()
    lean_binder: str = ""
    lean_targets: tuple[LeanTarget, ...] = ()
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
            source_targets=tuple(
                SourceTarget.parse(value)
                for value in (clause.get("source_targets") or ())
            ) if isinstance(clause.get("source_targets"), Sequence)
               and not isinstance(clause.get("source_targets"), (str, bytes)) else (),
            lean_declarations=tuple(_names(clause.get("lean_declarations"))),
            lean_binder=_text(clause.get("lean_binder")),
            lean_targets=tuple(
                LeanTarget.parse(value)
                for value in (clause.get("lean_targets") or ())
            ) if isinstance(clause.get("lean_targets"), Sequence)
               and not isinstance(clause.get("lean_targets"), (str, bytes)) else (),
            correspondence_declarations=tuple(_names(clause.get("correspondence_declarations"))),
            note=_text(clause.get("note")),
        )

    @property
    def inherited(self) -> bool:
        return self.relation == "inherited_standing_assumption"

    @property
    def effective_source_targets(self) -> tuple[SourceTarget, ...]:
        """Structured source targets, with the legacy excerpt as a shorthand."""
        if self.source_targets:
            return self.source_targets
        if self.source_excerpt:
            return (SourceTarget(kind="excerpt", fragment=self.source_fragment,
                                 text=self.source_excerpt),)
        return ()

    @property
    def effective_lean_targets(self) -> tuple[LeanTarget, ...]:
        """Structured targets, with the legacy binder field as a shorthand."""
        if self.lean_targets:
            return self.lean_targets
        if self.lean_binder:
            return (LeanTarget(kind="binder", name=self.lean_binder),)
        return ()

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
            "sourceTargets": [target.as_json() for target in self.effective_source_targets],
            "leanDeclarations": list(self.lean_declarations),
            "leanBinder": self.lean_binder,
            "leanTargets": [target.as_json() for target in self.effective_lean_targets],
            "correspondenceDeclarations": list(self.correspondence_declarations),
            "note": self.note,
        }
        return {k: v for k, v in out.items() if v not in ("", [], None)} | {"index": self.index}


def cited_declarations(review: Mapping[str, Any]) -> list[str]:
    """Every declaration a review's clauses name, registered or not."""
    out: list[str] = []
    for edge in edges_of(review):
        out.extend(edge.lean_declarations)
        out.extend(
            target.declaration for target in edge.effective_lean_targets
            if target.declaration
        )
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


def _record_value(record: object, name: str, default: object = None) -> object:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _binder_value(binder: object, name: str, default: object = None) -> object:
    if isinstance(binder, Mapping):
        return binder.get(name, default)
    return getattr(binder, name, default)


def _target_declaration(
    edge: CorrespondenceEdge, target: LeanTarget, review: Mapping[str, Any]
) -> tuple[str, str]:
    """Return ``(declaration, state)`` for one structured target.

    Omitting ``declaration`` is intentionally allowed when the clause has exactly
    one Lean realization or the review exactly one canonical declaration.  That
    removes the most common piece of redundant ledger text while refusing to guess
    when either side is genuinely ambiguous.
    """
    if target.declaration:
        return target.declaration, "current"
    if len(edge.lean_declarations) == 1:
        return edge.lean_declarations[0], "current"
    canonical = _names(review.get("canonical_declarations") or review.get("declarations"))
    if len(canonical) == 1:
        return canonical[0], "current"
    return "", "ambiguous-declaration"


def resolve_lean_targets(
    edge: CorrespondenceEdge, review: Mapping[str, Any], statements: Mapping[str, object]
) -> list[dict[str, Any]]:
    """Resolve clause target selectors against a fresh elaborated statement sidecar.

    The returned states are UI- and checker-facing evidence, not semantic verdicts.
    A ``current`` target means only that the reviewed structural pointer still
    names a part of the current declaration.  The declaration's statement pin is
    checked separately.
    """
    out: list[dict[str, Any]] = []
    for target in edge.effective_lean_targets:
        item = target.as_json()
        declaration, state = _target_declaration(edge, target, review)
        item["declaration"] = declaration
        if state != "current":
            item["state"] = state
            item["matches"] = []
            out.append(item)
            continue
        record = statements.get(declaration)
        if record is None or bool(_record_value(record, "missing", False)):
            item["state"] = "declaration-missing"
            item["matches"] = []
            out.append(item)
            continue

        matches: list[dict[str, Any]] = []
        if target.kind == "declaration":
            matches = [{"kind": "declaration"}]
        elif target.kind == "result":
            result = str(_record_value(record, "result", "") or "")
            if result:
                matches = [{"kind": "result"}]
            else:
                state = "shape-unavailable"
        elif target.kind in {"binder", "binder_type_dep"}:
            binders = tuple(_record_value(record, "binders", ()) or ())
            if not binders:
                state = "shape-unavailable"
            elif target.kind == "binder":
                matches = [
                    {
                        "kind": "binder",
                        "index": int(_binder_value(b, "index", i) or 0),
                        "name": str(_binder_value(b, "name", "") or ""),
                    }
                    for i, b in enumerate(binders)
                    if str(_binder_value(b, "name", "") or "") == target.name
                ]
                if not matches:
                    state = "target-missing"
            else:
                matches = [
                    {
                        "kind": "binder",
                        "index": int(_binder_value(b, "index", i) or 0),
                        "name": str(_binder_value(b, "name", "") or ""),
                    }
                    for i, b in enumerate(binders)
                    if target.constant in tuple(_binder_value(b, "type_deps", ()) or ())
                ]
                if not matches:
                    state = "target-missing"
        elif target.kind == "text":
            signature = str(_record_value(record, "signature", "") or _record_value(record, "type", "") or "")
            if target.excerpt and _normalized(target.excerpt) in _normalized(signature):
                matches = [{"kind": "text", "excerpt": target.excerpt}]
            else:
                state = "target-missing"
        else:
            state = "invalid-target"

        item["state"] = state if state != "current" or matches else "target-missing"
        item["matches"] = matches
        out.append(item)
    return out


def validate_lean_targets(
    review: Mapping[str, Any],
    *,
    statements: Mapping[str, object],
    location: str = "review",
) -> list[Finding]:
    """Check that each structured Lean pointer still resolves after elaboration."""
    findings: list[Finding] = []
    for edge in edges_of(review):
        for i, resolved in enumerate(resolve_lean_targets(edge, review, statements)):
            state = str(resolved.get("state") or "")
            if state == "current":
                continue
            loc = f"{location}.clause_map[{edge.index}].lean_targets[{i}]"
            declaration = resolved.get("declaration") or "<ambiguous declaration>"
            target = resolved.get("name") or resolved.get("constant") or resolved.get("kind")
            if state == "shape-unavailable":
                message = (
                    f"the current statement sidecar for {declaration} has no structured "
                    f"binder/result shape, so Lean target {target!r} cannot be checked; "
                    "regenerate the statement sidecar with the current leanq exporter"
                )
            elif state == "ambiguous-declaration":
                message = (
                    f"Lean target {target!r} does not name a declaration and the clause/review "
                    "does not determine exactly one; add an explicit declaration"
                )
            elif state == "declaration-missing":
                message = f"Lean target {target!r} points at missing declaration {declaration}"
            elif state == "invalid-target":
                message = f"unknown Lean target kind {resolved.get('kind')!r}"
            else:
                message = (
                    f"Lean target {target!r} no longer resolves inside {declaration}; "
                    "the reviewed clause pointer has drifted"
                )
            findings.append(Finding("error", "lean-target-drift", message, loc))
    return findings


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
    primaries = [f for f in fragments if str(f.get("role") or "primary") == "primary"]
    if fragments and len(primaries) != 1:
        # "Exactly one" is the whole content of the rule: a row with two primary
        # passages has not said which passage its statement is, and every source
        # pin and drift check downstream assumes it has.
        findings.append(Finding("error", "source-fragment-primary",
                                f"a review with source fragments needs exactly one primary "
                                f"passage; this one declares {len(primaries)}",
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
        if (spec.get("requires_excerpt") or edge.status == "open") and not edge.source_excerpt:
            what = (f"relation {edge.relation!r}" if spec.get("requires_excerpt")
                    else "an unestablished correspondence")
            findings.append(Finding(
                "error", "clause-excerpt-required",
                f"{what} is a claim about specific printed words, so the clause must quote them "
                "in source_excerpt; without one a reader can reach the passage but not the "
                "sentence under dispute",
                loc,
            ))
        if edge.source_excerpt and not edge.source_fragment and fragment_ids:
            findings.append(Finding(
                "warning", "clause-excerpt-fragment",
                "the clause quotes the source but does not say which fragment it quotes, so the "
                "quote is never checked against the passage",
                loc,
            ))
        if spec.get("requires_evidence") and not edge.correspondence_declarations:
            findings.append(Finding(
                "error", "clause-evidence",
                f"relation {edge.relation!r} asserts that another theorem carries the "
                "correspondence, so it must name that theorem in correspondence_declarations",
                loc,
            ))
        raw_source_targets = edge.effective_source_targets
        for target_index, target in enumerate(raw_source_targets):
            tloc = f"{loc}.source_targets[{target_index}]"
            if target.kind not in SOURCE_TARGET_KINDS:
                findings.append(Finding(
                    "error", "source-target-kind",
                    f"unknown source target kind {target.kind!r}; expected one of "
                    f"{sorted(SOURCE_TARGET_KINDS)}", tloc,
                ))
                continue
            fragment = target.fragment or edge.source_fragment
            if not fragment:
                findings.append(Finding(
                    "error", "source-target-fragment",
                    "source target needs a fragment, directly or through source_fragment", tloc,
                ))
            elif fragment_ids and fragment not in fragment_ids:
                findings.append(Finding(
                    "error", "source-target-fragment",
                    f"source target cites undeclared source fragment {fragment!r}", tloc,
                ))
            if target.kind in {"math", "excerpt"} and not target.text:
                findings.append(Finding(
                    "error", "source-target-text",
                    f"{target.kind} source target needs non-empty text", tloc,
                ))
            if target.occurrence < 0:
                findings.append(Finding(
                    "error", "source-target-occurrence",
                    "source target occurrence must be a non-negative integer", tloc,
                ))

        raw_targets = edge.effective_lean_targets
        for target_index, target in enumerate(raw_targets):
            tloc = f"{loc}.lean_targets[{target_index}]"
            if target.kind not in LEAN_TARGET_KINDS:
                findings.append(Finding(
                    "error", "lean-target-kind",
                    f"unknown Lean target kind {target.kind!r}; expected one of "
                    f"{sorted(LEAN_TARGET_KINDS)}", tloc,
                ))
                continue
            if target.kind == "binder" and not target.name:
                findings.append(Finding("error", "lean-target-name",
                                        "binder target needs a non-empty name", tloc))
            if target.kind == "binder_type_dep" and not target.constant:
                findings.append(Finding("error", "lean-target-constant",
                                        "binder_type_dep target needs a constant", tloc))
            if target.kind == "text" and not target.excerpt:
                findings.append(Finding("error", "lean-target-excerpt",
                                        "text target needs an excerpt", tloc))
            if target.declaration and registered and target.declaration not in registered:
                findings.append(Finding(
                    "error", "lean-target-declaration",
                    f"{target.declaration} is an explicit Lean target but is not registered in "
                    "lean_declarations, so it is never elaborated or pinned", tloc,
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

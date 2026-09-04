"""Validation helpers for embedded semantic-review surfaces.

The first source-census workflow used a compact review surface embedded directly
in selected census rows.  A separate semantic-review document is also supported
by this package, but the embedded form is useful when the source census itself
is the review contract.

This module preserves the stronger DK/YWS invariants without tying them to a
particular paper or namespace.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .common import Finding
from .correspondence import CLAUSE_STATUSES, validate_correspondence
from .source_pins import validate_source_pins

IMPORTANCE_ORDER = {
    "headline": 0,
    "major": 1,
    "supporting": 2,
    "technical": 3,
}

#: What a presentation declaration claims about the canonical statement it
#: fronts.  Closed, because the point of the field is that a reader can tell at a
#: glance whether the legible restatement is the same theorem; a reviewer with
#: something else to say has ``why`` for it.
PRESENTATION_RELATIONS = {
    "equivalent": "The same theorem, restated so the signature reads like the printed one.",
    "specialization": "States strictly less than the canonical declaration it fronts.",
    "notation": "The identical statement, differing only by notation or a reducible abbreviation.",
    "unstated": "Nothing is claimed; the entry records only the name.",
}


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _finding(findings: list[Finding], code: str, message: str, location: str) -> None:
    findings.append(Finding("error", code, message, location))


def _validate_source_statement(
    statement: object,
    *,
    location: str,
    findings: list[Finding],
) -> None:
    if not isinstance(statement, Mapping):
        _finding(findings, "semantic-source-statement", "source_statement must be an object", location)
        return
    for key in ("setup", "hypotheses", "conclusions", "scope"):
        value = statement.get(key)
        subloc = f"{location}.{key}"
        if not isinstance(value, list):
            _finding(findings, "semantic-source-statement", f"{key} must be a list", subloc)
            continue
        if any(not _nonempty_string(item) for item in value):
            _finding(findings, "semantic-source-statement", f"{key} must contain non-empty strings", subloc)
    hypotheses = statement.get("hypotheses")
    conclusions = statement.get("conclusions")
    if isinstance(hypotheses, list) and not hypotheses:
        _finding(findings, "semantic-source-hypotheses", "source_statement needs at least one hypothesis", location)
    if isinstance(conclusions, list) and not conclusions:
        _finding(findings, "semantic-source-conclusions", "source_statement needs at least one conclusion", location)


def _validate_context(
    context: object,
    *,
    location: str,
    findings: list[Finding],
) -> None:
    if not isinstance(context, list):
        _finding(findings, "semantic-context", "context_declarations must be a list", location)
        return
    for index, entry in enumerate(context):
        loc = f"{location}[{index}]"
        if not isinstance(entry, Mapping):
            _finding(findings, "semantic-context", "context declaration must be an object", loc)
            continue
        if not _nonempty_string(entry.get("name")):
            _finding(findings, "semantic-context-name", "context declaration name must be non-empty", loc)
        if not _nonempty_string(entry.get("mathematical_role")):
            _finding(
                findings,
                "semantic-context-role",
                "context declaration mathematical_role must be non-empty",
                loc,
            )


def _validate_clause_map(
    mapping: object,
    *,
    location: str,
    findings: list[Finding],
) -> None:
    if not isinstance(mapping, list) or not mapping:
        _finding(findings, "semantic-clause-map", "clause_map must be a non-empty list", location)
        return
    for index, entry in enumerate(mapping):
        loc = f"{location}[{index}]"
        if not isinstance(entry, Mapping):
            _finding(findings, "semantic-clause-map", "clause-map entry must be an object", loc)
            continue
        for key in ("source_clause", "lean_realization"):
            if not _nonempty_string(entry.get(key)):
                _finding(findings, f"semantic-{key}", f"{key} must be non-empty", loc)
        status = entry.get("status", "claimed_exact")
        if status not in CLAUSE_STATUSES:
            _finding(
                findings,
                "semantic-clause-status",
                f"unknown clause status {status!r}; expected one of {sorted(CLAUSE_STATUSES)}",
                loc,
            )


def _validate_declaration_list(
    value: object,
    *,
    field: str,
    location: str,
    findings: list[Finding],
    require_nonempty: bool,
) -> list[str]:
    if not isinstance(value, list):
        _finding(findings, "semantic-declarations", f"{field} must be a list", location)
        return []
    if require_nonempty and not value:
        _finding(findings, "semantic-declarations", f"{field} must be non-empty", location)
    if any(not _nonempty_string(item) for item in value):
        _finding(findings, "semantic-declarations", f"{field} must contain non-empty strings", location)
    return [str(item) for item in value if _nonempty_string(item)]


def _normalized_presentation_entry(entry: object) -> dict[str, Any] | None:
    """One presentation entry in object form, or ``None`` when it names nothing."""
    if _nonempty_string(entry):
        name = str(entry).strip()
        fronts: list[str] = []
        relation: Any = "unstated"
        devices: list[str] = []
        why = ""
    elif isinstance(entry, Mapping) and _nonempty_string(entry.get("name")):
        name = str(entry["name"]).strip()
        raw_fronts = entry.get("fronts")
        fronts = [str(x).strip() for x in raw_fronts if _nonempty_string(x)] if isinstance(raw_fronts, list) else []
        relation = entry.get("relation") if _nonempty_string(entry.get("relation")) else "unstated"
        raw_devices = entry.get("devices")
        devices = [str(x).strip() for x in raw_devices if _nonempty_string(x)] if isinstance(raw_devices, list) else []
        why = str(entry.get("why") or "").strip()
    else:
        return None
    return {"name": name, "fronts": fronts, "relation": relation, "devices": devices, "why": why}


def normalize_presentation(review: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The review's ``presentation_declarations``, in normalized object form.

    A presentation declaration is a thin restatement of a canonical theorem whose
    signature reads like the printed result -- the paper's operator named as an
    explicit argument with a defining hypothesis, or local notation standing for
    it -- so that the ledger records the statement a reader is shown rather than
    only the one the library exports.  Each entry becomes
    ``{"name", "fronts", "relation", "devices", "why"}``.

    A bare string is legal shorthand for a name with nothing claimed about it and
    expands with ``relation`` ``unstated``.  An unrecognized ``relation`` is
    returned as written: the reviewer's word is what a reader must see, and
    :func:`validate_embedded_review` reports it.  Entries naming nothing are
    dropped here and reported there.

    The review is not modified.
    """
    entries = review.get("presentation_declarations") if isinstance(review, Mapping) else None
    if not isinstance(entries, list):
        return []
    normalized = (_normalized_presentation_entry(entry) for entry in entries)
    return [entry for entry in normalized if entry is not None]


def _validate_presentation(
    value: object,
    *,
    location: str,
    registered: set[str],
    findings: list[Finding],
) -> list[str]:
    if not isinstance(value, list):
        _finding(findings, "semantic-presentation", "presentation_declarations must be a list", location)
        return []
    names: list[str] = []
    for index, entry in enumerate(value):
        loc = f"{location}[{index}]"
        normalized = _normalized_presentation_entry(entry)
        if normalized is None:
            _finding(
                findings,
                "semantic-presentation",
                "presentation declaration must be a name or an object carrying a non-empty name",
                loc,
            )
            continue
        names.append(normalized["name"])
        relation = normalized["relation"]
        if relation not in PRESENTATION_RELATIONS:
            _finding(
                findings,
                "semantic-presentation-relation",
                f"unknown presentation relation {relation!r}; expected one of {sorted(PRESENTATION_RELATIONS)}",
                loc,
            )
        if relation != "unstated" and not normalized["why"]:
            _finding(
                findings,
                "semantic-presentation-why",
                "a presentation declaration claiming a relation must say what the restatement buys and costs",
                loc,
            )
        for front in normalized["fronts"]:
            if front not in registered:
                # Not an error: the fronted theorem may live upstream and resolve
                # perfectly well.  But a row that shows a front for a statement it
                # does not otherwise register leaves the fronted theorem unreviewed.
                findings.append(
                    Finding(
                        "warning",
                        "semantic-presentation-fronts",
                        f"presentation form fronts {front}, which this row registers "
                        "neither as canonical nor as supporting",
                        loc,
                    )
                )
    return names


def validate_embedded_review(
    review: object,
    *,
    row_location: str,
    census_declarations: Sequence[str],
    findings: list[Finding],
    require_group_fields: bool = True,
    allow_external_declarations: bool = False,
    strict: bool = True,
) -> None:
    """Validate one curated semantic-review object.

    Args:
        review: Review mapping to validate.
        row_location: Human-readable location for findings.
        census_declarations: Declarations registered on the parent census row.
        findings: Destination finding list.
        require_group_fields: Require the grouping fields used by primary rows.
        allow_external_declarations: Derived variants may intentionally cite a
            declaration outside the parent row's declaration list.
        strict: Require the full curated contract -- grouping fields, a
            normalized source statement, canonical declarations, a clause map.
            A lighter review that records only which declarations a row cites is
            still checked for what it does contain.
    """
    if not isinstance(review, Mapping):
        _finding(findings, "semantic-review", "semantic_review must be an object", row_location)
        return

    if require_group_fields:
        required = ("group", "group_title", "claim")
    else:
        required = ("id", "title", "claim", "provenance_note")
    if strict:
        for key in required:
            if not _nonempty_string(review.get(key)):
                _finding(findings, f"semantic-{key}", f"{key} must be non-empty", row_location)

    if strict or review.get("source_statement") is not None:
        _validate_source_statement(
            review.get("source_statement"),
            location=f"{row_location}.source_statement",
            findings=findings,
        )
    canonical = _validate_declaration_list(
        review.get("canonical_declarations", review.get("declarations", [])),
        field="canonical_declarations",
        location=f"{row_location}.canonical_declarations",
        findings=findings,
        require_nonempty=strict,
    )
    supporting = _validate_declaration_list(
        review.get("supporting_declarations", []),
        field="supporting_declarations",
        location=f"{row_location}.supporting_declarations",
        findings=findings,
        require_nonempty=False,
    )
    presentation = _validate_presentation(
        review.get("presentation_declarations", []),
        location=f"{row_location}.presentation_declarations",
        registered={*canonical, *supporting},
        findings=findings,
    )
    if not allow_external_declarations:
        registered = set(census_declarations)
        missing = [name for name in [*canonical, *supporting, *presentation] if name not in registered]
        if missing:
            _finding(
                findings,
                "semantic-review-declaration",
                "semantic-review declarations must also be registered in lean_declarations: "
                + ", ".join(missing),
                row_location,
            )
    _validate_context(
        review.get("context_declarations", []),
        location=f"{row_location}.context_declarations",
        findings=findings,
    )
    if strict or review.get("clause_map") is not None:
        _validate_clause_map(
            review.get("clause_map"),
            location=f"{row_location}.clause_map",
            findings=findings,
        )
    # The structured half: which source fragment each clause came from, what
    # relation it claims, and whether a row reading its passage as
    # self-contained quietly cites a condition inherited from elsewhere.
    # A presentation form is registered like any other declaration, so a clause
    # may realize itself through one and a pin may protect it.
    findings.extend(
        validate_correspondence(
            review,
            location=row_location,
            declarations=[*canonical, *supporting, *presentation],
            check_status=False,
        )
    )
    from .statement_pins import validate_pins

    findings.extend(
        validate_pins(
            review, claimed=[*canonical, *supporting, *presentation], location=row_location
        )
    )
    findings.extend(validate_source_pins(review, location=row_location))


def validate_embedded_surface(
    row: Mapping[str, Any],
    *,
    row_location: str,
    require_headline_review: bool,
) -> list[Finding]:
    """Validate embedded primary and derived semantic-review surfaces."""
    findings: list[Finding] = []
    decls = row.get("lean_declarations", [])
    census_declarations = [str(x) for x in decls] if isinstance(decls, list) else []

    review = row.get("semantic_review")
    # A review that is present is validated whether or not one was *required*.
    # Only headline rows are obliged to carry the embedded contract, and for a
    # while that meant a review written on a lower-importance row -- Theorem 8.2,
    # Proposition 4.4 -- was accepted without being checked at all.
    #
    # The full contract still applies only where the review claims it: a review
    # that asserts a clause-by-clause correspondence is held to the whole
    # surface, while one that only records which declarations a row cites is
    # checked for what it contains.
    if require_headline_review or isinstance(review, Mapping):
        validate_embedded_review(
            review,
            row_location=f"{row_location}.semantic_review",
            census_declarations=census_declarations,
            findings=findings,
            strict=require_headline_review or bool(
                isinstance(review, Mapping) and review.get("clause_map")
            ),
        )

    variants = row.get("semantic_review_variants", [])
    if not isinstance(variants, list):
        _finding(
            findings,
            "semantic-review-variants",
            "semantic_review_variants must be a list",
            row_location,
        )
        return findings
    for index, variant in enumerate(variants):
        validate_embedded_review(
            variant,
            row_location=f"{row_location}.semantic_review_variants[{index}]",
            census_declarations=census_declarations,
            findings=findings,
            require_group_fields=False,
            allow_external_declarations=True,
        )
    return findings

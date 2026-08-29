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

IMPORTANCE_ORDER = {
    "headline": 0,
    "major": 1,
    "supporting": 2,
    "technical": 3,
}

CLAUSE_STATUSES = {"claimed_exact", "derived", "scope_companion"}


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


def validate_embedded_review(
    review: object,
    *,
    row_location: str,
    census_declarations: Sequence[str],
    findings: list[Finding],
    require_group_fields: bool = True,
    allow_external_declarations: bool = False,
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
    """
    if not isinstance(review, Mapping):
        _finding(findings, "semantic-review", "semantic_review must be an object", row_location)
        return

    if require_group_fields:
        required = ("group", "group_title", "claim")
    else:
        required = ("id", "title", "claim", "provenance_note")
    for key in required:
        if not _nonempty_string(review.get(key)):
            _finding(findings, f"semantic-{key}", f"{key} must be non-empty", row_location)

    _validate_source_statement(
        review.get("source_statement"),
        location=f"{row_location}.source_statement",
        findings=findings,
    )
    canonical = _validate_declaration_list(
        review.get("canonical_declarations"),
        field="canonical_declarations",
        location=f"{row_location}.canonical_declarations",
        findings=findings,
        require_nonempty=True,
    )
    supporting = _validate_declaration_list(
        review.get("supporting_declarations", []),
        field="supporting_declarations",
        location=f"{row_location}.supporting_declarations",
        findings=findings,
        require_nonempty=False,
    )
    if not allow_external_declarations:
        registered = set(census_declarations)
        missing = [name for name in [*canonical, *supporting] if name not in registered]
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
    _validate_clause_map(
        review.get("clause_map"),
        location=f"{row_location}.clause_map",
        findings=findings,
    )


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
    if require_headline_review:
        validate_embedded_review(
            review,
            row_location=f"{row_location}.semantic_review",
            census_declarations=census_declarations,
            findings=findings,
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

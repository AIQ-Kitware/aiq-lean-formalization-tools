"""A census row's semantic review, when the review lives in its own document.

Two shapes are in use, and only one of them was readable. Davis--Kahan and
Yu--Wang--Samworth embed a ``semantic_review`` in each census row. The four DKPS
application papers instead keep a standalone
``*-result-semantic-review.json`` beside the census, declaring the census it
belongs to in ``companion_census`` and matching it row for row. That second
shape is not a lesser one -- it carries the same claims, the same clause-by-clause
correspondence and the same source locators -- but the alignment packet only
looked for the embedded field, so forty-three reviewed headline and major
results rendered as if nobody had reviewed them.

This module resolves the second shape into the first. It is a translation, not a
judgement: field names differ, the content does not.

The one place a judgement would be tempting is the clause verdict. An embedded
clause carries ``status`` (``claimed_exact`` and friends); a standalone clause
carries ``relation`` from its document's own ``relation_definitions``. They are
different vocabularies answering different questions, so no ``status`` is
invented here. A consumer that counts unestablished clauses must count only what
some reviewer actually marked, and read an absent status as unstated rather than
as open.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

#: Where a standalone review names the census it reviews.
COMPANION_FIELD = "companion_census"

#: Filenames that could hold one, kept broad; `companion_census` decides.
REVIEW_GLOB = "*semantic-review*.json"


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def companion_review_paths(census_path: Path, root: Path | None = None) -> list[Path]:
    """Standalone reviews that declare ``census_path`` as their companion.

    Matched by resolved path rather than by filename, so a review is found
    however it spells the census -- repository-relative, as the four current ones
    do, or relative to itself.
    """
    census_path = Path(census_path).resolve()
    out: list[Path] = []
    for candidate in sorted(census_path.parent.glob(REVIEW_GLOB)):
        data = _load(candidate)
        if data is None:
            continue
        declared = data.get(COMPANION_FIELD)
        if not isinstance(declared, str) or not declared:
            continue
        for base in filter(None, (root, census_path.parent.parent, candidate.parent)):
            if (Path(base) / declared).resolve() == census_path:
                out.append(candidate)
                break
    return out


def companion_reviews(census_path: Path, root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Row id -> an embedded-shaped review, from the census's companion documents."""
    out: dict[str, dict[str, Any]] = {}
    for path in companion_review_paths(census_path, root):
        data = _load(path) or {}
        for index, row in enumerate(data.get("rows") or []):
            if not isinstance(row, dict):
                continue
            row_id = str(row.get("id") or "")
            if row_id and row_id not in out:
                review = normalize_review(row, document=data)
                # Where the review actually lives, so a reviewer's annotation is
                # written to the document that holds the field and not to the
                # census, which has no such field to write.
                review["companion_document"] = path.name
                review["companion_row"] = index
                out[row_id] = review
    return out


def normalize_review(row: Mapping[str, Any], *, document: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One standalone review row, in the shape an embedded review has."""
    declarations = [d for d in row.get("lean_declarations") or [] if isinstance(d, str)]
    review: dict[str, Any] = {
        "group": row.get("id"),
        "group_title": row.get("title") or row.get("id"),
        "claim": row.get("source_claim") or row.get("title") or "",
        "canonical_declarations": declarations[:1],
        "supporting_declarations": declarations[1:],
        "context_declarations": [],
        "clause_map": [_clause(c) for c in row.get("clauses") or [] if isinstance(c, Mapping)],
        "source_fragments": _fragments(row),
        # The reviewer's prose about the correspondence, and their notes about
        # what remains, are two different things and stay two different fields.
        # Neither is `source_interpretation`: in an embedded review that field is
        # an enum -- `local` or `nonlocal` -- and putting prose in it would read
        # as a value the viewer tests for.
        "reviewer_note": str(row.get("review") or ""),
        "note": str(row.get("notes") or ""),
    }
    for field in ("statement_pins", "source_pins", "source_statement", "source_fragments"):
        value = row.get(field)
        if value:
            review[field] = value
    # The row-level judgement a standalone review carries and an embedded one has
    # no place for. Dropping it would lose the most pointed thing on the row.
    for field in ("verdict", "literal_source_covered", "gap_refs"):
        if row.get(field) is not None:
            review[field] = row[field]
    review["companion"] = True
    if document is not None and document.get("relation_definitions"):
        review["relation_definitions"] = document["relation_definitions"]
    return review


def _clause(clause: Mapping[str, Any]) -> dict[str, Any]:
    out = {
        "source_clause": str(clause.get("source_clause") or ""),
        "lean_realization": str(clause.get("lean_clause") or clause.get("lean_realization") or ""),
        "relation": str(clause.get("relation") or ""),
        "source_fragment": "printed",
    }
    for field in (
        "status", "kind", "source_excerpt", "lean_binder", "lean_targets",
        "lean_declarations", "correspondence_declarations", "note",
    ):
        if clause.get(field):
            out[field] = clause[field]
    return out


def _fragments(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The row's declared fragments, or its single locator read as one."""
    declared = row.get("source_fragments")
    if isinstance(declared, list) and declared:
        return [dict(f) for f in declared if isinstance(f, Mapping)]
    locator = row.get("source_locator")
    if not isinstance(locator, Mapping) or not locator:
        return []
    fragment = {
        "id": "printed",
        "role": "primary",
        "locator": dict(locator),
    }
    anchor = row.get("source_anchor")
    if anchor and not fragment["locator"].get("result"):
        fragment["locator"]["result"] = str(anchor)
    return [fragment]


def merge_into(rows: Iterable[Mapping[str, Any]], reviews: Mapping[str, dict[str, Any]]) -> int:
    """Count how many of ``rows`` a companion review would answer for."""
    return sum(1 for row in rows
               if not isinstance(row.get("semantic_review"), dict)
               and str(row.get("id") or "") in reviews)

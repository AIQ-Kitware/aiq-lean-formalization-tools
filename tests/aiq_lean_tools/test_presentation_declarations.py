"""The presentation-declaration surface: a legible restatement of a canonical theorem."""
from __future__ import annotations

from aiq_lean_tools.semantic_surface import (
    normalize_presentation,
    validate_embedded_surface,
)
from aiq_lean_tools.server.xref import Xref
from aiq_lean_tools.statement_pins import claimed_pin_declarations

FULL_ENTRY = {
    "name": "Paper.presented",
    "fronts": ["Paper.canonical"],
    "relation": "specialization",
    "devices": ["named auxiliary with a defining hypothesis"],
    "why": "The printed operator is an explicit argument, so the conclusion is the printed "
           "inequality; the separation hypothesis is only one branch of the canonical gap.",
}


def _row(presentation, *, registered=("Paper.canonical", "Paper.helper", "Paper.presented")):
    return {
        "lean_declarations": list(registered),
        "semantic_review": {
            "canonical_declarations": ["Paper.canonical"],
            "supporting_declarations": ["Paper.helper"],
            "presentation_declarations": presentation,
        },
    }


def _findings(row):
    return [
        (f.level, f.code)
        for f in validate_embedded_surface(row, row_location="r1", require_headline_review=False)
    ]


def test_bare_string_is_shorthand_for_a_name_with_nothing_claimed():
    review = {"presentation_declarations": ["Paper.presented", {"name": "Paper.other"}]}
    assert normalize_presentation(review) == [
        {"name": "Paper.presented", "fronts": [], "relation": "unstated", "devices": [], "why": ""},
        {"name": "Paper.other", "fronts": [], "relation": "unstated", "devices": [], "why": ""},
    ]
    assert _findings(_row(["Paper.presented"])) == []


def test_normalization_fills_defaults_and_leaves_an_unknown_relation_as_written():
    entry = {"name": "Paper.presented", "relation": "thin_wrapper", "why": "  spaced  "}
    assert normalize_presentation({"presentation_declarations": [entry]}) == [
        {"name": "Paper.presented", "fronts": [], "relation": "thin_wrapper",
         "devices": [], "why": "spaced"},
    ]
    # Side-effect free: the reviewer's own object is untouched.
    assert entry == {"name": "Paper.presented", "relation": "thin_wrapper", "why": "  spaced  "}
    assert normalize_presentation({}) == []
    assert normalize_presentation({"presentation_declarations": "Paper.presented"}) == []


def test_a_well_formed_presentation_entry_is_accepted():
    assert _findings(_row([FULL_ENTRY])) == []
    assert normalize_presentation(_row([FULL_ENTRY])["semantic_review"]) == [FULL_ENTRY]


def test_an_unknown_relation_is_reported():
    entry = {**FULL_ENTRY, "relation": "thin_wrapper"}
    assert ("error", "semantic-presentation-relation") in _findings(_row([entry]))


def test_a_claimed_relation_must_say_why():
    entry = {**FULL_ENTRY, "why": ""}
    assert ("error", "semantic-presentation-why") in _findings(_row([entry]))
    # `unstated` claims nothing, so it owes no explanation.
    silent = {**FULL_ENTRY, "relation": "unstated", "why": ""}
    assert _findings(_row([silent])) == []


def test_fronting_a_statement_the_row_does_not_register_is_a_warning_not_an_error():
    entry = {**FULL_ENTRY, "fronts": ["Upstream.canonical"]}
    findings = _findings(_row([entry]))
    assert findings == [("warning", "semantic-presentation-fronts")]


def test_a_presentation_name_must_be_registered_like_a_canonical_one():
    row = _row([FULL_ENTRY], registered=("Paper.canonical", "Paper.helper"))
    assert ("error", "semantic-review-declaration") in _findings(row)


def test_an_entry_naming_nothing_is_reported():
    assert ("error", "semantic-presentation") in _findings(_row([{"relation": "notation"}]))
    assert ("error", "semantic-presentation") in _findings(_row("Paper.presented"))


def test_a_review_without_the_field_is_unchanged():
    with_field = _row([])
    without_field = _row([])
    del without_field["semantic_review"]["presentation_declarations"]
    assert _findings(without_field) == _findings(with_field) == []


def test_a_presentation_statement_is_pinned_like_a_canonical_one():
    review = _row([FULL_ENTRY, "Paper.notation"])["semantic_review"]
    assert claimed_pin_declarations(review) == (
        "Paper.canonical", "Paper.presented", "Paper.notation",
    )


def test_the_cross_reference_index_knows_presentation_names_and_what_they_front():
    xref = Xref()
    xref.add_document("census", "paper", "Paper census", {"items": [
        {"id": "r1", "title": "Headline", **_row([FULL_ENTRY])},
    ]}, stamp=1)
    assert {o["field"] for o in xref.occurrences("Paper.presented")} == {
        "lean_declarations", "presentation_declarations",
    }
    assert {o["field"] for o in xref.occurrences("Paper.canonical")} == {
        "lean_declarations", "canonical_declarations", "presentation_declarations",
    }

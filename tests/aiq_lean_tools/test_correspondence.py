"""Correspondence edges, and the consistency they make checkable."""
from __future__ import annotations

from aiq_lean_tools.correspondence import (
    cited_declarations,
    display_fragments,
    edges_of,
    relation_legend,
    validate_correspondence,
)


def _codes(findings, level=None):
    return sorted(f.code for f in findings if level is None or f.level == level)


def _review(**over):
    review = {
        "source_fragments": [
            {"id": "printed", "role": "primary", "locator": {"marker": "T-1"}},
        ],
        "clause_map": [
            {"source_clause": "the bound", "lean_realization": "thm", "status": "claimed_exact",
             "relation": "literal", "kind": "conclusion", "source_fragment": "printed"},
        ],
    }
    review.update(over)
    return review


def test_a_clean_row_reports_nothing():
    assert validate_correspondence(_review(), location="r") == []


def test_unknown_relation_kind_and_fragment_are_rejected():
    review = _review(clause_map=[{
        "source_clause": "c", "lean_realization": "l", "status": "claimed_exact",
        "relation": "vibes", "kind": "vibe", "source_fragment": "elsewhere",
    }])
    assert _codes(validate_correspondence(review, location="r")) == [
        "clause-fragment", "clause-kind", "clause-relation",
    ]


def test_a_document_may_extend_the_relation_vocabulary():
    review = _review(clause_map=[{
        "source_clause": "c", "lean_realization": "l", "relation": "house_style",
        "source_fragment": "printed",
    }])
    relations = {"house_style": "what this project calls it"}
    assert validate_correspondence(review, location="r", relations=relations) == []
    assert "house_style" in relation_legend(relations)


def test_a_representation_change_must_name_its_correspondence_theorem():
    review = _review(clause_map=[{
        "source_clause": "tan 2 Theta", "lean_realization": "corner gauge",
        "relation": "representation_change", "source_fragment": "printed",
        "source_excerpt": "the estimate",
    }])
    assert "clause-evidence" in _codes(validate_correspondence(review, location="r"))
    review["clause_map"][0]["correspondence_declarations"] = ["Paper.corner_singular_values"]
    assert validate_correspondence(review, location="r") == []


def test_a_clause_realization_must_be_registered_but_evidence_need_only_be_flagged():
    review = _review(clause_map=[{
        "source_clause": "c", "lean_realization": "l", "relation": "via_theorem",
        "source_fragment": "printed", "source_excerpt": "the estimate",
        "lean_declarations": ["Paper.unregistered"],
        "correspondence_declarations": ["Paper.bridge"],
    }])
    findings = validate_correspondence(review, location="r", declarations=["Paper.main"])
    assert _codes(findings, "error") == ["clause-declaration"]
    assert _codes(findings, "warning") == ["clause-evidence-unregistered"]


def test_a_local_reading_may_not_quietly_inherit_a_standing_condition():
    review = _review(
        source_interpretation="local",
        source_fragments=[
            {"id": "printed", "role": "primary", "locator": {"marker": "T-1"}},
            {"id": "standing", "role": "standing_assumption", "locator": {"marker": "T-2"}},
        ],
        clause_map=[{
            "source_clause": "(3.5)", "lean_realization": "hCross",
            "relation": "inherited_standing_assumption", "source_fragment": "standing",
            "source_excerpt": "the estimate",
        }],
    )
    assert _codes(validate_correspondence(review, location="r"), "error") == [
        "source-interpretation-local"
    ]

    del review["source_interpretation"]
    assert _codes(validate_correspondence(review, location="r"), "warning") == [
        "source-interpretation-missing"
    ]

    review["source_interpretation"] = "nonlocal"
    assert _codes(validate_correspondence(review, location="r"), "warning") == [
        "source-interpretation-rationale"
    ]

    review["nonlocal_rationale"] = "The theorem is proved in a section where (3.5) is standing."
    assert validate_correspondence(review, location="r") == []


def test_a_nonlocal_claim_with_no_inherited_source_is_flagged():
    review = _review(source_interpretation="nonlocal", nonlocal_rationale="because")
    assert _codes(validate_correspondence(review, location="r"), "warning") == [
        "source-interpretation-nonlocal"
    ]


def test_a_quoted_excerpt_is_checked_against_the_passage():
    review = _review(clause_map=[{
        "source_clause": "c", "lean_realization": "l", "source_fragment": "printed",
        "source_excerpt": "assumed as well",
    }])
    text = {"printed": "From here on (3.5) is  assumed as well, except where stated."}
    assert validate_correspondence(review, location="r", fragment_text=text) == []
    text["printed"] = "The passage was rewritten and no longer says that."
    assert _codes(validate_correspondence(review, location="r", fragment_text=text)) == [
        "clause-excerpt"
    ]


def test_excerpt_matching_folds_tex_dashes_and_ties():
    review = _review(clause_map=[{
        "source_clause": "c", "lean_realization": "l", "source_fragment": "printed",
        "source_excerpt": "the Rayleigh--Ritz condition of Section~7",
    }])
    rendered = {"printed": "impose the Rayleigh–Ritz condition of Section 7 here"}
    assert validate_correspondence(review, location="r", fragment_text=rendered) == []


def test_fragments_need_an_id_a_locator_a_known_role_and_one_primary():
    review = _review(source_fragments=[
        {"role": "primary"},
        {"id": "x", "role": "footnote", "locator": {"marker": "T-2"}},
    ])
    codes = _codes(validate_correspondence(review, location="r"))
    assert "source-fragment-id" in codes
    assert "source-fragment-locator" in codes
    assert "source-fragment-role" in codes
    review["source_fragments"] = [{"id": "x", "role": "context", "locator": {"marker": "T-2"}}]
    assert "source-fragment-primary" in _codes(validate_correspondence(review, location="r"))


def test_a_legacy_row_is_shown_through_its_single_locator():
    legacy = {"source_locator": {"file": "prose/paper.md", "lines": [3, 9]}}
    shown = display_fragments(legacy)
    assert shown == [{"id": "source", "locator": legacy["source_locator"],
                      "role": "primary", "implicit": True}]
    # An explicit list wins, and a row with neither shows nothing rather than guessing.
    assert display_fragments(_review())[0]["id"] == "printed"
    assert display_fragments({}) == []


def test_edges_read_both_the_embedded_and_the_standalone_clause_shape():
    embedded = edges_of({"clause_map": [{"source_clause": "a", "lean_realization": "b"}]})
    standalone = edges_of({"clauses": [{"source_clause": "a", "lean_clause": "b",
                                        "relation": "exact"}]})
    assert embedded[0].lean_realization == standalone[0].lean_realization == "b"
    assert standalone[0].relation == "exact"


def test_cited_declarations_collects_both_sides():
    review = _review(clause_map=[{
        "source_clause": "c", "lean_realization": "l", "source_fragment": "printed",
        "lean_declarations": ["A"], "correspondence_declarations": ["B", "A"],
    }])
    assert cited_declarations(review) == ["A", "B"]


def test_a_consequential_relation_must_quote_the_words_it_disputes():
    """Naming the passage is not enough for a claim about a specific sentence.

    The browser can jump to a cited passage without an excerpt, but it cannot
    mark the printed statement the clause is actually about -- which is the one
    thing a reviewer opened the row to see.
    """
    for relation in ("representation_change", "via_theorem",
                     "inherited_standing_assumption", "refutation"):
        review = _review(clause_map=[{
            "source_clause": "c", "lean_realization": "l", "relation": relation,
            "source_fragment": "printed",
            "correspondence_declarations": ["Paper.bridge"],
        }])
        codes = _codes(validate_correspondence(review, location="r"), "error")
        assert "clause-excerpt-required" in codes, relation
        review["clause_map"][0]["source_excerpt"] = "the estimate"
        assert "clause-excerpt-required" not in _codes(
            validate_correspondence(review, location="r")), relation


def test_an_unestablished_clause_must_quote_what_is_unestablished():
    review = _review(clause_map=[{
        "source_clause": "c", "lean_realization": "l", "status": "open",
        "relation": "literal", "source_fragment": "printed",
    }])
    assert _codes(validate_correspondence(review, location="r"), "error") == [
        "clause-excerpt-required"
    ]


def test_an_ordinary_relation_still_needs_no_excerpt():
    assert validate_correspondence(_review(), location="r") == []


def test_a_quote_that_names_no_fragment_is_never_checked():
    review = _review(clause_map=[{
        "source_clause": "c", "lean_realization": "l", "relation": "literal",
        "source_excerpt": "the estimate",
    }])
    assert _codes(validate_correspondence(review, location="r"), "warning") == [
        "clause-excerpt-fragment"
    ]


def test_two_primary_passages_are_rejected_as_firmly_as_none():
    """`exactly one` was only ever enforcing `at least one`."""
    fragments = [
        {"id": "printed", "role": "primary", "locator": {"marker": "T-1"}},
        {"id": "also", "role": "primary", "locator": {"marker": "T-2"}},
    ]
    review = _review(source_fragments=fragments)
    assert _codes(validate_correspondence(review, location="r"), "error") == [
        "source-fragment-primary"
    ]
    review = _review(source_fragments=[
        {"id": "printed", "role": "definition", "locator": {"marker": "T-1"}},
    ])
    assert _codes(validate_correspondence(review, location="r"), "error") == [
        "source-fragment-primary"
    ]


def test_an_uncurated_row_is_shown_as_open_not_as_exact():
    from aiq_lean_tools.alignment import _fallback_review

    review = _fallback_review({"id": "T-9", "summary": "a bound", "lean_declarations": ["A.b"]})
    clause = review["clause_map"][0]
    assert clause["status"] == "open", \
        "a row with no registered correspondence must not advertise exactness"
    assert "No curated correspondence" in clause["lean_realization"]

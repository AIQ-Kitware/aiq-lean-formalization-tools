"""The alignment packet and page, with literature source passages attached.

Fixtures are synthetic throughout; nothing here contains publisher text.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiq_lean_tools.alignment import alignment_payload, build_alignment_packet, render_alignment_html
from aiq_lean_tools.source_model import PRIVATE, SourceDocument, SourceLibrary

TEX = """% Q-CERT-CLAIM-BEGIN T-1
\\subsection*{T-1}
% Q-CERT-SOURCE-BEGIN
Assume a gap $\\delta>0$.  Then for every unitary-invariant norm,
\\[
 \\delta\\norm{\\sin\\Theta}\\le\\norm{R}.
 \\tag{2.1}
\\]
% Q-CERT-SOURCE-END
% Q-CERT-CLAIM-END T-1

% Q-CERT-CLAIM-BEGIN STANDING
% Q-CERT-SOURCE-BEGIN
From here on the crossing condition is assumed, except where stated otherwise.
% Q-CERT-SOURCE-END
% Q-CERT-CLAIM-END STANDING
"""


def _repo(tmp_path: Path, *, private: bool = False) -> tuple[Path, SourceLibrary]:
    (tmp_path / "prose").mkdir()
    (tmp_path / "prose" / "paper.tex").write_text(TEX, encoding="utf-8")
    (tmp_path / "prose" / "preamble.tex").write_text(
        "\\newcommand{\\norm}[1]{\\left\\lVert #1\\right\\rVert}\n", encoding="utf-8")
    library = SourceLibrary(tmp_path)
    library.add(SourceDocument(
        id="Paper", path=tmp_path / "prose" / "paper.tex", format="tex",
        marker_prefix="Q-CERT", citation="A. Author 1970",
        macro_files=("preamble.tex",),
    ))
    if private:
        outside = tmp_path.parent / "private-notes.md"
        outside.write_text("Invented private prose that must never be serialized.\n",
                           encoding="utf-8")
        library.add(SourceDocument(
            id="Private", path=outside, format="markdown", visibility=PRIVATE,
            citation="local transcription"))

    census = tmp_path / "dev" / "paper-full-source-census.json"
    census.parent.mkdir()
    census.write_text(json.dumps({
        "schema_version": 1,
        "primary_source": {"citation": "A. Author 1970"},
        "status_definitions": {"done": "d"},
        "verification_definitions": {"proved_in_build": "y"},
        "importance_definitions": {"headline": "h", "major": "m"},
        "items": [{
            "id": "T-1", "title": "The estimate", "source_anchor": "Theorem 1",
            "status": "done", "verification": "proved_in_build", "importance": "headline",
            "lean_declarations": ["Paper.main"],
            "semantic_review": {
                "group": "g", "group_title": "The estimate", "claim": "c",
                "canonical_declarations": ["Paper.main"],
                "source_interpretation": "nonlocal",
                "nonlocal_rationale": "Proved where the crossing condition is standing.",
                "source_fragments": [
                    {"id": "printed", "role": "primary",
                     "locator": {"document": "Paper", "marker": "T-1", "result": "Theorem 1"}},
                    {"id": "standing", "role": "standing_assumption",
                     "locator": {"document": "Paper", "marker": "STANDING"}},
                ],
                "source_statement": {"setup": [], "hypotheses": ["gap"],
                                     "conclusions": ["bound"], "scope": []},
                "clause_map": [
                    {"source_clause": "gap", "lean_realization": "hdelta",
                     "status": "claimed_exact", "relation": "literal", "kind": "hypothesis",
                     "source_fragment": "printed", "source_excerpt": "Assume a gap"},
                    {"source_clause": "the crossing condition",
                     "lean_realization": "hCross", "status": "claimed_exact",
                     "relation": "inherited_standing_assumption", "kind": "hypothesis",
                     "source_fragment": "standing",
                     "source_excerpt": "except where stated otherwise"},
                    {"source_clause": "the bound", "lean_realization": "corner gauge",
                     "status": "claimed_exact", "relation": "representation_change",
                     "kind": "conclusion", "source_fragment": "printed",
                     "source_excerpt": "for every unitary-invariant norm",
                     "correspondence_declarations": ["Paper.corner_singular_values"]},
                ],
            },
        }, {
            "id": "T-2", "title": "A quieter row", "source_anchor": "Theorem 2",
            "status": "done", "verification": "proved_in_build", "importance": "major",
            "lean_declarations": ["Paper.second"],
        }],
    }), encoding="utf-8")
    return census, library


def _payload(tmp_path: Path, **kw):
    census, library = _repo(tmp_path, private=kw.pop("private", False))
    packet = build_alignment_packet([census], root=tmp_path, sources=library, **kw)
    return alignment_payload(packet), packet


def test_payload_carries_resolved_passages_and_edges(tmp_path: Path):
    data, _ = _payload(tmp_path)
    row = data["papers"][0]["rows"][0]
    printed = next(s for s in row["sources"] if s["id"] == "printed")
    assert printed["role"] == "primary"
    assert printed["fragment"]["equations"] == ["2.1"]
    assert any(b["kind"] == "display" for b in printed["fragment"]["blocks"])
    standing = next(s for s in row["sources"] if s["id"] == "standing")
    assert standing["role"] == "standing_assumption"
    assert row["sourceInterpretation"] == "nonlocal"
    assert [c["relation"] for c in row["edges"]] == [
        "literal", "inherited_standing_assumption", "representation_change"]
    assert data["sources"]["macros"]["\\norm"].startswith("\\left")
    assert "representation_change" in data["relations"]


def test_declarations_a_clause_points_at_get_their_own_panel(tmp_path: Path):
    data, _ = _payload(tmp_path)
    row = data["papers"][0]["rows"][0]
    assert [d["name"] for d in row["evidence"]] == ["Paper.corner_singular_values"]


def test_unpinned_passages_are_reported_as_unpinned(tmp_path: Path):
    data, _ = _payload(tmp_path)
    assert data["papers"][0]["rows"][0]["sourcePinSummary"] == "unpinned"


def test_row_selection_overrides_the_importance_threshold(tmp_path: Path):
    data, _ = _payload(tmp_path, rows=("T-2",))
    assert [r["id"] for r in data["papers"][0]["rows"]] == ["T-2"]


def test_a_row_with_no_review_still_renders_from_the_census_fallback(tmp_path: Path):
    data, _ = _payload(tmp_path, importance="major")
    quiet = next(r for r in data["papers"][0]["rows"] if r["id"] == "T-2")
    assert quiet["sources"] == []
    assert quiet["edges"]


def test_private_text_never_reaches_the_payload_by_default(tmp_path: Path):
    census, library = _repo(tmp_path, private=True)
    review = json.loads(census.read_text())["items"][0]["semantic_review"]
    data = json.loads(census.read_text())
    data["items"][0]["semantic_review"]["source_fragments"].append(
        {"id": "original", "role": "context",
         "locator": {"document": "Private", "file": "private-notes.md", "lines": [1, 1]}})
    census.write_text(json.dumps(data), encoding="utf-8")

    packet = build_alignment_packet([census], root=tmp_path, sources=library)
    public = json.dumps(alignment_payload(packet))
    assert "must never be serialized" not in public
    assert "withheld" in public
    assert "private_source" in public          # its existence is still visible

    local = build_alignment_packet([census], root=tmp_path, sources=library, include_private=True)
    assert "must never be serialized" in json.dumps(alignment_payload(local))
    assert "must never be serialized" not in packet.render_markdown()


def test_the_page_is_self_contained_and_needs_no_network(tmp_path: Path):
    data, _ = _payload(tmp_path)
    page = render_alignment_html(data)
    assert "katex" in page.lower()
    assert "data:font/woff2;base64," in page
    # No resource reference may leave the file: no external script, stylesheet,
    # font or image, and no leftover relative font path from the KaTeX CSS.
    # (The XML namespace URIs inside KaTeX are identifiers, not fetches.)
    import re

    refs = re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)', page) \
        + re.findall(r"url\(([^)]+)\)", page)
    outside = [r for r in refs if not r.startswith(("data:", "#"))]
    assert outside == [], f"page reaches outside itself: {outside}"
    assert "@import" not in page


def test_markdown_packet_shows_the_passage_and_its_hash(tmp_path: Path):
    _, packet = _payload(tmp_path)
    text = packet.render_markdown()
    assert "Assume a gap" in text
    assert "content hash" in text
    assert "Inherited source (standing assumption)" in text


def test_a_review_with_no_source_library_still_builds(tmp_path: Path):
    census, _ = _repo(tmp_path)
    packet = build_alignment_packet([census], root=tmp_path)
    data = alignment_payload(packet)
    row = data["papers"][0]["rows"][0]
    assert [s["id"] for s in row["sources"]] == ["printed", "standing"]
    assert all("fragment" not in s for s in row["sources"])
    assert all("unresolved" in s for s in row["sources"])


# -- the private original, overlaid on the public reconstruction -------------

ORIGINAL = """% Q-CERT-CLAIM-BEGIN T-1
% Q-CERT-SOURCE-BEGIN
Suppose the spectra are separated by $\\delta$.  Then in any unitarily
invariant norm the printed bound holds.
% Q-CERT-SOURCE-END
% Q-CERT-CLAIM-END T-1
"""


def _with_overlay(tmp_path: Path, *, locator_map=None, include_private=False):
    """The repository, plus a lawfully held local transcription overlaid on it."""
    census, library = _repo(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-original.tex"
    outside.write_text(ORIGINAL, encoding="utf-8")
    library.add(SourceDocument(
        id="Paper-original", path=outside, format="tex", visibility=PRIVATE,
        marker_prefix="Q-CERT", citation="Printed paper, local transcription",
        overlay_for="Paper", locator_map=locator_map or {},
    ))
    packet = build_alignment_packet([census], root=tmp_path, sources=library,
                                    include_private=include_private)
    return alignment_payload(packet)


def _printed(data):
    return next(s for s in data["papers"][0]["rows"][0]["sources"] if s["id"] == "printed")


def test_configuring_a_private_original_overlays_it_on_the_public_passage(tmp_path: Path):
    """The feature this exists for: configure a lawful copy, read it beside the
    reconstruction, without any checked-in review naming a machine-local file."""
    printed = _printed(_with_overlay(tmp_path, include_private=True))
    assert printed["fragment"]["rendition"] == "reconstruction"
    alternates = printed["alternates"]
    assert [a["rendition"] for a in alternates] == ["original"]
    text = json.dumps(alternates[0])
    assert "Suppose the spectra are separated" in text
    assert alternates[0]["sha256"] != printed["fragment"]["sha256"], \
        "two renditions of a passage are two texts, hashed independently"


def test_an_overlay_discloses_itself_but_withholds_its_text(tmp_path: Path):
    printed = _printed(_with_overlay(tmp_path, include_private=False))
    original = printed["alternates"][0]
    assert original["visibility"] == PRIVATE
    assert original["sha256"] and "withheld" in original
    assert "Suppose the spectra" not in json.dumps(original)


def test_an_overlay_may_spell_a_passage_differently(tmp_path: Path):
    """A transcription that does not carry the reconstruction's markers is
    mapped passage by passage rather than being unusable."""
    outside_map = {"T-1": {"lines": [1, 6]}}
    data = _with_overlay(tmp_path, locator_map=outside_map, include_private=True)
    assert _printed(data)["alternates"][0]["locator"]["lines"] == [1, 6]


def test_a_passage_the_overlay_does_not_cover_gets_no_counterpart(tmp_path: Path):
    data = _with_overlay(tmp_path, include_private=True)
    standing = next(s for s in data["papers"][0]["rows"][0]["sources"] if s["id"] == "standing")
    assert "alternates" not in standing, \
        "an overlay covering part of a paper must not invent the rest"


def test_private_macros_travel_only_in_a_local_render(tmp_path: Path):
    """A transcription written in its own notation renders as broken formulas
    without its macros, and its macro definitions are source text."""
    census, library = _repo(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-macros.tex"
    outside.write_text("\\newcommand{\\priv}[1]{\\mathbf{#1}}\n" + ORIGINAL, encoding="utf-8")
    library.add(SourceDocument(
        id="Paper-original", path=outside, format="tex", visibility=PRIVATE,
        marker_prefix="Q-CERT", citation="local", overlay_for="Paper",
    ))
    public = alignment_payload(build_alignment_packet(
        [census], root=tmp_path, sources=library))["sources"]["macros"]
    assert "\\priv" not in public, "a private macro definition is private source text"
    assert "\\norm" in public

    local = alignment_payload(build_alignment_packet(
        [census], root=tmp_path, sources=library, include_private=True))["sources"]["macros"]
    assert local["\\priv"] == "\\mathbf{#1}"


# -- the third completion axis ----------------------------------------------

def test_a_row_shows_its_semantic_certification_beside_its_compiler_evidence(tmp_path: Path):
    """A row whose hostile review is blocked must not read as merely compiled.

    `status` and `verification` are the formal disposition and the compiler; a
    row could satisfy both while its semantic correspondence was unaccepted, and
    the collapsed row said so nowhere.
    """
    census, library = _repo(tmp_path)
    data = json.loads(census.read_text(encoding="utf-8"))
    data["result_inventory"] = {"path": "results.json", "collection": "results"}
    census.write_text(json.dumps(data), encoding="utf-8")
    (census.parent / "results.json").write_text(json.dumps({"results": [{
        "id": "T-1", "disposition": "proved_exact", "verification": "proved_in_build",
        "semantic_certification": "hostile_review_blocked",
        "semantic_certification_note": "the directed clause is not established",
    }]}), encoding="utf-8")

    packet = build_alignment_packet([census], root=tmp_path, sources=library)
    row = alignment_payload(packet)["papers"][0]["rows"][0]
    assert row["certification"]["semantic"] == "hostile_review_blocked"
    assert row["certification"]["disposition"] == "proved_exact"
    assert row["certification"]["semanticNote"]
    assert row["certification"]["inventory"] == "results.json"


def test_a_census_with_no_result_inventory_simply_has_one_axis_fewer(tmp_path: Path):
    data, _ = _payload(tmp_path)
    certification = data["papers"][0]["rows"][0]["certification"]
    assert "semantic" not in certification
    assert certification["disposition"] == "done"


def test_a_broken_inventory_pointer_does_not_take_the_page_down(tmp_path: Path):
    census, library = _repo(tmp_path)
    data = json.loads(census.read_text(encoding="utf-8"))
    data["result_inventory"] = {"path": "absent.json"}
    census.write_text(json.dumps(data), encoding="utf-8")
    packet = build_alignment_packet([census], root=tmp_path, sources=library)
    assert alignment_payload(packet)["papers"][0]["rows"][0]["certification"]["disposition"] == "done"


# -- triage: an unreviewed row is not a disputed one ------------------------

def test_an_uncurated_row_is_marked_as_such(tmp_path: Path):
    """A row nobody reviewed and a row a reviewer left open are both "not
    established", and a triage view that cannot tell them apart reports the
    whole uncurated tail as if it were disputed."""
    data, _ = _payload(tmp_path, importance="major")
    rows = {r["id"]: r for r in data["papers"][0]["rows"]}
    assert rows["T-1"]["uncurated"] is False
    assert rows["T-2"]["uncurated"] is True, "T-2 has no semantic_review"
    assert [c["status"] for c in rows["T-2"]["edges"]] == ["open"]


def test_a_curated_row_left_open_is_not_marked_uncurated(tmp_path: Path):
    census, library = _repo(tmp_path)
    data = json.loads(census.read_text(encoding="utf-8"))
    clause = data["items"][0]["semantic_review"]["clause_map"][0]
    clause["status"] = "open"
    census.write_text(json.dumps(data), encoding="utf-8")
    packet = build_alignment_packet([census], root=tmp_path, sources=library)
    row = alignment_payload(packet)["papers"][0]["rows"][0]
    assert row["uncurated"] is False
    assert any(c["status"] == "open" for c in row["edges"])

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

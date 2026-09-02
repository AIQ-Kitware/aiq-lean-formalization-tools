"""Pinning the source passage a review accepted."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiq_lean_tools.census import load_census
from aiq_lean_tools.source_model import SourceDocument, SourceLibrary
from aiq_lean_tools.source_pins import (
    census_source_targets,
    check_source_targets,
    pin_source_targets,
    validate_source_pins,
)

PASSAGE = """% M-CERT-CLAIM-BEGIN T-1
% M-CERT-SOURCE-BEGIN
Assume a gap of size $\\delta$.  Then the estimate holds.
% M-CERT-SOURCE-END
% M-CERT-CLAIM-END T-1
"""


def _repo(tmp_path: Path, passage: str = PASSAGE) -> SourceLibrary:
    """A fresh library over ``prose/paper.tex``; re-reading it picks up an edit."""
    (tmp_path / "prose").mkdir(exist_ok=True)
    (tmp_path / "prose" / "paper.tex").write_text(passage, encoding="utf-8")
    library = SourceLibrary(tmp_path)
    library.add(SourceDocument(
        id="Paper", path=tmp_path / "prose" / "paper.tex", format="tex",
        marker_prefix="M-CERT", citation="A. Author 1970",
    ))
    return library


def _census_file(tmp_path: Path) -> Path:
    path = tmp_path / "dev" / "paper-full-source-census.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "primary_source": {"citation": "A. Author 1970"},
        "status_definitions": {"done": "d"},
        "verification_definitions": {"proved_in_build": "y"},
        "importance_definitions": {"headline": "h"},
        "items": [{
            "id": "T-1", "title": "The estimate", "source_anchor": "Theorem 1",
            "status": "done", "verification": "proved_in_build", "importance": "headline",
            "lean_declarations": ["Paper.main"],
            "semantic_review": {
                "group": "g", "group_title": "The estimate", "claim": "c",
                "canonical_declarations": ["Paper.main"],
                "source_statement": {"setup": [], "hypotheses": ["gap"], "conclusions": ["bound"], "scope": []},
                "source_fragments": [
                    {"id": "printed", "role": "primary",
                     "locator": {"document": "Paper", "marker": "T-1"}}],
                "clause_map": [{"source_clause": "bound", "lean_realization": "Paper.main",
                                "status": "claimed_exact", "source_fragment": "printed"}],
            },
        }],
    }), encoding="utf-8")
    return path


def test_pin_then_check_then_notice_the_passage_moving(tmp_path: Path):
    library = _repo(tmp_path)
    path = _census_file(tmp_path)
    census = load_census(path, root=tmp_path)
    targets = census_source_targets(census)
    written, findings = pin_source_targets(targets, library)
    assert (written, findings) == (1, [])
    assert check_source_targets(targets, library) == []

    pin = targets[0].pins[0]
    assert pin["fragment"] == "printed" and pin["document"] == "Paper"
    assert pin["locator"]["lines"] == [3, 3]

    # Editing the reconstruction invalidates the review that accepted it.
    edited = _repo(tmp_path, PASSAGE.replace("the estimate holds", "a weaker estimate holds"))
    assert [f.code for f in check_source_targets(targets, edited)] == ["source-drift"]


def test_a_vanished_marker_is_an_error_not_a_silent_pass(tmp_path: Path):
    library = _repo(tmp_path)
    path = _census_file(tmp_path)
    census = load_census(path, root=tmp_path)
    targets = census_source_targets(census)
    pin_source_targets(targets, library)
    gone = _repo(tmp_path, "nothing marked here\n")
    assert [f.code for f in check_source_targets(targets, gone)] == ["source-pin-unresolved"]


def test_unpinned_fragments_are_a_warning(tmp_path: Path):
    library = _repo(tmp_path)
    census = load_census(_census_file(tmp_path), root=tmp_path)
    targets = census_source_targets(census)
    assert [f.code for f in check_source_targets(targets, library)] == ["source-unpinned"]


def test_structural_pin_validation():
    container = {
        "source_fragments": [{"id": "printed", "role": "primary", "locator": {"marker": "T"}}],
        "source_pins": [
            {"fragment": "printed", "sha256": "a"},
            {"fragment": "printed", "sha256": "b"},
            {"fragment": "ghost", "sha256": "c"},
            {"fragment": "printed"},
        ],
    }
    codes = sorted(f.message for f in validate_source_pins(container, location="r"))
    assert any("duplicate pin" in m for m in codes)
    assert any("not one the review declares" in m for m in codes)
    assert any("needs sha256" in m for m in codes)
    assert validate_source_pins({}, location="r") == []

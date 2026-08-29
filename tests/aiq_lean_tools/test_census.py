from __future__ import annotations

import json
from pathlib import Path

from aiq_lean_tools.census import load_census
from aiq_lean_tools.lean_backend import MockLeanBackend


def _data():
    return {
        "schema_version": 1,
        "primary_source": {"citation": "Example"},
        "status_definitions": {"implemented": "done", "planned": "todo"},
        "verification_definitions": {
            "proved_in_build": "all",
            "partially_in_build": "some",
            "absent": "none",
        },
        "importance_definitions": {"headline": "main", "supporting": "helper"},
        "gaps": {"gap-a": {"description": "known gap"}},
        "items": [
            {
                "id": "r1",
                "title": "Result one",
                "source_anchor": "Theorem 1",
                "status": "implemented",
                "verification": "proved_in_build",
                "importance": "headline",
                "lean_declarations": ["Paper.one", "Paper.helper"],
                "gap_refs": ["gap-a"],
                "semantic_alignment": None,
            }
        ],
    }


def test_validate_patch_render_and_mock_probe(tmp_path: Path):
    path = tmp_path / "paper-full-source-census.json"
    path.write_text(json.dumps(_data()))
    doc = load_census(path, root=tmp_path)
    assert not [f for f in doc.validate() if f.level == "error"]
    assert doc.family == "source-census"
    assert doc.summary()["items"] == 1

    doc.patch_item("r1", [("notes", "reviewed"), ("semantic_alignment.classification", "exact")])
    assert doc.row("r1")["notes"] == "reviewed"
    assert doc.row("r1")["semantic_alignment"]["classification"] == "exact"

    backend = MockLeanBackend({"Paper.one": True, "Paper.helper": False})
    probe = doc.probe(backend=backend, imports=["Paper"])
    assert probe.resolved == ["Paper.one"]
    assert probe.unresolved == ["Paper.helper"]
    assert doc.apply_probe(probe) == 1
    assert doc.row("r1")["verification"] == "partially_in_build"

    md = doc.render_markdown()
    html = doc.render_html()
    assert "Theorem 1" in md
    assert "Result one" in html
    assert "census" in html.lower()


def test_validation_catches_unknown_definition_values(tmp_path: Path):
    data = _data()
    data["items"][0]["status"] = "invented"
    path = tmp_path / "paper-source-census.json"
    path.write_text(json.dumps(data))
    findings = load_census(path, root=tmp_path).validate()
    assert any(f.code == "status" and f.level == "error" for f in findings)

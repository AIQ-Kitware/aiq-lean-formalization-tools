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


def _curated_review():
    return {
        "group": "g1",
        "group_title": "Main result",
        "claim": "A source claim.",
        "source_statement": {
            "setup": ["Objects are fixed."],
            "hypotheses": ["A hypothesis holds."],
            "conclusions": ["The conclusion follows."],
            "scope": [],
        },
        "canonical_declarations": ["Paper.one"],
        "supporting_declarations": ["Paper.helper"],
        "context_declarations": [
            {"name": "Paper.Context", "mathematical_role": "Defines the paper norm."}
        ],
        "clause_map": [
            {
                "source_clause": "The conclusion follows.",
                "lean_realization": "Paper.one states the conclusion.",
                "status": "claimed_exact",
            }
        ],
    }


def test_embedded_semantic_review_contract(tmp_path: Path):
    data = _data()
    data["items"][0]["semantic_review"] = _curated_review()
    data["items"][0]["semantic_review_variants"] = [
        {
            "id": "derived-projector-form",
            "title": "Derived projector form",
            "claim": "A derived review target.",
            "provenance_note": "Derived from the main paper theorem.",
            "source_statement": {
                "setup": [],
                "hypotheses": ["The main theorem applies."],
                "conclusions": ["A projector inequality follows."],
                "scope": ["This is not a separately printed source theorem."],
            },
            "canonical_declarations": ["Paper.derived"],
            "supporting_declarations": [],
            "context_declarations": [],
            "clause_map": [
                {
                    "source_clause": "Derived projector inequality.",
                    "lean_realization": "Paper.derived",
                    "status": "derived",
                }
            ],
        }
    ]
    path = tmp_path / "curated-census.json"
    path.write_text(json.dumps(data))
    doc = load_census(path, root=tmp_path)
    assert not [f for f in doc.validate() if f.level == "error"]

    # Once any headline row opts into the embedded review contract, headline
    # rows are required to keep the full curated surface well formed.
    del data["items"][0]["semantic_review"]["source_statement"]
    path.write_text(json.dumps(data))
    findings = load_census(path, root=tmp_path).validate()
    assert any(f.code == "semantic-source-statement" for f in findings)


def test_headline_review_contract_survives_total_review_removal(tmp_path):
    path = tmp_path / "paper-full-source-census.json"
    data = {
        "schema_version": 1,
        "primary_source": {"citation": "Paper"},
        "status_definitions": {"implemented": "done"},
        "verification_definitions": {"proved_in_build": "yes"},
        "importance_definitions": {
            "headline": "main",
            "major": "major",
            "supporting": "supporting",
            "technical": "technical",
        },
        "items": [{
            "id": "main",
            "title": "Main result",
            "status": "implemented",
            "verification": "proved_in_build",
            "importance": "headline",
            "lean_declarations": ["Paper.main"],
        }],
    }
    path.write_text(json.dumps(data))
    doc = load_census(path, root=tmp_path)
    codes = {finding.code for finding in doc.validate()}
    assert "semantic-review" in codes


def test_render_check_reports_a_stale_generated_file(tmp_path, capsys):
    from aiq_lean_tools.cli import main

    census = tmp_path / "census.json"
    census.write_text(json.dumps({
        "title": "Example",
        "status_definitions": {"done": "done"},
        "items": [{"id": "r1", "title": "Row", "status": "done", "lean_declarations": []}],
    }))
    out = tmp_path / "census.md"

    assert main(["census", "render", str(census), "-o", str(out), "--check"]) == 1
    assert "missing" in capsys.readouterr().out

    assert main(["census", "render", str(census), "-o", str(out)]) == 0
    assert main(["census", "render", str(census), "-o", str(out), "--check"]) == 0

    out.write_text("hand-edited\n")
    assert main(["census", "render", str(census), "-o", str(out), "--check"]) == 1
    assert "stale" in capsys.readouterr().out

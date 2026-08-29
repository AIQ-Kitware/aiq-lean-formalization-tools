from __future__ import annotations

import json
from pathlib import Path

from aiq_lean_tools.semantic_review import load_semantic_review


def test_semantic_review_roundtrip(tmp_path: Path):
    data = {
        "schema_version": 1,
        "paper": {"citation": "Example paper"},
        "relation_definitions": {"exact": "same clause"},
        "rows": [{
            "id": "r1",
            "source_anchor": "Theorem 1",
            "title": "Main result",
            "lean_declarations": ["Paper.one"],
            "clauses": [{
                "source_clause": "x is positive",
                "lean_clause": "0 < x",
                "relation": "exact",
                "note": "",
            }],
        }],
    }
    path = tmp_path / "paper-result-semantic-review.json"
    path.write_text(json.dumps(data))
    doc = load_semantic_review(path, root=tmp_path)
    assert not [f for f in doc.validate() if f.level == "error"]
    assert doc.summary()["relations"] == {"exact": 1}
    doc.patch_row("r1", [("review", "checked")])
    assert "checked" in doc.render_markdown()
    assert "Main result" in doc.render_html()


def test_append_row_clears_draft(tmp_path: Path):
    path = tmp_path / "review.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "draft": True,
        "relation_definitions": {"exact": "same"},
        "rows": [],
    }))
    doc = load_semantic_review(path, root=tmp_path)
    doc.append_row({
        "id": "x",
        "lean_declarations": [],
        "clauses": [{"source_clause": "a", "lean_clause": "a", "relation": "exact"}],
    })
    assert doc.data["draft"] is False
    assert not [f for f in doc.validate() if f.level == "error"]

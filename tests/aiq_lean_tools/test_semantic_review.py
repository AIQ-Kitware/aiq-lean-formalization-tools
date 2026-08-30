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


def test_review_must_not_drift_from_its_companion_census(tmp_path):
    import json as _json

    from aiq_lean_tools.semantic_review import load_semantic_review

    census = tmp_path / "census.json"
    census.write_text(_json.dumps({
        "items": [
            {"id": "r1", "lean_declarations": ["Lib.a"], "source_locator": {"file": "p.tex", "lines": [1, 2]}},
            {"id": "r2", "lean_declarations": [], "source_locator": {"file": "p.tex", "lines": [3, 4]}},
        ],
    }))
    (tmp_path / "p.tex").write_text("a\nb\nc\nd\n")
    review = tmp_path / "review.json"
    review.write_text(_json.dumps({
        "companion_census": "census.json",
        "relation_definitions": {"exact": "same"},
        "rows": [
            {"id": "r1", "source_anchor": "T1", "title": "T",
             "lean_declarations": ["Lib.b"],
             "source_locator": {"file": "p.tex", "lines": [1, 2]},
             "clauses": [{"source_clause": "s", "lean_clause": "l", "relation": "exact"}]},
        ],
    }))
    codes = {f.code for f in load_semantic_review(review, root=tmp_path).validate()}
    assert "companion-declarations" in codes   # r1 cites a different declaration
    assert "companion-unreviewed" in codes     # r2 is in the census and not reviewed


def test_review_locator_range_must_lie_inside_the_file(tmp_path):
    import json as _json

    from aiq_lean_tools.semantic_review import load_semantic_review

    (tmp_path / "p.tex").write_text("a\nb\n")
    review = tmp_path / "review.json"
    review.write_text(_json.dumps({
        "relation_definitions": {"exact": "same"},
        "rows": [
            {"id": "r1", "source_anchor": "T1", "title": "T",
             "source_locator": {"file": "p.tex", "lines": [1, 99]},
             "clauses": [{"source_clause": "s", "lean_clause": "l", "relation": "exact"}]},
        ],
    }))
    codes = {f.code for f in load_semantic_review(review, root=tmp_path).validate()}
    assert "source-range" in codes

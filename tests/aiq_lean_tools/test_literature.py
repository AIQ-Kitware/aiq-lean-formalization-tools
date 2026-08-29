import json

from aiq_lean_tools.literature import load_literature


def base_manifest():
    return {
        "version": "1",
        "title": "Sources",
        "role_legend": {"target": "direct target"},
        "status_legend": {"missing": "missing", "complete": "complete"},
        "bibliographic_status_legend": {"verified": "verified"},
        "group_order": ["Papers"],
        "priority_order": ["P0"],
        "works": {
            "Paper2026": {
                "title": "A Paper", "authors": ["A. Author"], "year": 2026,
                "kind": "paper", "group": "Papers", "priority": "P0", "role": "target",
                "formalization_status": "formalized", "distilled_status": "missing",
                "bibliographic_status": "verified", "primary_url": "https://example.com/paper",
                "target_note": "paper.tex", "repo_evidence": ["Proof.lean"], "existing_assets": [],
                "scope": "main theorem", "missing_work": "write source-order note",
            }
        },
    }


def test_literature_validate_render_and_edit(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "Proof.lean").write_text("theorem proof : True := by trivial\n")
    path = tmp_path / "literature.json"
    path.write_text(json.dumps(base_manifest()))
    doc = load_literature(path)
    assert not [f for f in doc.validate() if f.level == "error"]
    assert doc.summary()["works"] == 1
    assert "A Paper" in doc.render_markdown()
    assert "Literature and source inventory" in doc.render_html()
    assert "\\documentclass" in doc.render_latex()
    doc.patch_work("Paper2026", [("formalization_status", "complete")], [])
    assert doc.row("Paper2026")["formalization_status"] == "complete"


def test_literature_complete_note_markers(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "Proof.lean").write_text("")
    data = base_manifest()
    data["works"]["Paper2026"]["distilled_status"] = "complete"
    data["reconstruction"] = {"required_markers": ["Source map"], "complete_statuses": ["complete"]}
    path = tmp_path / "literature.json"
    path.write_text(json.dumps(data))
    doc = load_literature(path)
    assert "literature-complete-note" in {f.code for f in doc.validate()}
    (tmp_path / "paper.tex").write_text("Source map")
    assert not [f for f in doc.validate() if f.level == "error"]

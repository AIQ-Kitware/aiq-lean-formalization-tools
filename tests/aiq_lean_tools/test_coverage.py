import json

from aiq_lean_tools.coverage import load_coverage_bundle, load_result_inventory, load_source_atom_inventory


def _write_bundle(tmp_path):
    atoms = {
        "schema_version": 1,
        "atoms": [
            {
                "id": "A1",
                "order": 1,
                "kind": "theorem",
                "source_role": "mathematical_assertion",
                "formalization_role": "result",
                "formalization_result_ids": ["R1"],
                "summary": "source claim",
            },
            {
                "id": "A2",
                "order": 2,
                "kind": "proof",
                "source_role": "proof",
                "formalization_role": "non_result",
                "formalization_result_ids": [],
                "summary": "proof detail",
            },
        ],
    }
    result = {
        "schema_version": 1,
        "source_fidelity_inventory": "atoms.json",
        "result_count": 1,
        "results": [
            {
                "id": "R1",
                "title": "Theorem one",
                "source_anchor": "Theorem 1",
                "source_atom_ids": ["A1"],
                "completion_obligation": True,
                "disposition": "proved_exact",
                "verification": "proved_in_build",
                "semantic_certification": "accepted",
                "lean_declarations": ["MyLib.theoremOne"],
            }
        ],
    }
    (tmp_path / "atoms.json").write_text(json.dumps(atoms))
    (tmp_path / "results.json").write_text(json.dumps(result))
    return tmp_path / "results.json"


def test_coverage_bundle_crosslinks_and_render(tmp_path):
    path = _write_bundle(tmp_path)
    bundle = load_coverage_bundle(path, root=tmp_path)
    assert bundle.validate() == []
    summary = bundle.summary()
    assert summary["results"] == 1
    assert summary["source_atoms"]["atoms"] == 2
    assert summary["results_backlinked_by_atoms"] == 1
    assert "Theorem one" in bundle.render_markdown()
    assert "Formalization coverage" in bundle.render_html()


def test_coverage_bundle_reports_broken_links(tmp_path):
    path = _write_bundle(tmp_path)
    data = json.loads(path.read_text())
    data["results"][0]["source_atom_ids"] = ["MISSING"]
    path.write_text(json.dumps(data))
    bundle = load_coverage_bundle(path, root=tmp_path)
    codes = {finding.code for finding in bundle.validate()}
    assert "result-atom-missing" in codes


def test_inventory_patch(tmp_path):
    path = _write_bundle(tmp_path)
    results = load_result_inventory(path, root=tmp_path)
    results.patch_item("R1", [("semantic_certification", "pending")])
    assert results.row("R1")["semantic_certification"] == "pending"
    atoms = load_source_atom_inventory(tmp_path / "atoms.json", root=tmp_path)
    atoms.patch_atom("A1", [("formalization_role_reason", "counted source statement")])
    assert atoms.atom("A1")["formalization_role_reason"] == "counted source statement"

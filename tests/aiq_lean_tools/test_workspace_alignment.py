from __future__ import annotations

import json
from pathlib import Path

from aiq_lean_tools.alignment import build_alignment_packet
from aiq_lean_tools.lean_backend import MockLeanBackend
from aiq_lean_tools.workspace import FormalizationWorkspace


def _census(title: str, decl: str):
    return {
        "schema_version": 1,
        "primary_source": {"citation": title},
        "status_definitions": {"implemented": "done"},
        "verification_definitions": {"proved_in_build": "yes"},
        "importance_definitions": {"headline": "main"},
        "items": [{
            "id": "r1",
            "title": "Headline",
            "source_anchor": "Theorem 1",
            "status": "implemented",
            "verification": "proved_in_build",
            "importance": "headline",
            "lean_declarations": [decl],
            "semantic_review": {
                "group": "main",
                "group_title": "Main theorem",
                "claim": "claim",
                "canonical_declarations": [decl],
                "context_declarations": [{"name": "Paper.Context", "mathematical_role": "context type"}],
                "source_statement": {
                    "setup": [],
                    "hypotheses": ["x > 0"],
                    "conclusions": ["claim"],
                    "scope": [],
                },
                "clause_map": [{"source_clause": "claim", "lean_realization": "claim", "status": "claimed_exact"}],
            },
        }],
    }


def test_workspace_cross_document_reuse(tmp_path: Path):
    (tmp_path / "dev").mkdir()
    for name in ("a", "b"):
        (tmp_path / "dev" / f"{name}-full-source-census.json").write_text(json.dumps(_census(name, "Shared.theorem")))
    ws = FormalizationWorkspace.discover(tmp_path)
    data = ws.overview()
    assert data["census_count"] == 2
    assert data["result_rows"] == 2
    assert data["unique_cited_declarations"] == 1
    assert data["cross_document_reuse"][0]["document_count"] == 2
    assert "Shared.theorem" in ws.render_html()


def test_alignment_packet_uses_mock_backend(tmp_path: Path):
    path = tmp_path / "paper-full-source-census.json"
    data = _census("Paper", "Paper.main")
    data["items"][0]["semantic_review_variants"] = [{
        "id": "derived",
        "title": "Derived form",
        "claim": "derived claim",
        "provenance_note": "derived from the headline theorem",
        "source_statement": {
            "setup": [],
            "hypotheses": ["x > 0"],
            "conclusions": ["derived claim"],
            "scope": [],
        },
        "canonical_declarations": ["Paper.derived"],
        "supporting_declarations": [],
        "context_declarations": [],
        "clause_map": [{
            "source_clause": "derived claim",
            "lean_realization": "Paper.derived",
            "status": "derived",
        }],
    }]
    path.write_text(json.dumps(data))
    (tmp_path / "Paper.lean").write_text(
        "namespace Paper\nvariable {E : Type}\ndef Context := Nat\ntheorem main (x : E) : True := by trivial\ntheorem derived : True := by trivial\nend Paper\n"
    )
    backend = MockLeanBackend({
        "check:Paper.main": "Paper.main : True",
        "print:Paper.Context": "def Paper.Context := Nat",
        "check:Paper.derived": "Paper.derived : True",
    })
    packet = build_alignment_packet(
        [path], root=tmp_path, probe=True, imports=["Paper"], backend=backend
    )
    text = packet.render_markdown()
    assert "Paper.main : True" in text
    assert "def Paper.Context := Nat" in text
    assert "Human-written Lean statement" in text
    assert "theorem main (x : E) : True" in text
    assert "Derived review variants" in text
    assert "Paper.derived : True" in text
    assert "Clause correspondence" in text


def test_workspace_discovers_coverage_inventory(tmp_path: Path):
    dev = tmp_path / "dev"
    dev.mkdir()
    atoms = {
        "schema_version": 1,
        "atoms": [{
            "id": "A1",
            "order": 1,
            "kind": "theorem",
            "source_role": "mathematical_assertion",
            "formalization_role": "result",
            "formalization_result_ids": ["R1"],
        }],
    }
    results = {
        "schema_version": 1,
        "source_fidelity_inventory": "dev/paper-source-atom-inventory.json",
        "result_count": 1,
        "results": [{
            "id": "R1",
            "source_atom_ids": ["A1"],
            "completion_obligation": True,
            "disposition": "proved_exact",
            "verification": "proved_in_build",
            "semantic_certification": "accepted",
            "lean_declarations": [],
        }],
    }
    (dev / "paper-source-atom-inventory.json").write_text(json.dumps(atoms))
    (dev / "paper-formalization-result-inventory.json").write_text(json.dumps(results))
    ws = FormalizationWorkspace.discover(tmp_path)
    overview = ws.overview()
    assert overview["coverage_inventory_count"] == 1
    assert overview["coverage_results"] == 1
    assert overview["source_fidelity_atoms"] == 1
    assert ws.validate() == []
    assert "paper-formalization-result-inventory.json" in ws.render_html()


def test_workspace_discovers_literature_and_foundation_tracking(tmp_path: Path):
    dev = tmp_path / "dev"
    dev.mkdir()
    (tmp_path / "Demo.lean").write_text("namespace Demo\ntheorem base : True := by trivial\nend Demo\n")
    literature = {
        "version": "1",
        "title": "Sources",
        "role_legend": {"target": "target"},
        "status_legend": {"missing": "missing"},
        "bibliographic_status_legend": {"verified": "verified"},
        "works": {
            "Paper": {
                "title": "Paper", "authors": ["A"], "year": 2026,
                "kind": "paper", "group": "Primary", "priority": "P0", "role": "target",
                "formalization_status": "started", "distilled_status": "missing",
                "bibliographic_status": "verified", "primary_url": "https://example.com",
                "target_note": "paper.tex", "repo_evidence": ["Demo.lean"], "existing_assets": [],
                "scope": "main theorem", "missing_work": "write note",
            }
        },
    }
    foundation = {
        "title": "Foundations",
        "nodes": [{
            "id": "base", "module": "Demo", "file": "Demo.lean",
            "declaration": "Demo.base", "kind": "proof_candidate",
        }],
    }
    (dev / "paper-literature.json").write_text(json.dumps(literature))
    (dev / "paper-foundations.json").write_text(json.dumps(foundation))
    ws = FormalizationWorkspace.discover(tmp_path)
    overview = ws.overview()
    assert overview["literature_inventory_count"] == 1
    assert overview["literature_works"] == 1
    assert overview["foundation_map_count"] == 1
    assert overview["foundation_nodes"] == 1
    assert ws.validate() == []
    html = ws.render_html()
    assert "literature works" in html
    assert "foundation nodes" in html


def test_workspace_ignores_unrelated_foundation_named_json(tmp_path: Path):
    dev = tmp_path / "dev"
    dev.mkdir()
    valid = {
        "title": "Foundations",
        "nodes": [{
            "id": "base", "module": "Demo", "file": "Demo.lean",
            "declaration": "Demo.base", "kind": "proof_candidate",
        }],
    }
    (tmp_path / "Demo.lean").write_text("namespace Demo\ntheorem base : True := by trivial\nend Demo\n")
    (dev / "paper-foundations.json").write_text(json.dumps(valid))
    (dev / "shared-hard-foundations-candidates.json").write_text(json.dumps({
        "generated_at": "2026-08-29", "candidates": [{"name": "Demo.base"}],
    }))
    (dev / "foundation-provenance.json").write_text(json.dumps({
        "commit": "abc", "entries": [{"source": "Demo.lean"}],
    }))
    ws = FormalizationWorkspace.discover(tmp_path)
    assert len(ws.foundation_paths) == 1
    assert ws.foundation_paths[0].name == "paper-foundations.json"

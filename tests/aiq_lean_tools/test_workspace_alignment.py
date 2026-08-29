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
                "canonical_declarations": [decl],
                "context_declarations": [{"name": "Paper.Context", "mathematical_role": "context type"}],
                "source_statement": {"hypotheses": ["x > 0"], "conclusions": ["claim"]},
                "clause_map": [{"source_clause": "claim", "lean_realization": "claim", "status": "exact"}],
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
    path.write_text(json.dumps(_census("Paper", "Paper.main")))
    backend = MockLeanBackend({
        "check:Paper.main": "Paper.main : True",
        "print:Paper.Context": "def Paper.Context := Nat",
    })
    packet = build_alignment_packet(
        [path], root=tmp_path, probe=True, imports=["Paper"], backend=backend
    )
    text = packet.render_markdown()
    assert "Paper.main : True" in text
    assert "def Paper.Context := Nat" in text
    assert "Clause correspondence" in text

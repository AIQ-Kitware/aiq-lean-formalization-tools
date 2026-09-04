"""Presentation forms in the alignment packet and page.

A presentation form is a second Lean spelling of a result, restated so the
signature reads like the printed theorem.  The census registers it beside the
API-canonical statement it fronts; these tests fix how the packet reports that
claim, including what the dependency graph does and does not corroborate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiq_lean_tools.alignment import (
    alignment_payload,
    build_alignment_packet,
    load_graph_table,
    render_alignment_html,
)

PRESENTATION = "Paper.printed"
CANONICAL = "Paper.main"
ENGINE = "Paper.engine"


def _census(presentation: Any | None) -> dict[str, Any]:
    review: dict[str, Any] = {
        "group": "g",
        "group_title": "The estimate",
        "claim": "claim",
        "canonical_declarations": [CANONICAL],
        "source_statement": {"setup": [], "hypotheses": ["gap"],
                             "conclusions": ["bound"], "scope": []},
        "clause_map": [{"source_clause": "the bound", "lean_realization": CANONICAL,
                        "status": "claimed_exact"}],
    }
    if presentation is not None:
        review["presentation_declarations"] = [presentation]
    return {
        "schema_version": 1,
        "primary_source": {"citation": "A. Author 1970"},
        "status_definitions": {"done": "d"},
        "verification_definitions": {"proved_in_build": "y"},
        "importance_definitions": {"headline": "h"},
        "items": [{
            "id": "T-1", "title": "The estimate", "source_anchor": "Theorem 1",
            "status": "done", "verification": "proved_in_build", "importance": "headline",
            "lean_declarations": [CANONICAL, PRESENTATION],
            "semantic_review": review,
        }],
    }


#: Deps are the union of type and proof dependencies, as a saved index stores them.
NODES = {
    PRESENTATION: [ENGINE],
    CANONICAL: [ENGINE],
    ENGINE: [],
    "Paper.unrelated": [],
}


def _graph(tmp_path: Path, nodes: dict[str, list[str]] = NODES) -> dict[str, Any]:
    path = tmp_path / "graph.json"
    path.write_text(json.dumps({"nodes": [
        {"name": name, "module": "Paper", "kind": "theorem", "deps": deps, "typeDeps": []}
        for name, deps in nodes.items()
    ]}), encoding="utf-8")
    return load_graph_table(path)


def _payload(tmp_path: Path, presentation: Any | None, *, graph: Any = None) -> dict[str, Any]:
    census = tmp_path / "dev" / "paper-full-source-census.json"
    census.parent.mkdir(exist_ok=True)
    census.write_text(json.dumps(_census(presentation)), encoding="utf-8")
    (tmp_path / "Paper.lean").write_text(
        "/-- Printed. -/\ntheorem printed (x : Nat) : x = x := rfl\n"
        "/-- Canonical. -/\ntheorem main (x : Nat) : x = x := rfl\n",
        encoding="utf-8")
    packet = build_alignment_packet([census], root=tmp_path)
    return alignment_payload(packet, graph=graph)


def _row(data: dict[str, Any]) -> dict[str, Any]:
    return data["papers"][0]["rows"][0]


def test_a_row_without_the_field_is_unchanged(tmp_path: Path):
    row = _row(_payload(tmp_path, None))
    assert row["presentation"] == []
    assert [d["name"] for d in row["canonical"]] == [CANONICAL]
    # The presentation name is still a plain registered declaration here.
    assert [s["name"] for s in row["supporting"]] == [PRESENTATION]


def test_a_bare_string_entry_is_normalized(tmp_path: Path):
    row = _row(_payload(tmp_path, PRESENTATION))
    entry = row["presentation"][0]
    assert entry["name"] == PRESENTATION
    assert entry["fronts"] == []
    assert entry["relation"] == "unstated"
    assert entry["devices"] == []
    assert entry["why"] == ""
    assert entry["frontsVerified"] == {}


def test_a_partial_entry_degrades_rather_than_raising(tmp_path: Path):
    row = _row(_payload(tmp_path, {"name": PRESENTATION, "relation": "notation"}))
    entry = row["presentation"][0]
    assert (entry["relation"], entry["fronts"], entry["why"]) == ("notation", [], "")


def test_the_presentation_form_carries_a_full_declaration_payload(tmp_path: Path):
    row = _row(_payload(tmp_path, {"name": PRESENTATION, "fronts": [CANONICAL]}))
    entry = row["presentation"][0]
    assert entry["source"]["path"] == "Paper.lean"
    assert "theorem printed" in entry["source"]["text"]
    # And it is not repeated as a bare supporting name it has no panel for.
    assert [s["name"] for s in row["supporting"]] == []


def test_the_presentation_form_precedes_the_canonical_one(tmp_path: Path):
    census_dir = tmp_path / "dev"
    census_dir.mkdir()
    path = census_dir / "paper-full-source-census.json"
    path.write_text(json.dumps(_census({"name": PRESENTATION, "fronts": [CANONICAL]})),
                    encoding="utf-8")
    (tmp_path / "Paper.lean").write_text(
        "theorem printed (x : Nat) : x = x := rfl\ntheorem main (x : Nat) : x = x := rfl\n",
        encoding="utf-8")
    packet = build_alignment_packet([path], root=tmp_path)
    body = packet.render_markdown()
    assert body.index("### Presentation Lean declarations") < body.index("### Canonical Lean")
    assert f"Fronts `{CANONICAL}` (unstated)." in body

    html = render_alignment_html(alignment_payload(packet))
    assert html.index("Presentation form (") < html.index("The API-canonical statement it fronts")


def test_a_delegating_front_is_read_from_the_graph(tmp_path: Path):
    graph = _graph(tmp_path, {PRESENTATION: [ENGINE], ENGINE: [CANONICAL], CANONICAL: []})
    row = _row(_payload(tmp_path, {"name": PRESENTATION, "fronts": [CANONICAL]}, graph=graph))
    assert row["presentation"][0]["frontsVerified"][CANONICAL] == {
        "state": "delegates", "depth": 2,
    }


def test_a_shared_proof_core_is_a_verdict_of_its_own(tmp_path: Path):
    """The Davis--Kahan shape: the presentation form never mentions the canonical
    statement, and both are specializations of one engine."""
    row = _row(_payload(tmp_path, {"name": PRESENTATION, "fronts": [CANONICAL]},
                        graph=_graph(tmp_path)))
    assert row["presentation"][0]["frontsVerified"][CANONICAL] == {
        "state": "shared-core", "via": ENGINE, "depth": 1,
    }


def test_shared_hypothesis_vocabulary_is_not_the_shared_core(tmp_path: Path):
    """Both statements take the same structure, so its projections are one edge
    from each.  The core is the engine underneath, not the accessor."""
    nodes = {
        PRESENTATION: ["Paper.Data.field", ENGINE],
        CANONICAL: ["Paper.Data.field", ENGINE],
        "Paper.Data.field": [], ENGINE: [],
    }
    graph = _graph(tmp_path, nodes)
    graph["table"]["Paper.Data"] = graph["table"][ENGINE].__class__.from_json(
        {"name": "Paper.Data", "module": "Paper", "kind": "inductive", "deps": []})
    row = _row(_payload(tmp_path, {"name": PRESENTATION, "fronts": [CANONICAL]}, graph=graph))
    assert row["presentation"][0]["frontsVerified"][CANONICAL]["via"] == ENGINE


def test_an_unrelated_front_is_reported_plainly(tmp_path: Path):
    row = _row(_payload(tmp_path, {"name": PRESENTATION, "fronts": ["Paper.unrelated"]},
                        graph=_graph(tmp_path)))
    assert row["presentation"][0]["frontsVerified"]["Paper.unrelated"]["state"] == "independent"


def test_a_front_the_graph_cannot_speak_for_is_unknown(tmp_path: Path):
    graph = _graph(tmp_path)
    entry = {"name": PRESENTATION, "fronts": [CANONICAL, "Paper.absent"]}
    assert _row(_payload(tmp_path, entry))["presentation"][0]["frontsVerified"] == {
        CANONICAL: {"state": "unknown"}, "Paper.absent": {"state": "unknown"},
    }
    verified = _row(_payload(tmp_path, entry, graph=graph))["presentation"][0]["frontsVerified"]
    assert verified["Paper.absent"] == {"state": "unknown"}
    assert verified[CANONICAL]["state"] == "shared-core"


def test_the_depth_cap_never_manufactures_independence(tmp_path: Path):
    """A chain longer than the search budget is unknown, not independent: the walk
    stopped early and has shown nothing."""
    chain = {PRESENTATION: ["Paper.d1"], "Paper.d1": ["Paper.d2"], "Paper.d2": ["Paper.d3"],
             "Paper.d3": ["Paper.d4"], "Paper.d4": ["Paper.d5"], "Paper.d5": [CANONICAL],
             CANONICAL: ["Paper.d6"], "Paper.d6": ["Paper.d7"], "Paper.d7": ["Paper.d8"],
             "Paper.d8": ["Paper.d9"], "Paper.d9": []}
    row = _row(_payload(tmp_path, {"name": PRESENTATION, "fronts": [CANONICAL]},
                        graph=_graph(tmp_path, chain)))
    assert row["presentation"][0]["frontsVerified"][CANONICAL] == {"state": "unknown"}


def test_a_presentation_form_is_not_duplicated_into_the_evidence_list(tmp_path: Path):
    census = _census({"name": PRESENTATION, "fronts": [CANONICAL]})
    census["items"][0]["semantic_review"]["clause_map"][0]["correspondence_declarations"] = [
        PRESENTATION, "Paper.witness",
    ]
    path = tmp_path / "dev" / "paper-full-source-census.json"
    path.parent.mkdir()
    path.write_text(json.dumps(census), encoding="utf-8")
    packet = build_alignment_packet([path], root=tmp_path)
    row = _row(alignment_payload(packet))
    assert [d["name"] for d in row["evidence"]] == ["Paper.witness"]
    assert [d["name"] for d in row["presentation"]] == [PRESENTATION]

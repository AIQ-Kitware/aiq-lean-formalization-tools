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

from leanq.statement import StatementBinder, StatementRecord

from aiq_lean_tools.correspondence import edges_of
from aiq_lean_tools.alignment import (
    _resolve_source_targets,
    _source_target_summary,
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


def _payload(tmp_path: Path, presentation: Any | None, *, graph: Any = None,
             statement_map: Any = None) -> dict[str, Any]:
    census = tmp_path / "dev" / "paper-full-source-census.json"
    census.parent.mkdir(exist_ok=True)
    census.write_text(json.dumps(_census(presentation)), encoding="utf-8")
    (tmp_path / "Paper.lean").write_text(
        "/-- Printed. -/\ntheorem printed (x : Nat) : x = x := rfl\n"
        "/-- Canonical. -/\ntheorem main (x : Nat) : x = x := rfl\n",
        encoding="utf-8")
    packet = build_alignment_packet([census], root=tmp_path, statement_map=statement_map)
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


def test_html_leads_with_the_reviewed_theorem_and_collapses_presentation(tmp_path: Path):
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
    assert html.index("Reviewed theorem (") < html.index("Readable presentation form (")
    assert 'Bridge / correspondence evidence (' in html
    assert 'data-direct-decls=' in html and 'data-evidence-decls=' in html


def test_source_math_targets_resolve_to_renderable_source_coordinates():
    review = {
        "source_fragments": [{"id": "setup", "role": "primary", "locator": {"marker": "S-1"}}],
        "clause_map": [{
            "source_clause": "A, A0, and E0",
            "lean_realization": "binders",
            "source_fragment": "setup",
            "source_targets": [
                {"kind": "math", "text": "A"},
                {"kind": "math", "text": "A_0"},
                {"kind": "math", "text": "E_0"},
            ],
        }],
    }
    sources = [{
        "id": "setup",
        "pinStatus": "current",
        "fragment": {"blocks": [
            {"kind": "paragraph", "spans": [
                {"kind": "text", "text": "Let "},
                {"kind": "math", "text": "A=A^*"},
                {"kind": "text", "text": " and "},
                {"kind": "math", "text": "E_0"},
            ]},
            {"kind": "display", "text": "A_0=E_0^* A E_0"},
        ]},
    }]
    resolutions = _resolve_source_targets(edges_of(review)[0], sources)
    assert [r["state"] for r in resolutions] == ["current", "current", "current"]
    assert resolutions[0]["matches"] == [{
        "kind": "math", "block": 0, "span": 1, "text": "A",
    }]
    assert resolutions[1]["matches"] == [{
        "kind": "math", "block": 1, "text": "A_0",
    }]
    assert resolutions[2]["matches"] == [{
        "kind": "math", "block": 0, "span": 3, "text": "E_0",
    }]
    assert _source_target_summary(resolutions) == "current"

    sources[0]["pinStatus"] = "moved"
    moved = _resolve_source_targets(edges_of(review)[0], sources)
    assert _source_target_summary(moved) == "source-drift"

    review["clause_map"][0]["source_targets"][0]["text"] = "B"
    missing = _resolve_source_targets(edges_of(review)[0], sources)
    assert missing[0]["state"] == "target-missing"
    assert _source_target_summary(missing) == "target-drift"


def test_alignment_viewer_opens_inherited_source_and_can_mark_source_targets(tmp_path: Path):
    html = render_alignment_html(_payload(tmp_path, None))
    assert '<details class="source-secondary" open>' in html
    assert 'function markSourceTargets' in html
    assert 'source-target-hit' in html


def test_clause_targets_are_resolved_into_elaborated_statement_segments(tmp_path: Path):
    census = _census(None)
    census["items"][0]["semantic_review"]["clause_map"][0]["lean_targets"] = [
        {"kind": "binder", "name": "hgap"}, {"kind": "result"},
    ]
    path = tmp_path / "dev" / "paper-full-source-census.json"
    path.parent.mkdir()
    path.write_text(json.dumps(census), encoding="utf-8")
    (tmp_path / "Paper.lean").write_text(
        "theorem main (hgap : True) : True := trivial\n", encoding="utf-8")
    record = StatementRecord(
        name=CANONICAL, module="Paper", kind="theorem", library="Paper", role="seed",
        signature="Paper.main (hgap : True) : True", type="True", type_expr_hash="7",
        binders=(StatementBinder(0, "hgap", "explicit", "True", ()),), result="True",
    )
    packet = build_alignment_packet([path], root=tmp_path, statement_map={CANONICAL: record})
    data = alignment_payload(packet)
    edge = _row(data)["edges"][0]
    assert edge["leanTargetSummary"] == "unpinned"
    assert edge["leanTargetResolutions"][0]["matches"] == [
        {"kind": "binder", "index": 0, "name": "hgap"}
    ]
    assert edge["leanTargetResolutions"][1]["matches"] == [{"kind": "result"}]
    html = render_alignment_html(data)
    assert 'class="lean-segment binder"' in html
    assert 'function markLeanTargets' in html
    assert 'Lean target drift' in html


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

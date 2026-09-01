import json
from pathlib import Path

from aiq_lean_tools.census import load_census
from aiq_lean_tools.cli import main
from aiq_lean_tools.semantic_review import load_semantic_review
from aiq_lean_tools.statement_pins import (
    census_pin_targets,
    check_pins,
    pin_targets,
    review_pin_targets,
    validate_pins,
)
from leanq.statement import StatementRecord, by_name


def _record(name, type_text, expr_hash, **kw):
    return StatementRecord(
        name=name, module="Paper.Main", kind="theorem", library="Paper", role="seed",
        type_deps=kw.get("type_deps", ()), type=type_text, signature=f"{name} : {type_text}",
        type_expr_hash=expr_hash,
    )


def _census_data(decl="Paper.main"):
    return {
        "schema_version": 1,
        "primary_source": {"citation": "Paper"},
        "status_definitions": {"implemented": "done"},
        "verification_definitions": {"proved_in_build": "yes"},
        "importance_definitions": {"headline": "main"},
        "items": [{
            "id": "r1", "title": "Headline", "source_anchor": "Theorem 1",
            "status": "implemented", "verification": "proved_in_build",
            "importance": "headline", "lean_declarations": [decl, "Paper.helper"],
            "semantic_review": {
                "group": "main", "group_title": "Main theorem", "claim": "claim",
                "canonical_declarations": [decl],
                "supporting_declarations": ["Paper.helper"],
                "context_declarations": [{"name": "Paper.IsGood", "mathematical_role": "predicate"}],
                "source_statement": {"setup": [], "hypotheses": ["x > 0"],
                                     "conclusions": ["claim"], "scope": []},
                "clause_map": [{"source_clause": "claim", "lean_realization": "claim",
                                "status": "claimed_exact"}],
            },
        }],
    }


def _sidecar(tmp_path: Path, records):
    path = tmp_path / "Paper.statements.jsonl"
    path.write_text("\n".join(json.dumps(r.to_json()) for r in records) + "\n")
    (tmp_path / "Paper.statements.jsonl.meta.json").write_text(
        json.dumps({"toolchain": "leanprover/lean4:v-test"})
    )
    return path


def test_pin_then_check_detects_drift(tmp_path: Path):
    census_path = tmp_path / "paper-full-source-census.json"
    census_path.write_text(json.dumps(_census_data()))
    census = load_census(census_path)
    targets = census_pin_targets(census)
    assert [t.declarations for t in targets] == [("Paper.main",)]

    records = by_name([_record("Paper.main", "∀ x, x > 0 → P x", "111"),
                       _record("Paper.helper", "Q", "222")])
    written, findings = pin_targets(targets, records, toolchain="v-test")
    assert written == 1 and findings == []
    pin = census.items[0]["semantic_review"]["statement_pins"][0]
    assert pin["declaration"] == "Paper.main"
    assert pin["type_expr_hash"] == "111"
    assert pin["toolchain"] == "v-test"
    assert validate_pins(census.items[0]["semantic_review"], claimed=["Paper.main"], location="r1") == []
    assert check_pins(targets, records) == []

    # same term, different rendering: a warning, not an error
    renamed = by_name([_record("Paper.main", "∀ y, y > 0 → P y", "111")])
    found = check_pins(targets, renamed)
    assert [f.code for f in found] == ["statement-text-drift"]
    assert found[0].level == "warning"

    # a changed hypothesis moves the structural hash: an error
    changed = by_name([_record("Paper.main", "∀ x, x > 1 → P x", "999")])
    found = check_pins(targets, changed)
    assert [(f.level, f.code) for f in found] == [("error", "statement-drift")]

    gone = by_name([])
    assert [f.code for f in check_pins(targets, gone)] == ["statement-pin-gone"]


def test_pin_validation_rejects_unclaimed_and_incomplete_pins():
    review = {"statement_pins": [
        {"declaration": "Paper.other", "type_expr_hash": "1", "type_text_sha256": "2"},
        {"declaration": "Paper.main", "type_expr_hash": "1"},
        {"declaration": "Paper.main", "type_expr_hash": "1", "type_text_sha256": "2"},
    ]}
    codes = sorted(f.message for f in validate_pins(review, claimed=["Paper.main"], location="r1"))
    assert any("not one the review claims" in m for m in codes)
    assert any("needs type_text_sha256" in m for m in codes)
    assert any("duplicate pin" in m for m in codes)


def test_census_validation_sees_bad_pins(tmp_path: Path):
    data = _census_data()
    data["items"][0]["semantic_review"]["statement_pins"] = [
        {"declaration": "Paper.nope", "type_expr_hash": "1", "type_text_sha256": "2"}
    ]
    path = tmp_path / "paper-full-source-census.json"
    path.write_text(json.dumps(data))
    findings = load_census(path).validate()
    assert any(f.code == "statement-pin" for f in findings)


def test_cli_pin_and_check_with_explicit_sidecar(tmp_path: Path, capsys):
    census_path = tmp_path / "paper-full-source-census.json"
    census_path.write_text(json.dumps(_census_data()))
    sidecar = _sidecar(tmp_path, [_record("Paper.main", "∀ x, x > 0 → P x", "111"),
                                   _record("Paper.helper", "Q", "222")])
    assert main(["alignment", "pin", str(census_path), "--sidecar", str(sidecar), "--note", "first review"]) == 0
    pins = json.loads(census_path.read_text())["items"][0]["semantic_review"]["statement_pins"]
    assert pins[0]["note"] == "first review"
    assert pins[0]["toolchain"] == "leanprover/lean4:v-test"
    assert main(["alignment", "check", str(census_path), "--sidecar", str(sidecar)]) == 0

    drifted = _sidecar(tmp_path, [_record("Paper.main", "∀ x, x > 1 → P x", "999")])
    assert main(["alignment", "check", str(census_path), "--sidecar", str(drifted)]) == 1
    out = capsys.readouterr().out
    assert "statement-drift" in out
    # re-pinning without a note keeps the old note
    assert main(["alignment", "pin", str(census_path), "--sidecar", str(drifted)]) == 0
    pins = json.loads(census_path.read_text())["items"][0]["semantic_review"]["statement_pins"]
    assert pins[0]["type_expr_hash"] == "999" and pins[0]["note"] == "first review"


def test_review_document_rows_pin_every_declaration(tmp_path: Path):
    review = {
        "schema_version": 1, "review_kind": "semantic", "paper": {"citation": "P"},
        "relation_definitions": {"exact": "same"},
        "rows": [{"id": "T1", "title": "T", "source_anchor": "Theorem 1",
                  "lean_declarations": ["Paper.main", "Paper.helper"],
                  "clauses": [{"source_clause": "a", "lean_clause": "b", "relation": "exact"}]}],
    }
    path = tmp_path / "paper-result-semantic-review.json"
    path.write_text(json.dumps(review))
    doc = load_semantic_review(path)
    targets = review_pin_targets(doc)
    assert targets[0].declarations == ("Paper.main", "Paper.helper")
    records = by_name([_record("Paper.main", "A", "1"), _record("Paper.helper", "B", "2")])
    written, findings = pin_targets(targets, records)
    assert written == 2 and not findings
    doc.write()
    assert load_semantic_review(path).validate(check_companion=False) == []
    assert main(["alignment", "check", str(path), "--sidecar",
                 str(_sidecar(tmp_path, [_record("Paper.main", "A", "1")]))]) == 1


def test_render_with_sidecar_reports_undisclosed_constants(tmp_path: Path):
    census_path = tmp_path / "paper-full-source-census.json"
    census_path.write_text(json.dumps(_census_data()))
    main_rec = StatementRecord(
        name="Paper.main", module="Paper.Main", kind="theorem", library="Paper", role="seed",
        type_deps=("Paper.IsGood", "Paper.Hidden", "IsSelfAdjoint"), type="T",
        signature="Paper.main (x : ℝ) (h : Paper.Hidden x) : Paper.IsGood x", type_expr_hash="7",
    )
    good = StatementRecord(name="Paper.IsGood", module="Paper.Defs", kind="def", library="Paper",
                           type="ℝ → Prop", signature="Paper.IsGood (x : ℝ) : Prop", type_expr_hash="8")
    hidden = StatementRecord(name="Paper.Hidden", module="Paper.Defs", kind="def", library="Paper",
                             type="ℝ → Prop", signature="Paper.Hidden (x : ℝ) : Prop", type_expr_hash="9")
    sa = StatementRecord(name="IsSelfAdjoint", module="Mathlib.Algebra.Star.SelfAdjoint", kind="def",
                         library="Mathlib", role="boundary", boundary=True, type="Prop", type_expr_hash="3")
    stale = StatementRecord(name="Paper.Stale", module="Paper.Defs", kind="def", library="Paper",
                            type="Prop", type_expr_hash="10")
    explain = StatementRecord(name="Paper.isGood_iff", module="Paper.Defs", kind="theorem",
                              library="Paper", type="Prop", type_expr_hash="11")
    sidecar = _sidecar(tmp_path, [main_rec, good, hidden, sa, stale, explain])
    data = json.loads(census_path.read_text())
    data["items"][0]["semantic_review"]["context_declarations"] += [
        {"name": "Paper.Stale", "mathematical_role": "no longer used"},
        {"name": "Paper.isGood_iff", "mathematical_role": "unfolds IsGood"},
    ]
    census_path.write_text(json.dumps(data))
    out = tmp_path / "packet.md"
    assert main(["alignment", "render", str(census_path), "--sidecar", str(sidecar), "-o", str(out)]) == 0
    text = out.read_text()
    assert "Paper.main (x : ℝ) (h : Paper.Hidden x) : Paper.IsGood x" in text
    assert "dictionary does not disclose:** `Paper.Hidden`" in text
    assert "Boundary vocabulary: `IsSelfAdjoint`" in text
    assert "statement pin: unpinned" in text
    assert "Dictionary definitions this statement never reaches: `Paper.Stale`" in text
    assert "Paper.isGood_iff" not in text.split("Dictionary definitions")[1].split("\n")[0]
    assert "elaborated" in text  # supporting declaration status


def test_html_page_embeds_closure_and_proof_panel(tmp_path: Path):
    from leanq.index import Decl

    census_path = tmp_path / "paper-full-source-census.json"
    census_path.write_text(json.dumps(_census_data()))
    main_rec = StatementRecord(
        name="Paper.main", module="Paper.Main", kind="theorem", library="Paper", role="seed",
        type_deps=("Paper.IsGood", "Paper.Hidden"), type="T",
        signature="Paper.main (x : ℝ) (h : Paper.Hidden x) : Paper.IsGood x", type_expr_hash="7",
    )
    good = StatementRecord(name="Paper.IsGood", module="Paper.Defs", kind="def", library="Paper",
                           type="ℝ → Prop", signature="Paper.IsGood (x : ℝ) : Prop", type_expr_hash="8")
    hidden = StatementRecord(name="Paper.Hidden", module="Paper.Defs", kind="def", library="Paper",
                             type="ℝ → Prop", signature="Paper.Hidden (x : ℝ) : Prop", type_expr_hash="9",
                             docstring="A <hidden> hypothesis & more")
    sidecar = _sidecar(tmp_path, [main_rec, good, hidden])
    graph = {"nodes": [
        Decl(name="Paper.main", module="Paper.Main", kind="theorem", is_prop=None, prop_valued=None,
             sorried=None, line=3, axioms=None, deps=("Paper.IsGood", "Paper.lemma", "Mathlib.x"),
             library="Paper", type_deps=("Paper.IsGood",)).to_json(),
        Decl(name="Paper.IsGood", module="Paper.Defs", kind="def", is_prop=None, prop_valued=None,
             sorried=None, line=1, axioms=None, deps=(), library="Paper").to_json(),
        Decl(name="Paper.lemma", module="Paper.Lemmas", kind="theorem", is_prop=None, prop_valued=None,
             sorried=None, line=1, axioms=None, deps=("Paper.IsGood",), library="Paper").to_json(),
    ]}
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps(graph))
    payload_path = tmp_path / "page.json"
    assert main(["alignment", "html", str(census_path), "--sidecar", str(sidecar), "--graph",
                 str(graph_path), "--json", "-o", str(payload_path)]) == 0
    payload = json.loads(payload_path.read_text())
    row = payload["papers"][0]["rows"][0]
    decl = row["canonical"][0]
    assert decl["undisclosed"] == ["Paper.Hidden"]
    assert decl["pinStatus"] == "unpinned" and row["pinSummary"] == "unpinned"
    assert decl["proof"]["nodeCount"] == 3
    assert [d["role"] for d in decl["proof"]["direct"]] == ["type", "proof"]
    assert decl["proof"]["byLibrary"] == {"Paper": 3}
    assert "Paper.Hidden" in payload["records"]
    assert row["context"][0]["name"] == "Paper.IsGood" and row["context"][0]["reachable"] is True

    html_path = tmp_path / "page.html"
    assert main(["alignment", "html", str(census_path), "--sidecar", str(sidecar), "-o", str(html_path)]) == 0
    text = html_path.read_text()
    embedded = text.split('id="payload">')[1].split("</script>")[0]
    assert "<" not in embedded and ">" not in embedded  # angle brackets are escaped in the payload
    assert "\\u003chidden\\u003e" in embedded
    assert main(["alignment", "html", str(census_path), "--sidecar", str(sidecar), "-o", str(html_path), "--check"]) == 0

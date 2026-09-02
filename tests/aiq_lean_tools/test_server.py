"""The parts of the server that decide what may be written, and what a file is."""

from __future__ import annotations

import json

import pytest

from aiq_lean_tools.server.edits import EditRefused, apply_edit, check_writable, read_journal
from aiq_lean_tools.server.registry import _view_specs


def _census(tmp_path):
    path = tmp_path / "demo-full-source-census.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "primary_source": "Demo",
                "completion_certification": "accepted",
                "items": [
                    {
                        "id": "D-1",
                        "title": "Demo row",
                        "status": "compiled_exact",
                        "notes": "before",
                        "semantic_review": {
                            "clause_map": [{"source_clause": "c", "lean_realization": "l", "status": "claimed_exact"}]
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "pointer",
    [
        "/items/0/notes",
        "/items/12/next_action",
        "/items/0/semantic_review/note",
        "/items/0/semantic_review/clause_map/3/status",
    ],
)
def test_reviewer_fields_are_writable(pointer):
    check_writable(pointer)


@pytest.mark.parametrize(
    "pointer",
    [
        "/items/0/status",  # the row's own verdict
        "/items/0/verification",
        "/items/0/completion_certification",
        "/semantic_review_sweep/canonical_evidence_sha256",
        "/items/0/semantic_review/statement_pins/0/type_expr_hash",
        "/items/0/lean_declarations/0",
        "",
    ],
)
def test_gated_fields_are_refused(pointer):
    with pytest.raises(EditRefused):
        check_writable(pointer)


def test_edit_writes_value_and_journals_it(tmp_path):
    path = _census(tmp_path)
    result = apply_edit(path, "/items/0/notes", "after", author="tester", root=tmp_path)

    assert json.loads(path.read_text())["items"][0]["notes"] == "after"
    assert result.before == "before"
    assert result.digest_before != result.digest_after

    entries = read_journal(tmp_path)
    assert len(entries) == 1
    assert entries[0]["pointer"] == "/items/0/notes"
    assert entries[0]["before"] == "before"
    assert entries[0]["author"] == "tester"


def test_edit_warns_when_the_document_carries_an_acceptance(tmp_path):
    path = _census(tmp_path)
    result = apply_edit(path, "/items/0/notes", "after", root=tmp_path)
    assert result.acceptance_warnings
    assert "completion_certification" in result.acceptance_warnings[0]


def test_edit_does_not_restamp_the_acceptance(tmp_path):
    path = _census(tmp_path)
    apply_edit(path, "/items/0/notes", "after", root=tmp_path)
    # The acceptance is left exactly as it was: going stale is the gate's job to
    # notice, not this module's job to paper over.
    assert json.loads(path.read_text())["completion_certification"] == "accepted"


def test_edit_refuses_a_type_change(tmp_path):
    path = _census(tmp_path)
    with pytest.raises(EditRefused):
        apply_edit(path, "/items/0/notes", 42, root=tmp_path)


def test_edit_leaves_the_file_untouched_when_refused(tmp_path):
    path = _census(tmp_path)
    original = path.read_text()
    with pytest.raises(EditRefused):
        apply_edit(path, "/items/0/status", "nonsense", root=tmp_path)
    assert path.read_text() == original


def test_markers_separate_the_schemas():
    specs = {s.name: s for s in _view_specs()}
    census = {"items": [], "primary_source": "x"}
    review = {"rows": [], "review_kind": "result-semantic-review"}
    inventory = {"results": [], "result_count": 29}

    assert specs["census"].claims(census)
    assert not specs["review"].claims(census)
    assert not specs["coverage"].claims(census)

    assert specs["review"].claims(review)
    assert not specs["census"].claims(review)

    assert specs["coverage"].claims(inventory)
    assert not specs["census"].claims(inventory)

    # A review needs both markers: `rows` alone is not decisive.
    assert not specs["review"].claims({"rows": []})

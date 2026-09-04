"""The cross-paper headline index.

Named for the module under test rather than `test_headlines.py`: pytest imports
these directories without packages, and `tests/leanq/test_headlines.py` already
owns that module name.
"""

from __future__ import annotations

import json

import pytest

from aiq_lean_tools.server.declaration import DeclarationService
from aiq_lean_tools.server.headlines import HeadlineService
from aiq_lean_tools.server.registry import Catalog

LEAN = """\
import Mathlib

namespace Demo

/-- The demo sine bound, as written. -/
theorem sinBound (x : Nat) : x = x := by
  rfl

end Demo
"""


def _write_census(path, *, source, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "primary_source": source, "items": items}, indent=2) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "lakefile.toml").write_text("name = \"demo\"\n", encoding="utf-8")
    (tmp_path / "Demo").mkdir()
    (tmp_path / "Demo" / "Sine.lean").write_text(LEAN, encoding="utf-8")
    # "alpha" sorts before "beta", so file order and catalog order agree here;
    # the ordering assertions below rely on that being deliberate.
    _write_census(
        tmp_path / "dev" / "alpha-full-source-census.json",
        source={"citation": "Alpha 1970"},
        items=[
            {
                "id": "A-1",
                "title": "Alpha headline",
                "section": 2,
                "source_anchor": "Section 2",
                "summary": "The alpha bound.",
                "next_action": "none",
                "status": "compiled_exact",
                "verification": "proved_in_build",
                "completion_certification": "accepted",
                "importance": "headline",
                "lean_declarations": ["Demo.sinBound", "Demo.missing"],
                "semantic_review": {
                    "group": "A-group",
                    "claim": "Alpha claims a bound.",
                    "canonical_declarations": ["Demo.sinBound"],
                    "supporting_declarations": ["Demo.helper"],
                    "clause_map": [
                        {"source_clause": "c1", "status": "claimed_exact"},
                        {"source_clause": "c2", "status": "scope_companion"},
                        {"source_clause": "c3"},
                    ],
                },
            },
            {"id": "A-2", "title": "Alpha technical", "importance": "technical"},
        ],
    )
    _write_census(
        tmp_path / "dev" / "beta-full-source-census.json",
        source={"citation": "Beta 2020"},
        items=[
            # No semantic review at all: most censuses in the repository are
            # like this, and the row's own declaration list is then canonical.
            {"id": "B-1", "title": "Beta headline", "importance": "headline",
             "lean_declarations": ["Demo.sinBound"]},
            {"id": "B-2", "title": "Beta major", "importance": "major"},
        ],
    )
    return tmp_path


def _service(root):
    return HeadlineService(root, Catalog(root), DeclarationService(root))


def test_entries_are_ordered_by_paper_then_file(repo):
    rows = _service(repo).entries()
    assert [(r["slug"], r["id"]) for r in rows] == [
        ("alpha-full-source-census", "A-1"),
        ("beta-full-source-census", "B-1"),
    ]
    assert rows[0]["paper"] == "Alpha 1970"


def test_only_the_requested_importances_are_returned(repo):
    service = _service(repo)
    assert [r["id"] for r in service.entries(("technical", "major"))] == ["A-2", "B-2"]
    assert [r["id"] for r in service.entries(("headline", "major"))] == ["A-1", "B-1", "B-2"]


def test_row_fields_are_carried_across(repo):
    row = _service(repo).entries()[0]
    assert row["claim"] == "Alpha claims a bound."
    assert row["group"] == "A-group"
    assert row["certification"] == "accepted"
    assert row["section"] == "2"  # printed as written, even when the JSON has an int
    assert row["supporting"] == ["Demo.helper"]


def test_open_clauses_are_the_ones_a_reviewer_graded_as_unsettled(repo):
    row = _service(repo).entries()[0]
    assert row["clauseCount"] == 3
    # `scope_companion` is a stated verdict that is not settled. A clause with no
    # status is not a third settled one and not an open one either: it is
    # ungraded, and it is counted separately. The distinction earns its keep on
    # the reviews kept beside a census, which grade by `relation` and write no
    # `status` at all -- reading that silence as "not established" would report
    # forty-three reviewed rows as failing their own review.
    assert row["clauseOpen"] == 1
    assert row["clauseUnstated"] == 1


def test_canonical_declarations_carry_the_lean_statement(repo):
    row = _service(repo).entries()[0]
    assert [d["name"] for d in row["canonical"]] == ["Demo.sinBound"]
    decl = row["canonical"][0]
    assert decl["path"] == "Demo/Sine.lean"
    assert decl["line"] == 6
    assert decl["kind"] == "theorem"
    assert "theorem sinBound (x : Nat) : x = x" in decl["statement"]
    assert "The demo sine bound" in decl["statement"]


def test_a_row_without_a_semantic_review_falls_back_to_its_declarations(repo):
    row = _service(repo).entries()[1]
    assert [d["name"] for d in row["canonical"]] == ["Demo.sinBound"]
    assert row["claim"] == ""
    assert row["clauseCount"] == 0


def test_a_declaration_with_no_source_is_reported_without_a_location(repo):
    # `Demo.missing` is named by a row whose semantic review overrides it, so
    # reach it through a row that has no review of its own.
    _write_census(
        repo / "dev" / "beta-full-source-census.json",
        source={"citation": "Beta 2020"},
        items=[{"id": "B-1", "importance": "headline", "lean_declarations": ["Demo.missing"]}],
    )
    decl = _service(repo).entries()[1]["canonical"][0]
    assert decl == {"name": "Demo.missing", "kind": "", "module": "", "docstring": "",
                    "path": "", "line": 0, "statement": ""}


def test_entry_finds_a_row_at_any_importance(repo):
    service = _service(repo)
    assert service.entry("alpha-full-source-census", "A-1") == service.entries()[0]
    assert service.entry("alpha-full-source-census", "A-2")["title"] == "Alpha technical"
    assert service.entry("alpha-full-source-census", "B-1") is None
    assert service.entry("nope", "A-1") is None


def test_a_failing_declaration_service_only_costs_the_lean_evidence(repo):
    class Broken:
        def source_index(self):
            raise RuntimeError("scan not finished")

        def statements(self):
            raise RuntimeError("no sidecars")

    rows = HeadlineService(repo, Catalog(repo), Broken()).entries()
    assert [r["id"] for r in rows] == ["A-1", "B-1"]
    assert rows[0]["canonical"][0] == {"name": "Demo.sinBound", "kind": "", "module": "",
                                       "docstring": "", "path": "", "line": 0, "statement": ""}


def test_revision_changes_when_a_census_is_rewritten(repo):
    service = _service(repo)
    before = service.revision()
    assert [entry[0] for entry in before] == [
        "alpha-full-source-census",
        "beta-full-source-census",
    ]
    assert service.revision() == before

    path = repo / "dev" / "beta-full-source-census.json"
    _write_census(path, source={"citation": "Beta 2020"},
                  items=[{"id": "B-1", "title": "Beta headline, renamed", "importance": "headline"}])
    assert service.revision() != before
    # The parsed-JSON cache is keyed on the same stamp, so the rewrite is seen.
    assert service.entries()[1]["title"] == "Beta headline, renamed"

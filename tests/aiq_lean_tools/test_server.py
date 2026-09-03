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


# -- declaration audit ---------------------------------------------------

LEAN = '''import Mathlib

namespace Demo

/-- A docstring that belongs to the theorem below. -/
theorem alpha (n : Nat) : n = n := by
  rfl

/-- The next one, which must not be swallowed by the previous. -/
theorem beta (n : Nat) : n + 0 = n := by
  simp

end Demo
'''


def test_full_declaration_includes_docstring_and_proof(tmp_path):
    from aiq_lean_tools.lean_source import full_declaration_text as _full_declaration

    path = tmp_path / "Demo.lean"
    path.write_text(LEAN, encoding="utf-8")
    # `theorem alpha` is on line 6 (1-indexed).
    text = _full_declaration(path, 6)
    assert text is not None
    assert "A docstring that belongs" in text, "the docstring above the theorem is part of it"
    assert "theorem alpha" in text
    assert "rfl" in text, "the proof body is what makes this auditable"
    # Scanning forward from the docstring used to stop at the theorem itself,
    # returning the docstring alone.
    assert text.strip() != "/-- A docstring that belongs to the theorem below. -/"
    assert "theorem beta" not in text, "must stop at the next declaration"


def test_full_declaration_handles_a_missing_file(tmp_path):
    from aiq_lean_tools.lean_source import full_declaration_text as _full_declaration

    assert _full_declaration(tmp_path / "nope.lean", 3) is None


def test_graph_name_resolution_tries_the_qualified_spelling(tmp_path):
    from aiq_lean_tools.server.declaration import DeclarationService

    svc = DeclarationService(tmp_path)
    svc.graph = lambda: {"table": {"TauCeti.Demo.alpha": object(), "Other.beta": object()}}

    # Censuses use the short name; the graph index stores the qualified one.
    assert svc.resolve_graph_name("Demo.alpha") == "TauCeti.Demo.alpha"
    assert svc.resolve_graph_name("TauCeti.Demo.alpha") == "TauCeti.Demo.alpha"
    assert svc.resolve_graph_name("Missing.gamma") is None


def test_graph_name_resolution_refuses_an_ambiguous_suffix(tmp_path):
    from aiq_lean_tools.server.declaration import DeclarationService

    svc = DeclarationService(tmp_path)
    svc.graph = lambda: {"table": {"A.thing": object(), "B.thing": object()}}
    assert svc.resolve_graph_name("thing") is None


def test_graph_status_reports_absence_rather_than_guessing(tmp_path):
    from aiq_lean_tools.server.declaration import DeclarationService

    status = DeclarationService(tmp_path).graph_status()
    assert status["present"] is False


def test_theme_is_injected_before_the_viewer_stylesheet():
    from aiq_lean_tools.viewer import _with_theme

    page = "<html><head><style>body{color:red}</style></head><body></body></html>"
    out = _with_theme(page)
    # The viewer's own rules must still come last so it keeps what it styled.
    assert out.index('id="aiq-theme"') < out.index("body{color:red}")
    assert "color-scheme" in out


# -- readiness and caching ----------------------------------------------

def test_readiness_reports_progress_and_keeps_failures():
    from aiq_lean_tools.server.warmup import Readiness

    r = Readiness()
    r.declare("a", "Stage A", "the A view")
    r.declare("b", "Stage B", "the B view")

    assert r.as_json()["ready"] is False
    assert r.as_json()["done"] == 0

    r.run("a", lambda: [1, 2, 3], describe=lambda v: f"{len(v)} things")
    assert r.is_ready("a")
    assert r.stages["a"].detail == "3 things"
    assert r.as_json()["done"] == 1
    assert r.as_json()["ready"] is False

    # A failing stage must not take the server down, and must say why.
    def boom():
        raise RuntimeError("no graph on disk")

    r.run("b", boom)
    assert r.stages["b"].state == "failed"
    assert "no graph on disk" in r.stages["b"].detail
    assert r.is_ready("b") is False
    # Failed still counts as finished: the UI stops waiting on it.
    assert r.as_json()["ready"] is True


def test_readiness_names_the_stage_in_flight():
    from aiq_lean_tools.server.warmup import Readiness

    r = Readiness()
    r.declare("slow", "Scanning Lean sources", "source snippets")
    seen = {}

    def work():
        seen["working"] = r.as_json()["working"]
        return 1

    r.run("slow", work)
    assert seen["working"] == "Scanning Lean sources"


def test_proof_closure_is_cached_per_declaration(tmp_path):
    from aiq_lean_tools.server.declaration import DeclarationService

    svc = DeclarationService(tmp_path)
    calls = []

    def fake_payload(graph, name):
        calls.append(name)
        return {"nodeCount": 1}

    import aiq_lean_tools.alignment as alignment

    original = alignment._proof_payload
    alignment._proof_payload = fake_payload
    try:
        svc._graph_stamp = ("stamp",)
        assert svc._proof_for({}, "A.thing")["nodeCount"] == 1
        assert svc._proof_for({}, "A.thing")["nodeCount"] == 1
        assert calls == ["A.thing"], "walking the graph twice for one target is the cost being avoided"

        # A rebuilt graph must invalidate: a stale closure is worse than a slow one.
        svc._graph_stamp = ("newer",)
        svc._proof_for({}, "A.thing")
        assert calls == ["A.thing", "A.thing"]
    finally:
        alignment._proof_payload = original


# -- discovery walk ------------------------------------------------------

def test_pruned_discovery_matches_a_plain_glob(tmp_path):
    """The pruned walk must find exactly what root.glob found, and no more."""
    from aiq_lean_tools.workspace import _discovery_skip_parts, _glob_unique

    (tmp_path / "dev").mkdir()
    (tmp_path / "dev" / "a-full-source-census.json").write_text("{}")
    (tmp_path / "nested" / "deep").mkdir(parents=True)
    (tmp_path / "nested" / "deep" / "b-source-census.json").write_text("{}")
    (tmp_path / "unrelated.json").write_text("{}")
    # A skipped directory must not contribute, however many files it holds.
    (tmp_path / ".lake" / "packages").mkdir(parents=True)
    (tmp_path / ".lake" / "packages" / "c-full-source-census.json").write_text("{}")

    patterns = ("**/*full-source-census.json", "**/*source-census.json")

    def plain(root, pats):
        skip = _discovery_skip_parts(root)
        found = set()
        for pattern in pats:
            for path in root.glob(pattern):
                if path.is_file() and not skip.intersection(path.relative_to(root).parts):
                    found.add(path.resolve())
        return sorted(found)

    assert _glob_unique(tmp_path, patterns) == plain(tmp_path, patterns)
    names = {p.name for p in _glob_unique(tmp_path, patterns)}
    assert names == {"a-full-source-census.json", "b-source-census.json"}


def test_discovery_star_does_not_cross_a_separator(tmp_path):
    """`**/*literature*.json` matches a filename, not any path containing it."""
    from aiq_lean_tools.workspace import _glob_unique

    (tmp_path / "literature_notes").mkdir()
    (tmp_path / "literature_notes" / "other.json").write_text("{}")
    (tmp_path / "my-literature-index.json").write_text("{}")

    names = {p.name for p in _glob_unique(tmp_path, ("**/*literature*.json",))}
    assert names == {"my-literature-index.json"}, "a single fnmatch over the whole path would also match other.json"


def test_workspace_accepts_an_injected_source_index(tmp_path):
    """The server hands in the scan it already has instead of repeating it."""
    import inspect

    from aiq_lean_tools.workspace import FormalizationWorkspace

    for name in ("overview", "payload", "render_html"):
        assert "source_index" in inspect.signature(getattr(FormalizationWorkspace, name)).parameters


# ---------------------------------------------------------------------------
# The alignment view, served


def _alignment_repo(tmp_path):
    """A minimal repository: one census, one review, one marked source passage."""
    (tmp_path / "prose").mkdir()
    (tmp_path / "prose" / "paper.tex").write_text(
        "% S-CERT-CLAIM-BEGIN T-1\n% S-CERT-SOURCE-BEGIN\n"
        "Assume a gap $\\delta>0$.  Then the estimate holds.\n"
        "% S-CERT-SOURCE-END\n% S-CERT-CLAIM-END T-1\n",
        encoding="utf-8",
    )
    (tmp_path / "prose" / "literature.json").write_text(json.dumps({
        "version": 1,
        "reconstruction": {"note_root": "."},
        "works": {"Paper": {
            "title": "A paper", "authors": ["A. Author"], "year": 1970, "kind": "paper",
            "target_note": "paper.tex",
            "source_document": {"marker_prefix": "S-CERT", "format": "tex"},
        }},
    }), encoding="utf-8")
    (tmp_path / "dev").mkdir()
    (tmp_path / "dev" / "paper-full-source-census.json").write_text(json.dumps({
        "schema_version": 1,
        "primary_source": {"citation": "A. Author 1970"},
        "status_definitions": {"done": "d"},
        "verification_definitions": {"proved_in_build": "y"},
        "importance_definitions": {"headline": "h"},
        "items": [{
            "id": "T-1", "title": "The estimate", "source_anchor": "Theorem 1",
            "status": "done", "verification": "proved_in_build", "importance": "headline",
            "lean_declarations": ["Paper.main"],
            "semantic_review": {
                "group": "g", "group_title": "The estimate", "claim": "c",
                "canonical_declarations": ["Paper.main"],
                "source_fragments": [{"id": "printed", "role": "primary",
                                      "locator": {"document": "Paper", "marker": "T-1"}}],
                "source_statement": {"setup": [], "hypotheses": ["gap"],
                                     "conclusions": ["bound"], "scope": []},
                "clause_map": [{"source_clause": "the estimate", "lean_realization": "Paper.main",
                                "status": "claimed_exact", "source_fragment": "printed",
                                "source_excerpt": "Then the estimate holds"}],
            },
        }],
    }), encoding="utf-8")
    return tmp_path


def test_alignment_is_served_beside_the_census_it_reads(tmp_path):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from aiq_lean_tools.server.app import create_app

    root = _alignment_repo(tmp_path)
    with TestClient(create_app(root)) as client:
        catalog = client.get("/api/catalog").json()
        slugs = {(d["view"], d["slug"]) for d in catalog["documents"]}
        assert ("alignment", "paper-full-source-census") in slugs
        assert ("census", "paper-full-source-census") in slugs

        # The literal route must win over `/view/{view}/{slug}`.
        page = client.get("/view/alignment/paper-full-source-census")
        assert page.status_code == 200
        assert b"Assume a gap" in page.content
        assert b"data:font/woff2;base64," in page.content

        payload = client.get("/api/payload/alignment/paper-full-source-census").json()
        row = payload["papers"][0]["rows"][0]
        assert row["sources"][0]["fragment"]["blocks"]
        assert client.get("/api/rows/alignment/paper-full-source-census").json()["rows"] == [
            {"id": "T-1", "title": "The estimate"}
        ]
        assert client.get("/view/alignment/no-such-census").status_code == 404


def test_a_served_page_is_rerendered_only_when_its_inputs_change(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from aiq_lean_tools.server.app import create_app

    root = _alignment_repo(tmp_path)
    with TestClient(create_app(root)) as client:
        first = client.get("/view/alignment/paper-full-source-census")
        etag = first.headers["etag"]
        again = client.get("/view/alignment/paper-full-source-census",
                           headers={"if-none-match": etag})
        assert again.status_code == 304

        # Editing the source document changes the page, so it must change the tag.
        tex = root / "prose" / "paper.tex"
        tex.write_text(tex.read_text().replace("the estimate holds", "a weaker estimate holds"),
                       encoding="utf-8")
        edited = client.get("/view/alignment/paper-full-source-census",
                            headers={"if-none-match": etag})
        assert edited.status_code == 200
        assert b"a weaker estimate holds" in edited.content


# -- served evidence must not outlive the evidence it displays --------------

def _sidecar(root, name, type_text, hash_text):
    """One statement sidecar holding a single elaborated declaration."""
    (root / ".leanq").mkdir(exist_ok=True)
    path = root / ".leanq" / "paper.statements-main.jsonl"
    path.write_text(json.dumps({
        "name": name, "module": "Paper", "kind": "theorem", "role": "seed",
        "type": type_text, "signature": type_text, "typeExprHash": hash_text,
    }) + "\n", encoding="utf-8")
    return path


def test_statement_revision_changes_when_a_type_changes_but_the_count_does_not(tmp_path):
    """A record count is not a revision.

    Re-elaborating a theorem whose statement changed leaves the number of
    records identical, so anything keyed on the count kept serving the previous
    signature. This is the exact shape of that failure.
    """
    from aiq_lean_tools.server.declaration import DeclarationService

    svc = DeclarationService(tmp_path)
    _sidecar(tmp_path, "Paper.main", "a = b", "h1")
    first = svc.statement_revision()
    assert svc.detail("Paper.main", with_proof=False)["elaborated"]["type"] == "a = b"

    _sidecar(tmp_path, "Paper.main", "a = c", "h2")
    assert len(svc.statements()) == 1, "the count is unchanged, which is the point"
    assert svc.statement_revision() != first
    assert svc.detail("Paper.main", with_proof=False)["elaborated"]["type"] == "a = c"


def test_graph_revision_changes_when_edges_change_but_nodes_do_not(tmp_path):
    """The graph path is constant, so keying on it invalidates nothing."""
    from aiq_lean_tools.server.declaration import DeclarationService, GRAPH_PATH

    svc = DeclarationService(tmp_path)
    path = tmp_path / GRAPH_PATH
    path.parent.mkdir(parents=True)

    def write(edges):
        path.write_text(json.dumps({
            "nodes": [{"name": "Paper.main"}, {"name": "Paper.helper"}],
            "edges": edges,
        }), encoding="utf-8")

    write([])
    first = svc.graph_revision()
    assert first != ()
    write([{"src": "Paper.main", "dst": "Paper.helper"}])
    assert svc.graph_revision() != first, "same node count, different graph"


def test_the_alignment_payload_cache_follows_the_statement_revision(tmp_path):
    from aiq_lean_tools.server.alignment import AlignmentService
    from aiq_lean_tools.server.declaration import DeclarationService

    root = _alignment_repo(tmp_path)
    decls = DeclarationService(root)
    service = AlignmentService(root, decls)
    census = root / "dev" / "paper-full-source-census.json"

    _sidecar(root, "Paper.main", "‖E‖ ≤ δ", "h1")
    first, _ = service.payload(census)
    assert first["records"]["Paper.main"]["type"] == "‖E‖ ≤ δ"

    _sidecar(root, "Paper.main", "‖E‖ ≤ 2 * δ", "h2")
    second, _ = service.payload(census)
    assert second["records"]["Paper.main"]["type"] == "‖E‖ ≤ 2 * δ", \
        "the served payload showed the statement the theorem used to have"


def test_a_served_page_follows_an_edited_lean_source(tmp_path):
    """The Lean scan is cached; it must not be cached past an edit."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from aiq_lean_tools.server.app import create_app

    root = _alignment_repo(tmp_path)
    (root / "Paper").mkdir()
    lean = root / "Paper" / "Main.lean"
    lean.write_text("theorem Paper.main : True := trivial\n", encoding="utf-8")
    with TestClient(create_app(root)) as client:
        first = client.get("/view/alignment/paper-full-source-census")
        assert b"trivial" in first.content
        lean.write_text("theorem Paper.main : True := by exact trivial\n", encoding="utf-8")
        # What the background watcher does on its slow cadence: recompute the
        # source revision without waiting out the staleness TTL.
        client.app.state.declarations.rescan_sources()
        second = client.get("/view/alignment/paper-full-source-census",
                            headers={"if-none-match": first.headers["etag"]})
        assert second.status_code == 200, "the tag survived an edit to the Lean source"
        assert b"by exact trivial" in second.content

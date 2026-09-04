"""Resolving an identifier the way the file it was read in resolves it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from aiq_lean_tools.lean_source import scan_lean_project
from aiq_lean_tools.server.symbols import (
    DOCSTRING_LIMIT,
    Symbol,
    resolve_symbol,
    statement_identifiers,
)

SECTION_TWO = "DavisKahan.Sources.DavisKahan1970.SectionTwo"


@dataclass(frozen=True)
class _Record:
    """Stands in for a `leanq` statement sidecar record."""

    kind: str = "theorem"
    module: str = ""
    docstring: str = ""
    signature: str = ""
    type: str = ""
    missing: bool = False


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path):
    # A repository marker, so the scan's root is this tree and not whatever
    # encloses the pytest temp directory.
    _write(tmp_path, "lakefile.toml", "name = \"demo\"\n")
    _write(
        tmp_path,
        "TauCeti/Norm.lean",
        "namespace TauCeti\n\n"
        "/-- A symmetric gauge on finite sequences. -/\n"
        "structure SymmetricNormingFunction where\n"
        "  gauge : Nat\n\n"
        "end TauCeti\n",
    )
    _write(
        tmp_path,
        "DavisKahan/Aux.lean",
        "namespace DavisKahan\n\n"
        "/-- The package-wide helper. -/\n"
        "theorem helper : True := by trivial\n\n"
        "end DavisKahan\n",
    )
    _write(
        tmp_path,
        "DavisKahan/Sources/DavisKahan1970/SectionTwo.lean",
        "namespace DavisKahan.Sources.DavisKahan1970\n\n"
        "/-- The Section 2 helper. -/\n"
        "theorem helper : True := by trivial\n\n"
        "/-- The sine theorem of Section 2. -/\n"
        "theorem sinTheta_main (N : SymmetricNormingFunction) : True := by\n"
        "  trivial\n\n"
        "end DavisKahan.Sources.DavisKahan1970\n",
    )
    return tmp_path, scan_lean_project(tmp_path)


def _resolve(tree, name: str, statements=None, module=None) -> Symbol | None:
    root, index = tree
    return resolve_symbol(
        name=name,
        source_index=index,
        statements={} if statements is None else statements,
        root=root,
        module=module,
    )


def test_a_qualified_name_resolves_verbatim(tree):
    symbol = _resolve(tree, "TauCeti.SymmetricNormingFunction")
    assert symbol is not None
    assert symbol.name == "TauCeti.SymmetricNormingFunction"
    assert symbol.query == "TauCeti.SymmetricNormingFunction"
    assert symbol.candidates == ()


def test_the_reading_module_disambiguates_a_short_name(tree):
    # Both `DavisKahan.helper` and `DavisKahan.Sources.DavisKahan1970.helper`
    # end in `.helper`, and the suffix step would take the shorter one.
    symbol = _resolve(tree, "helper", module=SECTION_TWO)
    assert symbol is not None
    assert symbol.name == "DavisKahan.Sources.DavisKahan1970.helper"
    assert symbol.candidates == ()
    assert "Section 2 helper" in symbol.docstring


def test_a_unique_suffix_resolves_without_a_module(tree):
    symbol = _resolve(tree, "SymmetricNormingFunction")
    assert symbol is not None
    assert symbol.name == "TauCeti.SymmetricNormingFunction"
    assert symbol.kind == "structure"
    assert symbol.module == "TauCeti.Norm"
    assert symbol.candidates == ()


def test_an_ambiguous_suffix_takes_the_shortest_and_reports_the_rest(tree):
    symbol = _resolve(tree, "helper")
    assert symbol is not None
    assert symbol.name == "DavisKahan.helper"
    assert symbol.candidates == ("DavisKahan.Sources.DavisKahan1970.helper",)


def test_the_candidate_list_is_capped(tree):
    root, index = tree
    statements = {f"Ns{n}.helper": _Record() for n in range(20)}
    symbol = resolve_symbol(
        name="helper", source_index=index, statements=statements, root=root, limit=3
    )
    assert symbol is not None
    assert len(symbol.candidates) == 3


@pytest.mark.parametrize("query", ["P.gap", "nowhere_declared", "helper.gap", ""])
def test_an_unresolvable_query_is_none(tree, query):
    # A field access on a local binder has no global name, and saying so is the
    # right answer rather than a failure.
    assert _resolve(tree, query) is None


def test_an_empty_statement_index_still_answers_from_source(tree):
    root, _ = tree
    symbol = _resolve(tree, "SymmetricNormingFunction")
    assert symbol is not None
    assert symbol.path == "TauCeti/Norm.lean"
    assert symbol.line == (root / "TauCeti/Norm.lean").read_text().splitlines().index(
        "structure SymmetricNormingFunction where"
    ) + 1
    assert symbol.docstring == "A symmetric gauge on finite sequences."
    assert symbol.signature.startswith("structure SymmetricNormingFunction")
    assert "A symmetric gauge" in symbol.source


def test_an_elaborated_record_supplies_the_signature(tree):
    statements = {
        "TauCeti.SymmetricNormingFunction": _Record(
            kind="structure",
            module="TauCeti.Norm",
            docstring="Elaborated prose.",
            signature="SymmetricNormingFunction : Type",
        )
    }
    symbol = _resolve(tree, "SymmetricNormingFunction", statements=statements)
    assert symbol is not None
    assert symbol.signature == "SymmetricNormingFunction : Type"
    assert symbol.docstring == "Elaborated prose."
    # The written source is still carried, so the card can show both.
    assert "structure SymmetricNormingFunction" in symbol.source


def test_a_missing_record_carries_no_information(tree):
    statements = {"TauCeti.SymmetricNormingFunction": _Record(missing=True, docstring="stale")}
    symbol = _resolve(tree, "SymmetricNormingFunction", statements=statements)
    assert symbol is not None
    assert symbol.docstring == "A symmetric gauge on finite sequences."


def test_a_name_known_only_to_the_statement_index_resolves(tree):
    statements = {"Mathlib.Submodule.HasOrthogonalProjection": _Record(kind="class")}
    symbol = _resolve(tree, "Submodule.HasOrthogonalProjection", statements=statements)
    assert symbol is not None
    assert symbol.name == "Mathlib.Submodule.HasOrthogonalProjection"
    assert symbol.kind == "class"
    assert symbol.path == "" and symbol.line == 0 and symbol.source == ""


def test_a_long_docstring_is_clipped(tree):
    statements = {"TauCeti.SymmetricNormingFunction": _Record(docstring="x" * 5000)}
    symbol = _resolve(tree, "SymmetricNormingFunction", statements=statements)
    assert symbol is not None
    assert len(symbol.docstring) < DOCSTRING_LIMIT + 8


def test_as_json_is_plain_data(tree):
    symbol = _resolve(tree, "helper")
    assert symbol is not None
    data = symbol.as_json()
    assert data["name"] == "DavisKahan.helper"
    assert data["candidates"] == ["DavisKahan.Sources.DavisKahan1970.helper"]
    assert set(data) == {
        "name", "query", "kind", "module", "docstring", "signature",
        "path", "line", "source", "candidates",
    }


STATEMENT = """/-- The `HiddenInDocstring` name must not leak. -/
theorem sinTheta_bound (N : SymmetricNormingFunction) (A₀ : Operator) :
    N.gauge S₀ ≤ Submodule.HasOrthogonalProjection.bound := by
  simp -- HiddenInComment
"""


def test_statement_identifiers_ignore_prose_and_grammar():
    names = statement_identifiers(STATEMENT)
    assert "HiddenInDocstring" not in names
    assert "HiddenInComment" not in names
    for word in ("theorem", "by", "simp"):
        assert word not in names
    # Ambient binders are single characters; they are never declarations.
    assert "N" not in names


def test_statement_identifiers_keep_dotted_and_subscripted_names():
    names = statement_identifiers(STATEMENT)
    assert "Submodule.HasOrthogonalProjection.bound" in names
    assert "A₀" in names
    assert "S₀" in names


def test_statement_identifiers_are_distinct_and_in_first_appearance_order():
    names = statement_identifiers(STATEMENT)
    assert len(names) == len(set(names))
    assert names.index("sinTheta_bound") < names.index("SymmetricNormingFunction")
    assert names.index("SymmetricNormingFunction") < names.index("Operator")


def test_statement_identifiers_ignore_the_elided_proof_marker(tree):
    symbol = _resolve(tree, "sinTheta_main")
    assert symbol is not None
    names = statement_identifiers(symbol.source)
    assert "proof" not in names and "omitted" not in names
    assert "SymmetricNormingFunction" in names

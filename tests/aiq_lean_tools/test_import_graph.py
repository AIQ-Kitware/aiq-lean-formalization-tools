from pathlib import Path

from aiq_lean_tools.import_graph import SourceImportGraph, imports_of


def test_import_graph_nested_srcdir_resolution(tmp_path: Path):
    (tmp_path / "lakefile.toml").write_text('name = "x"\n')
    (tmp_path / "Base.lean").write_text("theorem base : True := by trivial\n")
    nested = tmp_path / "Pkg" / "Pkg" / "Core"
    nested.mkdir(parents=True)
    (nested / "B.lean").write_text("public import Base\n")
    (nested / "A.lean").write_text("import Pkg.Core.B\nimport Mathlib\n")

    graph = SourceImportGraph.discover(tmp_path)
    resolved = graph.resolve("Pkg.Core.A")
    assert resolved.path == nested / "A.lean"
    closure = graph.closure("Pkg.Core.A")
    assert closure["local_module_count"] == 3
    assert {row["imported_as"] for row in closure["modules"]} == {"Pkg.Core.A", "Pkg.Core.B", "Base"}
    assert any(row["module"] == "Mathlib" for row in closure["unresolved_imports"])
    assert imports_of(nested / "B.lean") == ("Base",)


def test_import_closure_overlap_and_html(tmp_path: Path):
    (tmp_path / "lakefile.toml").write_text('name = "x"\n')
    (tmp_path / "Shared.lean").write_text("def shared := 1\n")
    (tmp_path / "A.lean").write_text("import Shared\n")
    (tmp_path / "B.lean").write_text("import Shared\n")
    graph = SourceImportGraph.discover(tmp_path)
    data = graph.compare_closures([("A lane", "A"), ("B lane", "B")])
    assert data["overlaps"][0]["count"] == 1
    text = graph.render_closure_html([("A lane", "A"), ("B lane", "B")])
    assert "source-import upper bound" in text
    assert "A lane" in text

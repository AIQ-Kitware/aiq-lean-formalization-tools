from __future__ import annotations

from pathlib import Path

from aiq_lean_tools.lean_source import scan_lean_project, strip_comments


def _write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_strip_nested_comments_preserves_newlines():
    text = 'theorem a : True := by\n/- outer\n/- inner -/\n-/\n  trivial\n'
    clean = strip_comments(text)
    assert clean.count("\n") == text.count("\n")
    assert "inner" not in clean
    assert "theorem a" in clean


def test_source_inventory_and_private_shadow(tmp_path: Path):
    _write(tmp_path, "Lib/Base.lean", "namespace Lib\n/-- public -/\ntheorem helper : True := by trivial\nend Lib\n")
    _write(tmp_path, "Lib/Use.lean", "import Lib.Base\nnamespace Lib\nnoncomputable section\nprivate theorem helper : True := by trivial\ntheorem main : True := by\n  sorry\nend\nend Lib\n")
    index = scan_lean_project(tmp_path)
    assert "Lib.Use" in index.modules
    assert "Lib.Use" in index.admitted_modules
    assert any(row.name == "Lib.main" for row in index.declarations)
    rows = index.private_shadows_imported_public()
    assert len(rows) == 1
    assert rows[0]["name"] == "helper"
    assert rows[0]["providers"] == ["Lib.Base"]


def test_declaration_source_text_includes_relevant_ambient_variables(tmp_path: Path):
    _write(
        tmp_path,
        "Paper/Main.lean",
        """namespace Paper\nvariable {E : Type} [TopologicalSpace E]\nvariable {unused : Type}\n\ntheorem main (x : E)\n    : True := by\n  trivial\nend Paper\n""",
    )
    from aiq_lean_tools.lean_source import declaration_source_texts

    index = scan_lean_project(tmp_path)
    rows = declaration_source_texts(index, "Paper.main")
    assert len(rows) == 1
    rendered = rows[0].render()
    assert "variable {E : Type}" in rendered
    assert "unused" not in rendered
    assert "theorem main (x : E)" in rendered
    assert ": True" in rendered
    assert ":= by" not in rendered

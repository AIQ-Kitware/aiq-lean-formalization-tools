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


def test_module_system_import_modifiers_are_parsed(tmp_path: Path):
    _write(tmp_path, "Lib/Base.lean", "theorem base : True := by trivial\n")
    _write(tmp_path, "Lib/Meta.lean", "theorem meta_thm : True := by trivial\n")
    _write(tmp_path, "Lib/Hidden.lean", "theorem hidden : True := by trivial\n")
    _write(
        tmp_path,
        "Lib/Use.lean",
        "public import Lib.Base\nprivate import Lib.Hidden\nmeta import Lib.Meta\n"
        "theorem use : True := by trivial\n",
    )
    index = scan_lean_project(tmp_path)
    assert index.imports["Lib.Use"] == {"Lib.Base", "Lib.Hidden", "Lib.Meta"}


def test_anonymous_instances_are_inventoried_but_not_name_keyed(tmp_path: Path):
    _write(
        tmp_path,
        "Lib/Inst.lean",
        "namespace Lib\n"
        "class Foo (α : Type) where\n"
        "  bar : α\n"
        "instance : Foo Nat := ⟨0⟩\n"
        "instance (priority := 100) : Foo Bool := ⟨true⟩\n"
        "/-- documented -/\n"
        "instance : Foo Unit := ⟨()⟩\n"
        "instance named : Foo Int := ⟨0⟩\n"
        "end Lib\n",
    )
    index = scan_lean_project(tmp_path)
    anonymous = [row for row in index.declarations if row.anonymous]
    assert len(anonymous) == 3
    assert sum(not row.documented for row in anonymous) == 2
    assert [row.name for row in index.named_declarations] == ["Lib.Foo", "Lib.named"]
    assert "<anonymous>" not in index.by_name


def test_source_scope_from_formalization_manifest(tmp_path: Path):
    _write(tmp_path, "Lib/Base.lean", "theorem base : True := by trivial\n")
    _write(tmp_path, "reference/Other/Base.lean", "theorem base : True := by trivial\n")
    (tmp_path / "formalization.yaml").write_text(
        "version: \"1\"\nsource_scope:\n  roots: [\"Lib\"]\n  exclude_dirs: [\".lake\", \"reference\"]\n"
    )
    index = scan_lean_project(tmp_path)
    assert set(index.modules) == {"Lib.Base"}
    assert index.duplicate_public_names() == {}


def test_explicit_exclude_dirs_still_override_the_manifest(tmp_path: Path):
    _write(tmp_path, "Lib/Base.lean", "theorem base : True := by trivial\n")
    _write(tmp_path, "reference/Other/Base.lean", "theorem base : True := by trivial\n")
    (tmp_path / "formalization.yaml").write_text("version: \"1\"\nsource_scope:\n  roots: [\"Lib\"]\n")
    index = scan_lean_project(tmp_path, exclude_dirs=(".lake",))
    assert set(index.modules) == {"Lib.Base", "reference.Other.Base"}


def test_src_dir_libraries_keep_their_lake_module_names(tmp_path: Path):
    _write(tmp_path, "Lib/Base.lean", "theorem base : True := by trivial\n")
    _write(tmp_path, "Paper/Paper/Core.lean", "theorem core : True := by trivial\n")
    _write(tmp_path, "Paper/Paper/Main.lean", "import Paper.Core\ntheorem main : True := by trivial\n")
    (tmp_path / "formalization.yaml").write_text(
        "version: \"1\"\n"
        "source_scope:\n"
        "  roots:\n"
        "    - \"Lib\"\n"
        "    - path: \"Paper/Paper\"\n"
        "      module_root: \"Paper\"\n"
    )
    index = scan_lean_project(tmp_path)
    assert set(index.modules) == {"Lib.Base", "Paper.Core", "Paper.Main"}
    # The import now resolves against the index, which is the whole point.
    assert index.import_closure(["Paper.Main"]) == {"Paper.Main", "Paper.Core"}


def test_nested_root_wins_over_the_tree_containing_it(tmp_path: Path):
    _write(tmp_path, "Paper/Paper/Core.lean", "theorem core : True := by trivial\n")
    (tmp_path / "formalization.yaml").write_text(
        "version: \"1\"\n"
        "source_scope:\n"
        "  roots:\n"
        "    - \"Paper\"\n"
        "    - path: \"Paper/Paper\"\n"
        "      module_root: \"Paper\"\n"
    )
    index = scan_lean_project(tmp_path)
    assert set(index.modules) == {"Paper.Core"}


def test_end_and_section_on_consecutive_lines_do_not_merge(tmp_path: Path):
    # `end` on one line and `section` on the next must not parse as a single
    # `end section`: that pops the wrong stack entry and silently mis-qualifies
    # every declaration in the rest of the file.
    _write(
        tmp_path,
        "Lib/A.lean",
        "namespace N\n"
        "end\n"
        "section\n"
        "theorem outside : True := by trivial\n"
        "end\n",
    )
    index = scan_lean_project(tmp_path)
    assert [row.name for row in index.declarations] == ["outside"]


def test_a_bare_end_closing_a_section_does_not_close_the_namespace(tmp_path: Path):
    _write(
        tmp_path,
        "Lib/A.lean",
        "namespace Outer\n"
        "section\n"
        "theorem inside : True := by trivial\n"
        "end\n"
        "theorem after : True := by trivial\n"
        "end Outer\n"
        "theorem outside : True := by trivial\n",
    )
    index = scan_lean_project(tmp_path)
    assert sorted(row.name for row in index.declarations) == [
        "Outer.after", "Outer.inside", "outside",
    ]


def test_structure_keywords_need_a_whole_line(tmp_path: Path):
    _write(
        tmp_path,
        "Lib/A.lean",
        "namespace N\n"
        "-- `end` and `section` mentioned in prose, and an attributed section:\n"
        "@[expose] public section\n"
        "noncomputable section Named\n"
        "theorem x : True := by trivial\n"
        "end Named\n"
        "end\n"
        "end N\n",
    )
    index = scan_lean_project(tmp_path)
    assert [row.name for row in index.declarations] == ["N.x"]

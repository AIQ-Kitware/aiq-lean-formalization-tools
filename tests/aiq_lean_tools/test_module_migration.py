from pathlib import Path

from aiq_lean_tools.module_migration import convert_module_text, migrate_module_file


def test_convert_module_text():
    source = '''/-\nCopyright X\n-/\n\nimport Mathlib\nimport MyLib.Base\n\n/-!\n# Demo\n-/\n\ntheorem x : True := by trivial\n'''
    new, changed = convert_module_text(source)
    assert changed
    assert new.startswith("/-\nCopyright X\n-/\nmodule\n")
    assert "public import Mathlib" in new
    assert "public import MyLib.Base" in new
    assert "-/\n\npublic section\n\ntheorem" in new
    again, changed_again = convert_module_text(new)
    assert changed_again is False
    assert again == new


def test_migrate_file_write(tmp_path: Path):
    p = tmp_path / "Foo.lean"
    p.write_text("import Mathlib\n\ntheorem x : True := by trivial\n")
    result = migrate_module_file(p, write=True)
    assert result.changed
    assert p.read_text().startswith("module\n\npublic import Mathlib")

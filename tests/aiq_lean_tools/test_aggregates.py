from __future__ import annotations

from pathlib import Path

from aiq_lean_tools.aggregates import generate_aggregates


def _write(path: Path, text: str = "theorem x : True := by trivial\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_recursive_aggregates_and_foreign_reexport(tmp_path: Path):
    _write(tmp_path / "Lib/A.lean")
    _write(tmp_path / "Lib/Sub/B.lean")
    _write(tmp_path / "Other/Moved.lean")
    _write(tmp_path / "Lib/All.lean", "import Other.Moved\n")

    rows = generate_aggregates(tmp_path, "Lib", own_library="Lib")
    assert any(row.changed for row in rows)
    root_text = (tmp_path / "Lib/All.lean").read_text()
    assert "import Lib.Sub.All" in root_text
    assert "import Lib.A" in root_text
    assert "import Other.Moved" in root_text

    checked = generate_aggregates(tmp_path, "Lib", own_library="Lib", check=True)
    assert not any(row.changed for row in checked)


def test_skipped_subtree_root_module_is_not_reimported(tmp_path):
    from aiq_lean_tools.aggregates import generate_aggregates

    lib = tmp_path / "Lib"
    (lib / "Core").mkdir(parents=True)
    (lib / "Core/Basic.lean").write_text("theorem a : True := by trivial\n")
    (lib / "Staging").mkdir()
    (lib / "Staging/Draft.lean").write_text("theorem b : True := by trivial\n")
    # The sibling root module of the skipped subtree.
    (lib / "Staging.lean").write_text("import Lib.Staging.Draft\n")

    generate_aggregates(tmp_path, lib, own_library="Lib", skip_dirs={"Staging"})
    text = (lib / "All.lean").read_text()
    assert "import Lib.Core.All" in text
    assert "Lib.Staging" not in text


def test_a_hand_written_directory_note_survives_regeneration(tmp_path):
    from aiq_lean_tools.aggregates import generate_aggregates

    lib = tmp_path / "Lib"
    lib.mkdir()
    (lib / "A.lean").write_text("theorem a : True := by trivial\n")
    (lib / "All.lean").write_text(
        "import Lib.A\n\n/-! # `Lib`\n\nWhy this directory exists, written by a human. -/\n"
    )
    generate_aggregates(tmp_path, lib, own_library="Lib")
    text = (lib / "All.lean").read_text()
    assert "written by a human" in text

    # A trailer that is not this directory's heading is replaced, so a stale one
    # left over from a move does not persist.
    (lib / "All.lean").write_text("import Lib.A\n\n/-! # `Other` -/\n")
    generate_aggregates(tmp_path, lib, own_library="Lib")
    assert (lib / "All.lean").read_text().endswith("/-! # `Lib` -/\n")


def test_a_module_that_depends_on_its_own_aggregate_is_promoted(tmp_path):
    from aiq_lean_tools.aggregates import generate_aggregates

    lib = tmp_path / "Lib"
    (lib / "Sub").mkdir(parents=True)
    (lib / "Sub/Leaf.lean").write_text("theorem leaf : True := by trivial\n")
    # `Wide` sits in `Sub/` but consumes the whole subtree through a parent
    # module, so listing it in `Sub/All.lean` is a Lake build cycle.
    (lib / "Wide.lean").write_text("import Lib.Sub.All\ntheorem wide : True := by trivial\n")
    (lib / "Sub/Consumer.lean").write_text("import Lib.Wide\ntheorem c : True := by trivial\n")

    generate_aggregates(tmp_path, lib, own_library="Lib")
    sub = (lib / "Sub/All.lean").read_text()
    top = (lib / "All.lean").read_text()
    assert "import Lib.Sub.Leaf" in sub
    assert "Lib.Sub.Consumer" not in sub
    assert "import Lib.Sub.Consumer" in top


def test_ignoring_cycles_restores_the_naive_placement(tmp_path):
    from aiq_lean_tools.aggregates import generate_aggregates

    lib = tmp_path / "Lib"
    (lib / "Sub").mkdir(parents=True)
    (lib / "Sub/Leaf.lean").write_text("theorem leaf : True := by trivial\n")
    (lib / "Wide.lean").write_text("import Lib.Sub.All\ntheorem wide : True := by trivial\n")
    (lib / "Sub/Consumer.lean").write_text("import Lib.Wide\ntheorem c : True := by trivial\n")

    generate_aggregates(tmp_path, lib, own_library="Lib", respect_cycles=False)
    assert "import Lib.Sub.Consumer" in (lib / "Sub/All.lean").read_text()

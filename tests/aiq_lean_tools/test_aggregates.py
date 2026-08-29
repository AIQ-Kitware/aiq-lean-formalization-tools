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

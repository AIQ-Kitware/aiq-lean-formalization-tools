from __future__ import annotations

import json
from pathlib import Path

from aiq_lean_tools.cli import main


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _collision_tree(root: Path) -> None:
    _write(root, "Lib/A.lean", "namespace Lib\ntheorem clash : True := by trivial\nend Lib\n")
    _write(root, "Lib/B.lean", "namespace Lib\ntheorem clash : True := by trivial\nend Lib\n")


def test_duplicates_check_respects_and_ratchets_a_baseline(tmp_path, capsys):
    _collision_tree(tmp_path)
    baseline = tmp_path / "accepted.json"

    assert main(["source", "duplicates", "--root", str(tmp_path), "--check"]) == 1

    assert main([
        "source", "duplicates", "--root", str(tmp_path),
        "--write-baseline", str(baseline),
    ]) == 0
    assert json.loads(baseline.read_text())["accepted"] == {"Lib.clash": ""}

    assert main([
        "source", "duplicates", "--root", str(tmp_path),
        "--baseline", str(baseline), "--check",
    ]) == 0

    _write(tmp_path, "Lib/C.lean", "namespace Lib\ntheorem other : True := by trivial\nend Lib\n")
    _write(tmp_path, "Lib/D.lean", "namespace Lib\ntheorem other : True := by trivial\nend Lib\n")
    assert main([
        "source", "duplicates", "--root", str(tmp_path),
        "--baseline", str(baseline), "--check",
    ]) == 1
    assert "NEW" in capsys.readouterr().out


def test_stale_baseline_entry_fails_the_check(tmp_path, capsys):
    _collision_tree(tmp_path)
    baseline = tmp_path / "accepted.json"
    baseline.write_text(json.dumps({"accepted": {"Lib.clash": "reviewed", "Lib.retired": "gone"}}))
    assert main([
        "source", "duplicates", "--root", str(tmp_path),
        "--baseline", str(baseline), "--check",
    ]) == 1
    assert "STALE" in capsys.readouterr().out


def test_baseline_only_matters_under_check(tmp_path):
    _collision_tree(tmp_path)
    assert main(["source", "duplicates", "--root", str(tmp_path)]) == 0


def test_docstring_baseline_keys_are_file_and_name(tmp_path):
    _write(tmp_path, "Lib/A.lean", "namespace Lib\ntheorem bare : True := by trivial\nend Lib\n")
    baseline = tmp_path / "docstrings.json"
    assert main(["source", "docstrings", "--root", str(tmp_path), "--check"]) == 1
    assert main([
        "source", "docstrings", "--root", str(tmp_path), "--write-baseline", str(baseline),
    ]) == 0
    assert json.loads(baseline.read_text())["accepted"] == {"Lib/A.lean:Lib.bare": ""}
    assert main([
        "source", "docstrings", "--root", str(tmp_path), "--baseline", str(baseline), "--check",
    ]) == 0


def test_private_shadow_baseline(tmp_path):
    _write(tmp_path, "Lib/Base.lean", "namespace Lib\n/-- doc -/\ntheorem helper : True := by trivial\nend Lib\n")
    _write(
        tmp_path,
        "Lib/Use.lean",
        "import Lib.Base\nnamespace Lib\n/-- doc -/\nprivate theorem helper : True := by trivial\nend Lib\n",
    )
    baseline = tmp_path / "shadows.json"
    assert main(["source", "private-shadows", "--root", str(tmp_path), "--check"]) == 1
    assert main([
        "source", "private-shadows", "--root", str(tmp_path), "--write-baseline", str(baseline),
    ]) == 0
    assert json.loads(baseline.read_text())["accepted"] == {"Lib.Use:helper": ""}
    assert main([
        "source", "private-shadows", "--root", str(tmp_path), "--baseline", str(baseline), "--check",
    ]) == 0

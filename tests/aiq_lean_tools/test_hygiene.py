from __future__ import annotations

from pathlib import Path

from aiq_lean_tools.hygiene import (
    conflict_markers,
    orphan_build_modules,
    remove_orphan_build_modules,
)


def test_conflict_markers_exact_shape(tmp_path: Path):
    (tmp_path / "bad.txt").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> branch\n")
    (tmp_path / "decor.txt").write_text("====================\n")
    rows = conflict_markers(tmp_path, tracked_only=False)
    assert [row.line for row in rows] == [1, 3, 5]


def test_orphan_build_artifacts(tmp_path: Path):
    artifact = tmp_path / ".lake/build/lib/lean/Lib/Ghost.olean"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("built")
    trace = tmp_path / ".lake/build/ir/Lib/Ghost.trace"
    trace.parent.mkdir(parents=True)
    trace.write_text("trace")
    modules = orphan_build_modules(tmp_path, libraries=["Lib"])
    assert modules == [Path("Lib/Ghost")]
    removed = remove_orphan_build_modules(tmp_path, modules)
    assert set(removed) == {artifact, trace}


def test_orphan_detection_follows_src_dir_libraries(tmp_path):
    from aiq_lean_tools.hygiene import orphan_build_modules

    (tmp_path / "formalization.yaml").write_text(
        "version: \"1\"\n"
        "source_scope:\n"
        "  roots:\n"
        "    - path: \"Paper\"\n"
        "      module_root: \"\"\n"
    )
    src = tmp_path / "Paper/Paper/Core.lean"
    src.parent.mkdir(parents=True)
    src.write_text("theorem core : True := by trivial\n")
    (tmp_path / "Paper/Paper.lean").write_text("import Paper.Core\n")

    build = tmp_path / ".lake/build/lib/lean"
    for rel in ("Paper.olean", "Paper/Core.olean", "Paper/Gone.olean", "Mathlib/Data.olean"):
        target = build / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")

    orphans = [".".join(module.parts) for module in orphan_build_modules(tmp_path)]
    # `Paper.olean` and `Paper/Core.olean` both have sources through the srcDir
    # mapping; `Mathlib/Data.olean` is a dependency's and not ours to judge.
    assert orphans == ["Paper.Gone"]

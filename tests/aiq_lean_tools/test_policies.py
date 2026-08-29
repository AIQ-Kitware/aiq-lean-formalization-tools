from __future__ import annotations

from pathlib import Path

from aiq_lean_tools.import_policy import ImportPolicy, check_import_policy
from aiq_lean_tools.lean_source import scan_lean_project
from aiq_lean_tools.namespace_policy import check_namespace_policy, declared_namespaces


def _write(root: Path, rel: str, text: str):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_import_policy_direct_and_transitive(tmp_path: Path):
    _write(tmp_path, "Foundation/A.lean", "import Mathlib.Data.Nat\nimport Paper.Main\n")
    _write(tmp_path, "Paper/Main.lean", "import Paper.Experimental.X\n")
    _write(tmp_path, "Paper/Experimental/X.lean", "")
    index = scan_lean_project(tmp_path)
    policy = ImportPolicy.from_data({
        "groups": {
            "mathlib": ["Mathlib", "Mathlib.*"],
            "foundation": ["Foundation", "Foundation.*"],
            "paper": ["Paper", "Paper.*"],
            "experimental": ["Paper.Experimental", "Paper.Experimental.*"],
        },
        "rules": [
            {"id": "foundation", "from": ["foundation"], "allow": ["mathlib", "foundation"]},
            {"id": "production", "from": ["paper"], "deny": ["experimental"], "transitive": True},
        ],
    })
    rows = check_import_policy(index, policy)
    assert any(row.rule == "foundation" and row.imported == "Paper.Main" for row in rows)
    assert any(row.rule == "production" and row.imported == "Paper.Experimental.X" for row in rows)


def test_namespace_policy(tmp_path: Path):
    _write(tmp_path, "Foundation/A.lean", "namespace Foundation\nnamespace Core\ntheorem a : True := by trivial\nend Core\nend Foundation\nnamespace Bad\ntheorem b : True := by trivial\nend Bad\n")
    assert declared_namespaces((tmp_path / "Foundation/A.lean").read_text()) == ["Foundation", "Foundation.Core", "Bad"]
    rows = check_namespace_policy(tmp_path, {"rules": [{
        "id": "ns",
        "modules": ["Foundation", "Foundation.*"],
        "allow": ["Foundation", "Foundation.*"],
    }]})
    assert [row.namespace for row in rows] == ["Bad"]

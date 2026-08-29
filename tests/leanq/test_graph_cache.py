import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from leanq.index import ensure_scoped_index, scoped_index_cache_state
from leanq.project import LeanProject


class GraphCacheTests(unittest.TestCase):
    def make_project(self, root: Path) -> LeanProject:
        (root / "lakefile.toml").write_text(
            '''
name = "demo"
defaultTargets = ["App"]
[[lean_lib]]
name = "App"
''',
            encoding="utf-8",
        )
        (root / "lean-toolchain").write_text("leanprover/lean4:v4.34.0\n", encoding="utf-8")
        (root / "App.lean").write_text("import App.Core\n", encoding="utf-8")
        (root / "App").mkdir()
        (root / "App" / "Core.lean").write_text(
            "namespace App\ntheorem core : True := by trivial\nend App\n",
            encoding="utf-8",
        )
        return LeanProject(root)

    def test_graph_cache_reuses_until_source_changes(self):
        with tempfile.TemporaryDirectory() as d:
            project = self.make_project(Path(d))
            roots = ["App"]

            def fake_build(_project, library, *, out, modules, **kwargs):
                row = {
                    "name": "App.core", "module": "App.Core", "kind": "theorem",
                    "library": library, "internal": False, "isProp": None,
                    "propValued": None, "sorried": None, "line": None,
                    "axioms": None, "deps": [],
                }
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(row) + "\n", encoding="utf-8")
                return out

            with patch("leanq.index.build_index", side_effect=fake_build) as build:
                ensure_scoped_index(project, "App", roots, detail="graph", verbose=False)
                self.assertEqual(build.call_count, 1)
                self.assertTrue(scoped_index_cache_state(project, "App", roots, detail="graph")["current"])
                ensure_scoped_index(project, "App", roots, detail="graph", verbose=False)
                self.assertEqual(build.call_count, 1)
                (project.root / "App" / "Core.lean").write_text(
                    "namespace App\ntheorem core : True := by decide\nend App\n",
                    encoding="utf-8",
                )
                self.assertFalse(scoped_index_cache_state(project, "App", roots, detail="graph")["current"])
                ensure_scoped_index(project, "App", roots, detail="graph", verbose=False)
                self.assertEqual(build.call_count, 2)


if __name__ == "__main__":
    unittest.main()

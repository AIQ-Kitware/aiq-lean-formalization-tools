import tempfile
import unittest
from pathlib import Path

from leanq.project import LeanProject


class LeanProjectGraphScopeTests(unittest.TestCase):
    def make_project(self, root: Path) -> LeanProject:
        (root / "lakefile.toml").write_text(
            '''
name = "demo"

[[lean_lib]]
name = "Foundation"
srcDir = "nested"

[[lean_lib]]
name = "App"

[[lean_lib]]
name = "Challenge"
''',
            encoding="utf-8",
        )
        (root / "nested" / "Foundation").mkdir(parents=True)
        (root / "nested" / "Foundation" / "Core.lean").write_text(
            "namespace Foundation\ntheorem base : True := by trivial\nend Foundation\n",
            encoding="utf-8",
        )
        (root / "App").mkdir()
        (root / "App" / "Target.lean").write_text(
            "import Foundation.Core\n\n"
            "namespace App.Query\n"
            "theorem final : True := by trivial\n"
            "end App.Query\n",
            encoding="utf-8",
        )
        (root / "Challenge").mkdir()
        (root / "Challenge" / "Old.lean").write_text(
            "namespace Challenge\ntheorem old : True := by trivial\nend Challenge\n",
            encoding="utf-8",
        )
        return LeanProject(root)

    def test_project_graph_roots_follow_default_targets_and_globs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "lakefile.toml").write_text(
                '''
name = "demo"
defaultTargets = ["Foundation", "App.All"]

[[lean_lib]]
name = "Foundation"
globs = ["Foundation.*"]

[[lean_lib]]
name = "App"

[[lean_lib]]
name = "Challenge"
globs = ["Challenge.*"]
''',
                encoding="utf-8",
            )
            (root / "Foundation").mkdir()
            (root / "Foundation.lean").write_text("", encoding="utf-8")
            (root / "Foundation" / "One.lean").write_text("", encoding="utf-8")
            (root / "Foundation" / "Two.lean").write_text("", encoding="utf-8")
            (root / "App").mkdir()
            (root / "App" / "All.lean").write_text("", encoding="utf-8")
            (root / "Challenge").mkdir()
            (root / "Challenge" / "Old.lean").write_text("", encoding="utf-8")
            project = LeanProject(root)
            roots = project.project_graph_roots()
            self.assertEqual(
                roots["Foundation"],
                ["Foundation", "Foundation.One", "Foundation.Two"],
            )
            self.assertEqual(roots["App"], ["App.All"])
            self.assertNotIn("Challenge", roots)
            all_roots = project.project_graph_roots(all_libraries=True)
            self.assertIn("Challenge", all_roots)
            self.assertEqual(all_roots["Challenge"], ["Challenge.Old"])

    def test_srcdir_aware_source_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            project = self.make_project(Path(d))
            source = project.source_of("Foundation.Core")
            self.assertEqual(
                source.relative_to(project.root),
                Path("nested/Foundation/Core.lean"),
            )
            self.assertEqual(project.module_of_source(source), "Foundation.Core")

    def test_target_locator_and_import_scope_ignore_unrelated_library(self):
        with tempfile.TemporaryDirectory() as d:
            project = self.make_project(Path(d))
            roots = project.declaration_modules(["App.Query.final"])
            self.assertEqual(roots, ["App.Target"])
            self.assertEqual(
                project.local_import_closure(roots),
                ["App.Target", "Foundation.Core"],
            )
            self.assertEqual(
                project.libraries_for_import_closure(roots),
                ["Foundation", "App"],
            )
            self.assertNotIn("Challenge", project.libraries_for_import_closure(roots))


if __name__ == "__main__":
    unittest.main()

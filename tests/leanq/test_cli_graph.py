import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, call, patch

from leanq.cli import cmd_graph
from leanq.index import Decl


def decl(name, *, deps=(), module, library):
    return Decl(
        name=name,
        module=module,
        kind="theorem",
        is_prop=None,
        prop_valued=None,
        sorried=None,
        line=None,
        axioms=None,
        deps=tuple(deps),
        library=library,
        internal=False,
    )


class GraphCliScopeTests(unittest.TestCase):
    def test_graph_indexes_only_target_import_scope(self):
        project = Mock()
        project.root = Path("/tmp/demo")
        project.declaration_modules.return_value = ["App.Target"]
        project.libraries_for_import_closure.return_value = ["Foundation", "App"]
        rows = {
            "Foundation": [
                decl("Foundation.base", module="Foundation.Core", library="Foundation")
            ],
            "App": [
                decl(
                    "App.final",
                    deps=("Foundation.base",),
                    module="App.Target",
                    library="App",
                )
            ],
        }
        args = Namespace(
            project=None,
            presentation=None,
            target=["App.final"],
            root_module=None,
            include_lib=None,
            lib=None,
            exclude_lib=None,
            refresh=False,
            json=False,
            transitive_reduction=False,
            html=None,
            include_unresolved=False,
            headline=None,
            title=None,
            subtitle=None,
            strict_presentation=False,
            out=None,
        )

        def scoped(_project, library, roots, **kwargs):
            self.assertIs(_project, project)
            self.assertEqual(roots, ["App.Target"])
            self.assertEqual(kwargs["detail"], "graph")
            return rows[library]

        with patch("leanq.cli.find_project", return_value=project), patch(
            "leanq.cli.ensure_scoped_index", side_effect=scoped
        ) as ensure, patch("sys.stdout", new_callable=io.StringIO) as stdout:
            status = cmd_graph(args)

        self.assertEqual(status, 0)
        self.assertEqual(ensure.call_count, 2)
        self.assertIn("libraries: Foundation, App", stdout.getvalue())
        project.libraries_for_import_closure.assert_called_once_with(["App.Target"])


    def test_duplicate_provenance_still_writes_json_and_html(self):
        project = Mock()
        project.root = Path("/tmp/demo")
        project.declaration_modules.return_value = ["App.Target"]
        project.libraries_for_import_closure.return_value = ["Foundation", "App"]
        rows = {
            "Foundation": [
                decl(
                    "Foundation.base",
                    module="Foundation.Internal.LongPath",
                    library="Foundation",
                ),
                decl(
                    "Foundation.base",
                    module="Foundation.Core",
                    library="Foundation",
                ),
            ],
            "App": [
                decl(
                    "App.final",
                    deps=("Foundation.base",),
                    module="App.Target",
                    library="App",
                )
            ],
        }

        def scoped(_project, library, roots, **kwargs):
            return rows[library]

        with tempfile.TemporaryDirectory() as dpath:
            out = Path(dpath) / "proof.json"
            html = Path(dpath) / "proof.html"
            args = Namespace(
                project=None,
                presentation=None,
                target=["App.final"],
                root_module=None,
                include_lib=None,
                lib=None,
                exclude_lib=None,
                refresh=False,
                json=False,
                transitive_reduction=True,
                html=str(html),
                include_unresolved=False,
                headline=None,
                title=None,
                subtitle=None,
                strict_presentation=False,
                out=str(out),
            )
            with patch("leanq.cli.find_project", return_value=project), patch(
                "leanq.cli.ensure_scoped_index", side_effect=scoped
            ), patch("sys.stdout", new_callable=io.StringIO):
                status = cmd_graph(args)

            self.assertEqual(status, 0)
            payload = json.loads(out.read_text())
            self.assertEqual(payload["targets"], ["App.final"])
            self.assertEqual(payload["nodeCount"], 2)
            self.assertEqual(payload["edgeCount"], 1)
            self.assertTrue(html.exists())
            self.assertIn("App.final", html.read_text())

    def test_stale_presentation_headline_warns_but_writes_outputs(self):
        from leanq.presentation import PresentationSpec

        project = Mock()
        project.root = Path("/tmp/demo")
        project.declaration_modules.return_value = ["App.Target"]
        project.libraries_for_import_closure.return_value = ["App"]
        rows = {
            "App": [decl("App.final", module="App.Target", library="App")]
        }

        def scoped(_project, library, roots, **kwargs):
            return rows[library]

        spec = PresentationSpec.from_json(
            {"targets": ["App.final"], "headlines": ["YWS.missing"]}
        )
        with tempfile.TemporaryDirectory() as dpath:
            out = Path(dpath) / "proof.json"
            html = Path(dpath) / "proof.html"
            args = Namespace(
                project=None,
                presentation="unused.json",
                target=[],
                root_module=None,
                include_lib=None,
                lib=None,
                exclude_lib=None,
                refresh=False,
                json=False,
                transitive_reduction=True,
                html=str(html),
                include_unresolved=False,
                headline=None,
                title=None,
                subtitle=None,
                strict_presentation=False,
                out=str(out),
            )
            with patch("leanq.cli.find_project", return_value=project), patch(
                "leanq.cli.load_presentation", return_value=spec
            ), patch(
                "leanq.cli.ensure_scoped_index", side_effect=scoped
            ), patch("sys.stdout", new_callable=io.StringIO), patch(
                "sys.stderr", new_callable=io.StringIO
            ) as stderr:
                status = cmd_graph(args)

            self.assertEqual(status, 0)
            self.assertTrue(out.exists())
            self.assertTrue(html.exists())
            payload = json.loads(out.read_text())
            self.assertEqual(payload["presentation"]["missingHeadlines"], ["YWS.missing"])
            self.assertIn("presentation skipped 1 headline", stderr.getvalue())
            self.assertIn("Presentation warning", html.read_text())


if __name__ == "__main__":
    unittest.main()

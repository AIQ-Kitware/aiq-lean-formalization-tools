import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

from leanq.cli import cmd_graph_headlines, cmd_graph_html, cmd_graph_index, cmd_graph_slice
from leanq.graph import environment_dependency_graph
from leanq.index import Decl


def decl(name, *, deps=(), module="App.Core", library="App"):
    return Decl(name, module, "theorem", None, None, None, None, None, tuple(deps), library, False)


class StagedGraphCliTests(unittest.TestCase):
    def test_graph_index_writes_broad_environment_and_reuses_cache(self):
        project = Mock()
        project.root = Path("/tmp/demo")
        project.declaration_modules.return_value = ["App.Target"]
        project.libraries_for_import_closure.return_value = ["Foundation", "App"]
        rows = {
            "Foundation": [decl("Foundation.used", library="Foundation"), decl("Foundation.unused", library="Foundation")],
            "App": [decl("App.target", deps=("Foundation.used",))],
        }
        with tempfile.TemporaryDirectory() as dpath:
            out = Path(dpath) / "semantic.json"
            cache = Path(dpath) / "cache.jsonl"
            cache.write_text("", encoding="utf-8")
            args = Namespace(
                project=None, target=["App.target"], root_module=None,
                include_lib=None, exclude_lib=None, lib=None, refresh=False,
                refresh_lib=None, out=str(out), json=False, whole_project=False,
                all_libraries=False,
            )
            cache_state = {"path": cache, "exists": True, "current": True, "fingerprint": "abc"}
            with patch("leanq.cli.find_project", return_value=project), patch(
                "leanq.cli.ensure_scoped_index", side_effect=lambda _p, lib, _roots, **kw: rows[lib]
            ) as ensure, patch("leanq.cli.scoped_index_cache_state", return_value=cache_state), patch(
                "sys.stdout", new_callable=io.StringIO
            ), patch("sys.stderr", new_callable=io.StringIO) as stderr:
                status = cmd_graph_index(args)
            self.assertEqual(status, 0)
            payload = json.loads(out.read_text())
            self.assertEqual(payload["payloadKind"], "semantic-index")
            self.assertEqual(payload["nodeCount"], 3)
            self.assertEqual(payload["targets"], [])
            self.assertEqual(payload["bootstrapTargets"], ["App.target"])
            self.assertEqual(ensure.call_count, 2)
            self.assertIn("using current graph cache", stderr.getvalue())

    def test_project_index_discovers_scope_without_target(self):
        project = Mock()
        project.root = Path("/tmp/demo")
        project.default_targets.return_value = ["Foundation", "App"]
        project.project_graph_roots.return_value = {
            "Foundation": ["Foundation"],
            "App": ["App"],
        }
        rows = {
            "Foundation": [decl("Foundation.base", library="Foundation")],
            "App": [decl("App.target", deps=("Foundation.base",))],
        }
        with tempfile.TemporaryDirectory() as dpath:
            out = Path(dpath) / "project.json"
            args = Namespace(
                project=None, target=[], root_module=None, include_lib=None,
                exclude_lib=None, lib=None, refresh=False, refresh_lib=None,
                out=str(out), json=False, whole_project=False, all_libraries=False,
            )
            def state(_project, library, roots, **kwargs):
                return {
                    "path": Path(dpath) / f"{library}.jsonl",
                    "exists": True, "current": True, "fingerprint": library,
                }
            with patch("leanq.cli.find_project", return_value=project), patch(
                "leanq.cli.scoped_index_cache_state", side_effect=state
            ), patch(
                "leanq.cli.ensure_scoped_index", side_effect=lambda _p, lib, _roots, **kw: rows[lib]
            ), patch("sys.stdout", new_callable=io.StringIO), patch(
                "sys.stderr", new_callable=io.StringIO
            ):
                self.assertEqual(cmd_graph_index(args), 0)
            payload = json.loads(out.read_text())
            self.assertEqual(payload["scope"], "project-default-build")
            self.assertEqual(payload["bootstrapTargets"], [])
            self.assertEqual(payload["importRootsByLibrary"]["App"], ["App"])
            self.assertEqual(payload["nodeCount"], 2)

    def test_graph_slice_is_file_only(self):
        rows = [
            decl("Foundation.base", library="Foundation"),
            decl("App.mid", deps=("Foundation.base",)),
            decl("App.target", deps=("App.mid",)),
            decl("Unused.extra"),
        ]
        semantic = environment_dependency_graph(rows).to_json()
        semantic.update({"payloadKind": "semantic-index", "scope": "project-default-build"})
        with tempfile.TemporaryDirectory() as dpath:
            root = Path(dpath)
            semantic_path = root / "semantic.json"
            out = root / "slice.json"
            semantic_path.write_text(json.dumps(semantic), encoding="utf-8")
            args = Namespace(
                semantic_index=str(semantic_path), target=["App.target"],
                transitive_reduction=True, out=str(out), json=False,
            )
            with patch("sys.stdout", new_callable=io.StringIO), patch(
                "sys.stderr", new_callable=io.StringIO
            ):
                self.assertEqual(cmd_graph_slice(args), 0)
            payload = json.loads(out.read_text())
            self.assertEqual(payload["payloadKind"], "graph-slice")
            self.assertEqual(payload["targets"], ["App.target"])
            self.assertEqual(payload["nodeCount"], 3)
            self.assertNotIn("Unused.extra", {row["name"] for row in payload["nodes"]})
            self.assertIn("reducedEdges", payload)

    def test_headline_and_html_stages_are_file_only(self):
        rows = [decl("YWS.core", library="YuWangSamworth2015"), decl("App.target", deps=("YWS.core",), module="DkpsQuench2026.Target", library="DkpsQuench2026")]
        semantic = environment_dependency_graph(rows).to_json()
        semantic.update({"payloadKind": "semantic-index", "bootstrapTargets": ["App.target"], "libraries": ["YuWangSamworth2015", "DkpsQuench2026"]})
        with tempfile.TemporaryDirectory() as dpath:
            root = Path(dpath)
            semantic_path = root / "semantic.json"
            census = root / "yu-wang-samworth-2015-full-source-census.json"
            analysis_path = root / "headlines.json"
            html_path = root / "headlines.html"
            semantic_path.write_text(json.dumps(semantic), encoding="utf-8")
            census.write_text(json.dumps({"items": [{"id":"Y","title":"YWS","importance":"headline","lean_declarations":["YWS.core"]}]}), encoding="utf-8")
            args = Namespace(semantic_index=str(semantic_path), target=None, census=[str(census)], importance=None, view="dependencies", include_supporting=False, out=str(analysis_path), json=False)
            with patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
                self.assertEqual(cmd_graph_headlines(args), 0)
            self.assertTrue(analysis_path.exists())
            args_html = Namespace(input=str(analysis_path), out=str(html_path), title=None)
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(cmd_graph_html(args_html), 0)
            self.assertTrue(html_path.exists())
            text = html_path.read_text()
            self.assertIn("Whole formalization dependency explorer", text)
            self.assertIn("YWS.core", text)

    def test_whole_project_html_embeds_complete_graph_with_census_landmarks(self):
        rows = [
            decl("Foundation.base", library="Foundation"),
            decl("YWS.helper", deps=("Foundation.base",), library="YuWangSamworth2015"),
            decl("YWS.head", deps=("YWS.helper",), library="YuWangSamworth2015"),
            decl("Unrelated.kept", library="Unrelated"),
        ]
        semantic = environment_dependency_graph(rows).to_json()
        semantic.update({"payloadKind": "semantic-index", "libraries": ["Foundation", "YuWangSamworth2015", "Unrelated"]})
        with tempfile.TemporaryDirectory() as dpath:
            root = Path(dpath)
            semantic_path, census, html_path = root / "semantic.json", root / "census.json", root / "project.html"
            semantic_path.write_text(json.dumps(semantic), encoding="utf-8")
            census.write_text(json.dumps({"items": [{"id": "Y", "title": "YWS headline", "importance": "headline", "lean_declarations": ["YWS.head"], "semantic_review": {"canonical_declarations": ["YWS.head"], "supporting_declarations": ["YWS.helper"]}}]}), encoding="utf-8")
            args = Namespace(input=str(semantic_path), census=[str(census)], importance=None, target=None, out=str(html_path), title=None)
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(cmd_graph_html(args), 0)
            text = html_path.read_text(encoding="utf-8")
        self.assertIn('"completeGraphEmbedded":true', text)
        self.assertIn("Unrelated.kept", text)
        self.assertIn('"headline":true', text)
        self.assertIn("Dependencies +1", text)
        self.assertIn("Ancestors of selection only", text)
        self.assertIn('"defaultClaimSelection"', text)
        self.assertIn("Nearest shared dependency", text)
        self.assertIn("Modules / source files", text)
        self.assertIn("Headline subset", text)
        self.assertIn("Paper default", text)
        self.assertIn("Direct dependencies (in)", text)


if __name__ == "__main__":
    unittest.main()

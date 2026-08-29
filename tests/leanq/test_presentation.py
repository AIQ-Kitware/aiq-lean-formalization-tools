import json
import tempfile
import unittest
from pathlib import Path

from leanq.graph import target_dependency_graph
from leanq.index import Decl
from leanq.presentation import (
    PresentationSpec,
    build_presentation,
    load_presentation,
)
from leanq.project import ProjectError


def decl(name, *, deps=(), module="Pkg.Main", library="Pkg", internal=False):
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
        internal=internal,
    )


class PresentationTests(unittest.TestCase):
    def graph(self):
        rows = [
            decl("YWS.align", library="YWS"),
            decl("private.bridge", deps=("YWS.align",), internal=True),
            decl("Ach.config", deps=("private.bridge",), library="Acharyya"),
            decl("Quench.end", deps=("Ach.config",), library="Quench"),
        ]
        return target_dependency_graph(rows, ["Quench.end"])

    def test_projection_resolves_labels_and_witnesses(self):
        spec = PresentationSpec.from_json(
            {
                "schemaVersion": 1,
                "title": "Quench proof",
                "headlines": [
                    {"name": "YWS.align", "label": "YWS alignment", "group": "YWS"},
                    {"name": "Ach.config", "label": "Acharyya configuration"},
                ],
            }
        )
        payload = build_presentation(self.graph(), spec)
        self.assertEqual(payload["title"], "Quench proof")
        self.assertEqual(
            [node["id"] for node in payload["nodes"]],
            ["YWS.align", "Ach.config", "Quench.end"],
        )
        self.assertEqual(payload["edges"][0]["source"], "Ach.config")
        yws_edge = next(e for e in payload["edges"] if e["source"] == "YWS.align")
        self.assertEqual(
            yws_edge["witness"],
            ["YWS.align", "private.bridge", "Ach.config"],
        )
        self.assertEqual(yws_edge["collapsedNodeCount"], 1)

    def test_stale_headline_is_reported_without_blocking_output(self):
        spec = PresentationSpec.from_json({"headlines": ["Not.A.Dependency"]})
        payload = build_presentation(self.graph(), spec)
        self.assertEqual(payload["missingHeadlineCount"], 1)
        self.assertEqual(payload["missingHeadlines"], ["Not.A.Dependency"])
        self.assertEqual([node["id"] for node in payload["nodes"]], ["Quench.end"])

    def test_strict_stale_headline_still_fails(self):
        spec = PresentationSpec.from_json({"headlines": ["Not.A.Dependency"]})
        with self.assertRaisesRegex(ProjectError, "not in the target dependency closure"):
            build_presentation(self.graph(), spec, strict=True)

    def test_load_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "view.json"
            path.write_text(
                json.dumps(
                    {
                        "targets": ["Quench.end"],
                        "headlines": [{"name": "Ach.config", "subtitle": "Finite bridge"}],
                    }
                ),
                encoding="utf-8",
            )
            spec = load_presentation(path)
        self.assertEqual(spec.targets, ("Quench.end",))
        self.assertEqual(spec.headlines[0].subtitle, "Finite bridge")


if __name__ == "__main__":
    unittest.main()

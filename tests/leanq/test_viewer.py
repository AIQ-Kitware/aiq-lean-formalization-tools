import tempfile
import unittest
from pathlib import Path

from leanq.viewer import render_graph_html, write_graph_html


class ViewerTests(unittest.TestCase):
    def payload(self):
        return {
            "schemaVersion": 1,
            "targets": ["Pkg.final"],
            "nodeCount": 2,
            "edgeCount": 1,
            "libraries": ["Pkg"],
            "nodes": [
                {"id": "Pkg.base", "name": "Pkg.base", "module": "Pkg.Core", "kind": "theorem", "library": "Pkg", "internal": False, "line": 4},
                {"id": "Pkg.final", "name": "Pkg.final", "module": "Pkg.Main", "kind": "theorem", "library": "Pkg", "internal": False, "line": 9, "target": True},
            ],
            "edges": [{"source": "Pkg.base", "target": "Pkg.final", "direct": True}],
            "reducedEdges": [{"source": "Pkg.base", "target": "Pkg.final"}],
            "presentation": {
                "schemaVersion": 1,
                "title": "Proof <chain>",
                "subtitle": "One exact edge",
                "nodes": [],
                "edges": [],
            },
        }

    def test_render_is_self_contained_and_escapes_script_data(self):
        payload = self.payload()
        payload["nodes"][0]["doc"] = "</script><script>alert(1)</script>"
        text = render_graph_html(payload)
        self.assertIn("<!doctype html>", text.lower())
        self.assertIn("Proof &lt;chain&gt;", text)
        self.assertNotIn("</script><script>alert(1)</script>", text)
        self.assertIn(r"\u003c/script\u003e", text)
        self.assertIn('id="graph"', text)
        self.assertIn("Export presentation JSON", text)

    def test_write_creates_parent(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nested" / "graph.html"
            result = write_graph_html(path, self.payload())
            self.assertEqual(result, path)
            self.assertTrue(path.read_text(encoding="utf-8").endswith("</html>\n"))

    def test_missing_presentation_headlines_are_embedded_for_visible_warning(self):
        payload = {
            "schemaVersion": 1,
            "targets": ["Quench.end"],
            "nodes": [],
            "edges": [],
            "presentation": {
                "title": "Proof",
                "nodes": [],
                "edges": [],
                "missingHeadlineCount": 1,
                "missingHeadlines": ["YWS.missing"],
            },
        }
        rendered = render_graph_html(payload)
        self.assertIn("YWS.missing", rendered)
        self.assertIn("presentation-warning", rendered)


if __name__ == "__main__":
    unittest.main()

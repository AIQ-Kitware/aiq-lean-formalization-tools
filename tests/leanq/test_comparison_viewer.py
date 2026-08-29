import json
import tempfile
import unittest
from pathlib import Path

from leanq.cli import _compact_comparison_payload
from leanq.headlines import load_census_claims
from leanq.viewer import render_comparison_html


class ComparisonViewerTests(unittest.TestCase):
    def payload(self):
        return {
            "payloadKind": "project-explorer",
            "libraries": ["Foundation", "App"],
            "nodes": [
                {"id": "Foundation.extra", "module": "Foundation.Extra", "kind": "theorem", "library": "Foundation", "dependencyDepth": 0, "internal": False},
                {"id": "Foundation.base", "module": "Foundation.Core", "kind": "theorem", "library": "Foundation", "dependencyDepth": 1, "internal": False},
                {"id": "App.head", "module": "App.Main", "kind": "theorem", "library": "App", "dependencyDepth": 2, "internal": False},
                {"id": "Unused.dead", "module": "Unused.Dead", "kind": "theorem", "library": "Unused", "dependencyDepth": 0, "internal": False},
            ],
            "edges": [
                {"source": "Foundation.extra", "target": "Foundation.base", "direct": True},
                {"source": "Foundation.base", "target": "App.head", "direct": True},
            ],
            "headlineAnalysis": {
                "claims": [
                    {"nodeId": "claim:A", "family": "Paper A", "id": "A", "title": "A headline", "sourceKind": "theorem", "canonicalDeclarations": ["Foundation.base"], "leafDeclarations": ["Foundation.base"]},
                    {"nodeId": "claim:B", "family": "Paper B", "id": "B", "title": "B headline", "sourceKind": "theorem", "canonicalDeclarations": ["App.head"], "leafDeclarations": ["App.head"]},
                ],
                "defaultClaimSelection": ["claim:A", "claim:B"],
            },
        }

    def test_compaction_keeps_all_loaded_claim_ancestry(self):
        payload = _compact_comparison_payload(self.payload())
        self.assertEqual(
            {row["id"] for row in payload["nodes"]},
            {"Foundation.extra", "Foundation.base", "App.head"},
        )
        self.assertEqual(payload["comparisonScope"]["kind"], "loaded-census-claim-union")

    def test_generic_census_family_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "whatever.json"
            path.write_text(json.dumps({
                "presentation": {"family": "Future Formalization"},
                "items": [{
                    "id": "T1", "title": "Headline", "importance": "headline",
                    "lean_declarations": ["Foo.bar"],
                }],
            }), encoding="utf-8")
            rows = load_census_claims([path])
        self.assertEqual(rows[0].family, "Future Formalization")

    def test_renderer_supports_zero_many_families_and_drag_layout(self):
        payload = _compact_comparison_payload(self.payload())
        payload["comparisonPublisher"] = {"initialFamilies": []}
        text = render_comparison_html(payload)
        self.assertIn("Clear families", text)
        self.assertIn("Arrange by drag", text)
        self.assertIn("Reset moved", text)
        self.assertIn("beginNodeDrag", text)
        self.assertIn("beginPackageDrag", text)
        self.assertIn("state.nodeManual", text)
        self.assertIn("state.packageManual", text)
        self.assertIn("if(P)draw()", text)
        self.assertIn("if(!P)return null", text)
        self.assertNotIn("Family A<select", text)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from leanq.graph import environment_dependency_graph
from leanq.headlines import (
    ConsumptionLandmark,
    analyze_headlines,
    load_census_claims,
    parse_consumption_landmark,
    prepare_project_explorer,
)
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


def write_census(path, rows, *, family=None):
    payload = {"items": rows}
    if family is not None:
        payload["family"] = family
    path.write_text(json.dumps(payload), encoding="utf-8")


class HeadlineAnalysisTests(unittest.TestCase):
    def test_claim_group_consumption_and_generic_landmarks(self):
        rows = [
            decl("Source.unused", module="Source.Core", library="SourceLib"),
            decl("Source.used", module="Source.Core", library="SourceLib"),
            decl(
                "Stage.core",
                deps=("Source.used",),
                module="Stage.Core",
                library="StageLib",
            ),
            decl(
                "Bridge.join",
                deps=("Stage.core",),
                module="Bridge.Config",
                library="BridgeLib",
            ),
            decl(
                "Downstream.high",
                deps=("Bridge.join",),
                module="Downstream.Geometry",
                library="DownstreamLib",
            ),
            decl(
                "Downstream.target",
                deps=("Downstream.high",),
                module="Downstream.Target",
                library="DownstreamLib",
            ),
            decl(
                "Downstream.other",
                module="Downstream.Other",
                library="DownstreamLib",
            ),
        ]
        semantic = environment_dependency_graph(rows).to_json()
        semantic["payloadKind"] = "semantic-index"
        semantic["libraries"] = ["SourceLib", "StageLib", "BridgeLib", "DownstreamLib"]

        with tempfile.TemporaryDirectory() as dpath:
            root = Path(dpath)
            source = root / "source-full-source-census.json"
            stage = root / "stage-full-source-census.json"
            downstream = root / "downstream-full-source-census.json"
            write_census(
                source,
                [
                    {"id": "S-H", "title": "Unused source", "importance": "headline", "lean_declarations": ["Source.unused"]},
                    {"id": "S-U", "title": "Used source", "importance": "headline", "lean_declarations": ["Source.used"], "semantic_review": {"canonical_declarations": ["Source.used"], "supporting_declarations": []}},
                ],
                family="Source paper",
            )
            write_census(
                stage,
                [{"id": "M-H", "title": "Middle result", "importance": "headline", "lean_declarations": ["Stage.core"], "semantic_review": {"canonical_declarations": ["Stage.core"], "supporting_declarations": []}}],
                family="Middle paper",
            )
            write_census(
                downstream,
                [
                    {"id": "D-H", "title": "Downstream headline", "importance": "headline", "lean_declarations": ["Downstream.high"]},
                    {"id": "D-U", "title": "Other downstream headline", "importance": "headline", "lean_declarations": ["Downstream.other"]},
                ],
                family="Downstream paper",
            )
            result = analyze_headlines(
                semantic,
                targets=["Downstream.target"],
                census_paths=[source, stage, downstream],
                view="consumption",
                landmarks=[
                    ConsumptionLandmark("middle", "Middle integration", "StageLib"),
                    ConsumptionLandmark("bridge", "Bridge integration", "BridgeLib"),
                    ConsumptionLandmark("downstream", "Downstream integration", "DownstreamLib"),
                ],
            )

        claims = {row["id"]: row for row in result["headlineAnalysis"]["claims"]}
        self.assertFalse(claims["S-H"]["consumed"])
        self.assertTrue(claims["S-U"]["consumed"])
        source_hits = {row["id"]: row for row in claims["S-U"]["landmarkHits"]}
        self.assertEqual(source_hits["middle"]["declaration"], "Stage.core")
        self.assertEqual(source_hits["bridge"]["declaration"], "Bridge.join")
        self.assertEqual(source_hits["downstream"]["declaration"], "Downstream.high")
        self.assertTrue(claims["M-H"]["consumed"])
        self.assertEqual(claims["M-H"]["representativeDeclaration"], "Stage.core")
        self.assertEqual(claims["M-H"]["nearestDownstreamHeadline"]["id"], "D-H")
        self.assertEqual(
            claims["M-H"]["witnessToTarget"],
            ["Stage.core", "Bridge.join", "Downstream.high", "Downstream.target"],
        )
        self.assertTrue(claims["D-H"]["consumed"])
        self.assertFalse(claims["D-U"]["consumed"])
        self.assertEqual(claims["M-H"]["consumptionClass"], "canonical")
        self.assertEqual(
            result["headlineAnalysis"]["familyCounts"]["Middle paper"],
            {"claims": 1, "consumed": 1, "canonical": 1, "supporting": 0},
        )
        self.assertEqual(
            [row["label"] for row in result["headlineAnalysis"]["stagePlan"]],
            [
                "Source headline claims",
                "Middle integration",
                "Bridge integration",
                "Downstream integration",
                "Downstream paper headlines",
                "Selected target",
            ],
        )
        edge_roles = {edge["role"] for edge in result["edges"]}
        self.assertIn("landmark:bridge", edge_roles)
        self.assertIn("downstream-headline", edge_roles)
        self.assertIn("claim-to-target", edge_roles)

    def test_landmark_cli_spec(self):
        row = parse_consumption_landmark("Configuration bridge=MyLibrary.Config")
        self.assertEqual(row.id, "configuration-bridge")
        self.assertEqual(row.label, "Configuration bridge")
        self.assertEqual(row.library, "MyLibrary.Config")

    def test_only_requested_importance_is_loaded(self):
        with tempfile.TemporaryDirectory() as dpath:
            path = Path(dpath) / "quench-2026-full-source-census.json"
            write_census(
                path,
                [
                    {"id": "H", "title": "Headline", "importance": "headline", "lean_declarations": []},
                    {"id": "M", "title": "Major", "importance": "major", "lean_declarations": []},
                ],
            )
            rows = load_census_claims([path], importances=["headline"])
        self.assertEqual([row.id for row in rows], ["H"])

    def test_real_dependency_union_coverage_frontier_and_distances(self):
        rows = [
            decl("Base.shared", module="Foundation.Core", library="Foundation"),
            decl("A.a3", deps=("Base.shared",), module="A.Core", library="A"),
            decl("A.a2", deps=("A.a3",), module="A.Core", library="A"),
            decl("A.head", deps=("A.a2",), module="A.Main", library="A"),
            decl("B.b2", deps=("Base.shared",), module="B.Core", library="B"),
            decl("B.b1", deps=("B.b2",), module="B.Core", library="B"),
            decl("B.head", deps=("B.b1",), module="B.Main", library="B"),
            decl("C.private1", module="C.Core", library="C"),
            decl("C.private2", deps=("C.private1",), module="C.Core", library="C"),
            decl("C.head", deps=("C.private2",), module="C.Main", library="C"),
            decl("Unused.outside", module="Unused.Core", library="Unused"),
        ]
        semantic = environment_dependency_graph(rows).to_json()
        semantic["payloadKind"] = "semantic-index"
        with tempfile.TemporaryDirectory() as dpath:
            census = Path(dpath) / "example-full-source-census.json"
            write_census(census, [
                {"id": "A", "title": "Headline A", "importance": "headline", "lean_declarations": ["A.head"], "semantic_review": {"canonical_declarations": ["A.head"], "supporting_declarations": ["A.a2"]}},
                {"id": "B", "title": "Headline B", "importance": "headline", "lean_declarations": ["B.head"], "semantic_review": {"canonical_declarations": ["B.head"], "supporting_declarations": []}},
                {"id": "C", "title": "Headline C", "importance": "headline", "lean_declarations": ["C.head"], "semantic_review": {"canonical_declarations": ["C.head"], "supporting_declarations": []}},
            ])
            result = analyze_headlines(semantic, census_paths=[census])

        nodes = {row["id"]: row for row in result["nodes"]}
        self.assertEqual(result["payloadKind"], "headline-dependencies")
        self.assertNotIn("Unused.outside", nodes)
        self.assertEqual(set(nodes), {row.name for row in rows} - {"Unused.outside"})
        self.assertTrue(nodes["A.head"]["headline"])
        self.assertEqual(nodes["A.a2"]["headlineRole"], "supporting")
        self.assertEqual(nodes["Base.shared"]["headlineCoverageCount"], 2)
        self.assertTrue(nodes["Base.shared"]["sharedDependency"])
        self.assertTrue(nodes["Base.shared"]["sharedFrontier"])
        self.assertEqual(
            {(edge["source"], edge["target"]) for edge in result["edges"]},
            set(environment_dependency_graph(rows[:-1]).edges),
        )
        headline_rows = {row["declaration"]: row for row in result["headlineAnalysis"]["headlines"]}
        self.assertEqual(headline_rows["A.head"]["distanceToNearestSharedDependency"], 3)
        self.assertEqual(headline_rows["B.head"]["distanceToNearestSharedDependency"], 3)
        self.assertIsNone(headline_rows["C.head"]["nearestSharedDependency"])
        pair = next(row for row in result["headlineAnalysis"]["headlinePairs"] if row["headlines"] == ["A.head", "B.head"])
        self.assertEqual(pair["nearestSharedDependency"], "Base.shared")
        self.assertEqual(pair["distances"], [3, 3])
        structural = set(result["structuralNodeIds"])
        self.assertIn("A.head", structural)
        self.assertIn("B.head", structural)
        self.assertIn("Base.shared", structural)
        self.assertTrue(any(edge["collapsedNodeCount"] > 0 for edge in result["structuralEdges"]))

    def test_whole_project_keeps_supporting_landmark_outside_canonical_cone(self):
        rows = [
            decl("Pkg.base", module="Pkg.Core", library="Pkg"),
            decl("Pkg.head", deps=("Pkg.base",), module="Pkg.Main", library="Pkg"),
            decl("Pkg.alternative", module="Pkg.Alternative", library="Pkg"),
        ]
        semantic = environment_dependency_graph(rows).to_json()
        semantic["payloadKind"] = "semantic-index"
        with tempfile.TemporaryDirectory() as dpath:
            census = Path(dpath) / "pkg-census.json"
            write_census(census, [{
                "id": "H", "title": "Headline", "importance": "headline",
                "lean_declarations": ["Pkg.head", "Pkg.alternative"],
                "semantic_review": {
                    "canonical_declarations": ["Pkg.head"],
                    "supporting_declarations": ["Pkg.alternative"],
                },
            }])
            result = prepare_project_explorer(semantic, census_paths=[census])
        nodes = {row["id"]: row for row in result["nodes"]}
        self.assertTrue(nodes["Pkg.head"]["headline"])
        self.assertEqual(nodes["Pkg.alternative"]["headlineRole"], "supporting")
        self.assertTrue(nodes["Pkg.alternative"]["supportingClaims"])


if __name__ == "__main__":
    unittest.main()

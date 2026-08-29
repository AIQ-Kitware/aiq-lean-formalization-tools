import unittest

from leanq.graph import (
    merge_declarations,
    projected_reduction,
    strongly_connected_components,
    target_dependency_graph,
    transitive_reduction,
)
from leanq.index import Decl


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


class DependencyGraphTests(unittest.TestCase):
    def test_target_closure_crosses_library_boundaries(self):
        rows = merge_declarations(
            [
                [decl("DK.base", library="DavisKahan", module="DavisKahan.Core")],
                [
                    decl(
                        "YWS.align",
                        deps=("DK.base",),
                        library="YuWangSamworth2015",
                        module="YuWangSamworth2015.Core",
                    )
                ],
                [
                    decl(
                        "Ach.config",
                        deps=("YWS.align", "Mathlib.sqrt"),
                        library="Acharyya2025",
                        module="Acharyya2025.Config",
                    )
                ],
            ]
        )
        graph = target_dependency_graph(rows.values(), ["Ach.config"])
        self.assertEqual(set(graph.nodes), {"DK.base", "YWS.align", "Ach.config"})
        self.assertEqual(
            graph.edges,
            frozenset({("DK.base", "YWS.align"), ("YWS.align", "Ach.config")}),
        )
        self.assertEqual(graph.unresolved, (("Ach.config", "Mathlib.sqrt"),))

    def test_duplicate_module_provenance_is_not_a_conflict(self):
        name = (
            "TauCeti.RectangularUnitarilyInvariantSeminorm."
            "rectangularKyFanSum.congr_simp"
        )
        deps = ("TauCeti.alpha", "TauCeti.beta")
        first = decl(
            name,
            deps=deps,
            module="ForTauCeti.Analysis.InnerProductSpace.Sylvester.Interval",
            library="ForTauCeti",
        )
        second = decl(
            name,
            deps=tuple(reversed(deps)),
            module=(
                "ForTauCeti.Analysis.InnerProductSpace.Sylvester.Internal."
                "ReciprocalMultiplier.DoubledPhase"
            ),
            library="ForTauCeti",
        )
        rows = merge_declarations([[second], [first]])
        merged = rows[name]
        self.assertEqual(
            merged.module,
            "ForTauCeti.Analysis.InnerProductSpace.Sylvester.Interval",
        )
        self.assertEqual(merged.deps, tuple(sorted(deps)))

    def test_duplicate_semantic_disagreement_still_fails(self):
        left = decl("Pkg.same", deps=("Pkg.a",), module="Pkg.One")
        right = decl("Pkg.same", deps=("Pkg.b",), module="Pkg.Two")
        with self.assertRaisesRegex(Exception, "direct dependencies differ"):
            merge_declarations([[left], [right]])

    def test_internal_support_is_kept_in_exact_graph(self):
        rows = [
            decl("YWS.public"),
            decl("_private.helper", deps=("YWS.public",), internal=True),
            decl("Ach.public", deps=("_private.helper",)),
        ]
        graph = target_dependency_graph(rows, ["Ach.public"])
        self.assertTrue(graph.nodes["_private.helper"].internal)
        self.assertEqual(
            graph.edges,
            frozenset(
                {
                    ("YWS.public", "_private.helper"),
                    ("_private.helper", "Ach.public"),
                }
            ),
        )

    def test_namespace_relative_dependency_is_reconciled(self):
        rows = [
            decl(
                "Pkg.Feature.support",
                module="Pkg.Feature",
            ),
            decl(
                "Pkg.Feature.capstone",
                deps=("support",),
                module="Pkg.Feature",
            ),
        ]
        graph = target_dependency_graph(rows, ["Pkg.Feature.capstone"])
        self.assertEqual(set(graph.nodes), {"Pkg.Feature.support", "Pkg.Feature.capstone"})
        self.assertEqual(
            graph.edges,
            frozenset({("Pkg.Feature.support", "Pkg.Feature.capstone")}),
        )

    def test_external_name_does_not_collide_by_short_name(self):
        rows = [
            decl("TauCeti.StandardCompletion.ofNat"),
            decl("TauCeti.Function.id"),
            decl("Pkg.capstone", deps=("OfNat.ofNat", "id")),
        ]
        graph = target_dependency_graph(rows, ["Pkg.capstone"])
        self.assertEqual(set(graph.nodes), {"Pkg.capstone"})
        self.assertEqual(graph.edges, frozenset())
        self.assertEqual(
            graph.unresolved,
            (("Pkg.capstone", "OfNat.ofNat"), ("Pkg.capstone", "id")),
        )

    def test_transitive_reduction_removes_shortcut(self):
        nodes = {"A", "B", "C", "D"}
        edges = {
            ("A", "B"),
            ("B", "C"),
            ("A", "C"),
            ("C", "D"),
            ("A", "D"),
        }
        self.assertEqual(
            transitive_reduction(nodes, edges),
            {("A", "B"), ("B", "C"), ("C", "D")},
        )

    def test_cycle_is_condensed_before_reduction(self):
        nodes = {"A", "B", "C", "D"}
        edges = {
            ("A", "B"),
            ("B", "A"),
            ("A", "C"),
            ("B", "C"),
            ("C", "D"),
            ("A", "D"),
        }
        components = strongly_connected_components(nodes, edges)
        self.assertIn(("A", "B"), components)
        reduced = transitive_reduction(nodes, edges)
        self.assertIn(("A", "B"), reduced)
        self.assertIn(("B", "A"), reduced)
        self.assertIn(("C", "D"), reduced)
        self.assertNotIn(("A", "D"), reduced)
        # Exactly one direct edge represents the SCC -> C condensation edge.
        self.assertEqual(len({e for e in reduced if e[1] == "C"}), 1)

    def test_headline_projection_keeps_witness_path(self):
        rows = [
            decl("YWS.align"),
            decl("helper.one", deps=("YWS.align",), internal=True),
            decl("helper.two", deps=("helper.one",), internal=True),
            decl("Ach.config", deps=("helper.two",)),
            decl("Quench.end", deps=("Ach.config",)),
        ]
        graph = target_dependency_graph(rows, ["Quench.end"])
        projected = projected_reduction(
            graph, ["YWS.align", "Ach.config", "Quench.end"]
        )
        self.assertEqual(
            projected,
            [
                {
                    "source": "Ach.config",
                    "target": "Quench.end",
                    "witness": ["Ach.config", "Quench.end"],
                },
                {
                    "source": "YWS.align",
                    "target": "Ach.config",
                    "witness": [
                        "YWS.align",
                        "helper.one",
                        "helper.two",
                        "Ach.config",
                    ],
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()

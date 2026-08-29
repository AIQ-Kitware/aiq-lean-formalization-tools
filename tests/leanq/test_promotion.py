import unittest

from leanq.index import Decl
from leanq.promotion import module_is_tagged, promotion_report


def decl(name, module, *, kind="theorem", deps=()):
    return Decl(
        name=name,
        module=module,
        kind=kind,
        is_prop=kind == "theorem",
        prop_valued=False,
        sorried=False,
        line=1,
        axioms=(),
        deps=tuple(deps),
    )


class PromotionTests(unittest.TestCase):
    def test_module_tags_are_exact_components(self):
        self.assertTrue(module_is_tagged("DavisKahan.Experimental.Foo"))
        self.assertTrue(module_is_tagged("DavisKahan.MathAhead.Foo"))
        self.assertFalse(module_is_tagged("DavisKahan.NotExperimental.Foo"))
        self.assertFalse(module_is_tagged("DavisKahan.MathAheadish.Foo"))

    def test_promotion_boundary_and_tagged_support_closure(self):
        rows = [
            decl("DK.public", "DavisKahan.Sources.Main", deps=("DK.endpoint",)),
            decl("DK.endpoint", "DavisKahan.MathAhead.Foo", deps=("DK.helper", "DK.stable")),
            decl("DK.helper", "DavisKahan.MathAhead.Foo"),
            decl("DK.stable", "DavisKahan.Analysis.Stable"),
            decl("DK.unused", "DavisKahan.Experimental.Bar"),
        ]
        report = promotion_report(rows)
        self.assertEqual(len(report.tagged_reachable), 3)
        self.assertEqual(
            [(e.decl.name, e.role) for e in report.entries],
            [("DK.endpoint", "boundary"), ("DK.helper", "support")],
        )
        self.assertEqual(report.entries[0].direct_consumers, ("DK.public",))
        self.assertEqual(report.count_kind("theorem"), 2)

    def test_dependency_only_records_keep_unknown_metadata_honest(self):
        row = Decl.from_json(
            {
                "name": "DK.endpoint",
                "module": "DavisKahan.MathAhead.Foo",
                "kind": "theorem",
                "isProp": None,
                "propValued": None,
                "sorried": None,
                "line": None,
                "axioms": None,
                "deps": [],
            }
        )
        self.assertIsNone(row.sorried)
        self.assertIsNone(row.axioms)
        self.assertIsNone(row.line)

    def test_consumer_prefix_can_narrow_the_boundary(self):
        rows = [
            decl("DK.source", "DavisKahan.Sources.Main", deps=("DK.endpoint",)),
            decl("DK.alt", "DavisKahan.Alternative.Main", deps=("DK.other",)),
            decl("DK.endpoint", "DavisKahan.MathAhead.Foo"),
            decl("DK.other", "DavisKahan.Experimental.Bar"),
        ]
        report = promotion_report(rows, consumer_prefixes=("DavisKahan.Sources",))
        self.assertEqual([e.decl.name for e in report.entries], ["DK.endpoint"])


if __name__ == "__main__":
    unittest.main()

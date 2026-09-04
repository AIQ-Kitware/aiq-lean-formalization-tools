import json
import unittest

from leanq.index import Decl
from leanq.statement import (
    StatementBinder,
    StatementRecord,
    by_name,
    closure_edges,
    closure_payload,
    closure_summary,
    render_closure_text,
)


def rec(name, *, kind="theorem", type_deps=(), body_deps=(), boundary=False,
        role="unfolded", module="Pkg.Main", library="Pkg", type="Prop", fields=(),
        docstring=None, flags=(), signature="", binders=(), result=""):
    return StatementRecord(
        name=name, module=module, kind=kind, library=library, role=role,
        boundary=boundary, is_prop=kind == "theorem", line=3,
        type_deps=tuple(type_deps), body_deps=tuple(body_deps), type=type,
        signature=signature, binders=tuple(binders), result=result,
        type_expr_hash="42", docstring=docstring, fields=tuple(fields), flags=tuple(flags),
    )


def sample():
    # main : uses the compact predicate IsGood (a def) and Mathlib's IsSelfAdjoint.
    # IsGood unfolds to the structure GoodData, whose fields mention Mathlib's Dense
    # and a project lemma helper_lemma (a leaf).
    return by_name([
        rec("Pkg.main", role="seed", type_deps=("Pkg.IsGood", "IsSelfAdjoint", "Real.instLE"),
            body_deps=("Pkg.proof_only",), signature="Pkg.main (x : ℝ) : Pkg.IsGood x"),
        rec("Pkg.IsGood", kind="def", type_deps=("Pkg.GoodData",),
            body_deps=("Pkg.GoodData", "Pkg.helper_lemma", "Dense"),
            type="Prop", docstring="Compact predicate."),
        rec("Pkg.GoodData", kind="inductive", type_deps=(), body_deps=("Dense", "Pkg.helper_lemma", "Pkg.GoodData.mk"),
            type="Type", fields=({"name": "dense", "projection": "Pkg.GoodData.dense",
                                   "type": "Dense s"},)),
        rec("Pkg.helper_lemma", kind="theorem"),
        rec("Pkg.proof_only", kind="theorem"),
        rec("IsSelfAdjoint", kind="def", boundary=True, role="boundary",
            module="Mathlib.Algebra.Star.SelfAdjoint", library="Mathlib",
            body_deps=("star",)),
        rec("Dense", kind="def", boundary=True, role="boundary",
            module="Mathlib.Topology.Basic", library="Mathlib", body_deps=("closure",)),
        rec("Real.instLE", kind="def", boundary=True, role="boundary", flags=("instance",),
            module="Mathlib.Data.Real.Basic", library="Mathlib"),
        rec("Pkg.GoodData.mk", kind="ctor", type_deps=("Pkg.GoodData",)),
    ])


class ClosureTests(unittest.TestCase):
    def test_edges_distinguish_type_from_body_and_stop_at_boundary(self):
        edges = closure_edges(sample(), "Pkg.main")
        self.assertIn(("Pkg.main", "Pkg.IsGood", "type"), edges)
        self.assertIn(("Pkg.main", "IsSelfAdjoint", "type"), edges)
        # The seed is a theorem: its proof is not part of its statement.
        self.assertNotIn(("Pkg.main", "Pkg.proof_only", "body"), edges)
        self.assertIn(("Pkg.IsGood", "Pkg.helper_lemma", "body"), edges)
        self.assertIn(("Pkg.GoodData", "Dense", "body"), edges)
        # Boundary constants are leaves: nothing is reached through them.
        self.assertFalse(any(parent in {"IsSelfAdjoint", "Dense"} for parent, _, _ in edges))
        # A project theorem reached through a body is a leaf too.
        self.assertFalse(any(parent == "Pkg.helper_lemma" for parent, _, _ in edges))

    def test_summary_partitions_reached_constants(self):
        summary = closure_summary(sample(), "Pkg.main")
        self.assertEqual(summary["unfolded"], ["Pkg.IsGood", "Pkg.GoodData"])
        self.assertEqual(summary["boundary"], ["IsSelfAdjoint", "Dense"])
        self.assertEqual(summary["plumbing"], ["Real.instLE", "Pkg.GoodData.mk"])
        self.assertEqual(summary["leaves"], ["Pkg.helper_lemma"])
        self.assertEqual(summary["unknown"], [])

    def test_unknown_constants_are_reported_not_dropped(self):
        table = dict(sample())
        table["Pkg.main"] = rec("Pkg.main", role="seed", type_deps=("Pkg.Ghost",))
        summary = closure_summary(table, "Pkg.main")
        self.assertEqual(summary["unknown"], ["Pkg.Ghost"])
        text = render_closure_text(table, "Pkg.main")
        self.assertIn("Pkg.Ghost  (no record)", text)

    def test_seed_that_is_a_boundary_constant_is_still_expanded(self):
        edges = closure_edges(sample(), "IsSelfAdjoint")
        self.assertEqual(edges, [("IsSelfAdjoint", "star", "body")])

    def test_render_prints_each_constant_once_with_fields(self):
        text = render_closure_text(sample(), "Pkg.main", show_docstrings=True)
        self.assertEqual(text.count("Pkg.GoodData  (inductive"), 1)
        self.assertIn("Pkg.main (x : ℝ) : Pkg.IsGood x", text)
        self.assertIn("field dense : Dense s", text)
        self.assertIn("doc: Compact predicate.", text)
        self.assertIn("[type] IsSelfAdjoint  (def, boundary", text)
        self.assertIn("via instances/projections: Real.instLE", text)
        self.assertNotIn("[type] Real.instLE", text)
        self.assertIn(
            "2 project constant(s) unfolded, 1 project leaf/leaves, 2 boundary constant(s), "
            "2 instance/projection constant(s)", text)
        hidden = render_closure_text(sample(), "Pkg.main", show_boundary=False)
        self.assertNotIn("IsSelfAdjoint  (def", hidden)
        self.assertNotIn("Dense  (above)", hidden)
        self.assertIn("Pkg.helper_lemma  (above)", hidden)
        self.assertNotIn("via instances", hidden)
        self.assertIn("boundary: IsSelfAdjoint, Dense", hidden)

    def test_payload_round_trips_records_and_typed_edges(self):
        payload = closure_payload(sample(), ["Pkg.main"], meta={"toolchain": "v4"})
        self.assertEqual(payload["payloadKind"], "statement-closure")
        self.assertEqual(payload["meta"], {"toolchain": "v4"})
        vias = {(e["source"], e["target"]): e["via"] for e in payload["edges"]}
        self.assertEqual(vias[("Pkg.main", "Pkg.IsGood")], "type")
        self.assertEqual(vias[("Pkg.IsGood", "Pkg.helper_lemma")], "body")
        names = [r["name"] for r in payload["records"]]
        self.assertEqual(names[0], "Pkg.main")
        back = [StatementRecord.from_json(r) for r in json.loads(json.dumps(payload["records"]))]
        self.assertEqual(back[0].type_deps, ("Pkg.IsGood", "IsSelfAdjoint", "Real.instLE"))
        self.assertEqual(back[0].signature, "Pkg.main (x : ℝ) : Pkg.IsGood x")
        self.assertEqual(back[0].to_json()["typeTextSha256"], sample()["Pkg.main"].type_text_sha256)

    def test_structured_statement_shape_round_trips(self):
        record = rec(
            "Pkg.bound",
            signature="Pkg.bound (x : ℝ) (hx : 0 < x) : x <= x",
            binders=(
                StatementBinder(0, "x", "explicit", "ℝ", ("Real",)),
                StatementBinder(1, "hx", "explicit", "0 < x", ("LT.lt",)),
            ),
            result="x ≤ x",
        )
        back = StatementRecord.from_json(json.loads(json.dumps(record.to_json())))
        self.assertEqual(back.binders, record.binders)
        self.assertEqual(back.result, "x ≤ x")
        self.assertEqual(back.binders[1].name, "hx")
        self.assertEqual(back.binders[1].type_deps, ("LT.lt",))

    def test_missing_record_round_trip(self):
        missing = StatementRecord.from_json({"name": "Pkg.gone", "role": "seed", "missing": True})
        self.assertTrue(missing.missing)
        self.assertEqual(missing.to_json(), {"name": "Pkg.gone", "role": "seed", "missing": True})
        self.assertFalse(missing.expands)

    def test_text_hash_tracks_the_printed_type(self):
        a = rec("Pkg.x", type="∀ (x : ℝ), x = x")
        b = rec("Pkg.x", type="∀ (y : ℝ), y = y")
        self.assertNotEqual(a.type_text_sha256, b.type_text_sha256)
        self.assertEqual(a.type_expr_hash, b.type_expr_hash)


class DeclTypeDepsTests(unittest.TestCase):
    def test_type_deps_round_trip_and_role(self):
        obj = {"name": "Pkg.t", "module": "Pkg.M", "kind": "theorem",
               "deps": ["Pkg.a", "Pkg.b"], "typeDeps": ["Pkg.a"]}
        decl = Decl.from_json(obj)
        self.assertEqual(decl.type_deps, ("Pkg.a",))
        self.assertEqual(decl.dependency_role("Pkg.a"), "type")
        self.assertEqual(decl.dependency_role("Pkg.b"), "proof")
        self.assertEqual(Decl.from_json(decl.to_json()), decl)

    def test_old_index_without_type_deps_reads_as_unknown(self):
        decl = Decl.from_json({"name": "Pkg.t", "module": "Pkg.M", "kind": "theorem", "deps": ["Pkg.a"]})
        self.assertIsNone(decl.type_deps)
        self.assertEqual(decl.dependency_role("Pkg.a"), "unknown")
        self.assertIsNone(decl.to_json()["typeDeps"])


if __name__ == "__main__":
    unittest.main()

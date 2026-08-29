from aiq_lean_tools.lean_backend import CommandResult
from aiq_lean_tools.signatures import SignaturePolicy, compare_signatures


class SequenceBackend:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def run(self, root, argv, *, timeout=3600):
        self.calls.append((root, tuple(argv), timeout))
        text = self.outputs.pop(0)
        if isinstance(text, CommandResult):
            return text
        return CommandResult(tuple(argv), 0, text, "")

    def probe_queries(self, root, queries, imports, *, timeout=3600):
        raise AssertionError("not used")

    def probe_declarations(self, root, declarations, imports, *, timeout=3600):
        raise AssertionError("not used")


def policy():
    return SignaturePolicy.from_mapping({
        "build": False,
        "pairs": [{
            "name": "candidate",
            "left_module": "Challenge",
            "right_module": "Solution",
            "declarations": ["Demo.theorem"],
            "left_label": "challenge",
            "right_label": "solution",
        }],
    })


def test_signature_compare_pass(tmp_path):
    (tmp_path / "lakefile.toml").write_text("")
    outputs = [
        "theorem Demo.theorem.{u_1, u_2} : P := by trivial\n",
        "@Demo.theorem : @Eq.{u_1} Nat Nat\n",
        "theorem Demo.theorem.{u_1, u_2} : P := by trivial\n",
        "@Demo.theorem : @Eq.{u_1} Nat Nat\n",
    ]
    report = compare_signatures(policy(), root=tmp_path, backend=SequenceBackend(outputs))
    assert report.ok
    assert report.comparisons[0].status == "PASS"
    assert report.comparisons[0].left.universe_signature == ".{u_1, u_2}"


def test_signature_compare_detects_universe_and_type_drift(tmp_path):
    (tmp_path / "lakefile.toml").write_text("")
    outputs = [
        "theorem Demo.theorem.{u_1, u_2} : P := by trivial\n",
        "@Demo.theorem : @Eq.{u_1} Nat Nat\n",
        "theorem Demo.theorem.{u_1, u_3} : P := by trivial\n",
        "@Demo.theorem : @Eq.{u_1} Int Int\n",
    ]
    report = compare_signatures(policy(), root=tmp_path, backend=SequenceBackend(outputs))
    codes = {f.code for f in report.findings}
    assert not report.ok
    assert "signature-universe-mismatch" in codes
    assert "signature-type-mismatch" in codes
    assert report.comparisons[0].status == "FAIL"


def test_signature_policy_accepts_legacy_comparator_shape():
    cfg = SignaturePolicy.from_mapping({
        "challenge_module": "Challenge",
        "solution_module": "Solution",
        "theorem_names": ["A", "B"],
        "build": False,
    })
    assert cfg.pairs[0].left_module == "Challenge"
    assert cfg.pairs[0].declarations == ("A", "B")

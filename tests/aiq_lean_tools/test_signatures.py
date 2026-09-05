from aiq_lean_tools.lean_backend import CommandResult, LeanQueryProbe
from aiq_lean_tools.signatures import SignaturePolicy, compare_signatures


class SequenceBackend:
    """Hands back the queued outputs one query at a time.

    Signature evidence is batched per module -- one Lean run for every
    declaration in it -- so the backend sees one `probe_queries` call per module,
    with a `print` and a `check_pp_all` query per declaration.
    """

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
        self.calls.append((root, tuple(queries), tuple(imports), timeout))
        rows = []
        for mode, name in queries:
            text = self.outputs.pop(0)
            rows.append(LeanQueryProbe(mode, name, True, text))
        return rows

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


def test_one_lean_run_per_module_not_per_declaration(tmp_path):
    (tmp_path / "lakefile.toml").write_text("")
    policy = SignaturePolicy.from_mapping({
        "build": False,
        "pairs": [{
            "name": "candidate",
            "left_module": "Challenge",
            "right_module": "Solution",
            "declarations": ["Demo.one", "Demo.two"],
        }],
    })
    outputs = [
        "theorem Demo.one.{u} : P := by trivial\n", "@Demo.one : Nat\n",
        "theorem Demo.two.{u} : P := by trivial\n", "@Demo.two : Nat\n",
        "theorem Demo.one.{u} : P := by trivial\n", "@Demo.one : Nat\n",
        "theorem Demo.two.{u} : P := by trivial\n", "@Demo.two : Nat\n",
    ]
    backend = SequenceBackend(outputs)
    report = compare_signatures(policy, root=tmp_path, backend=backend)
    assert report.ok
    # Two modules, two declarations each: two probe calls, not four.
    assert len(backend.calls) == 2
    assert [len(call[1]) for call in backend.calls] == [4, 4]


def _expected_missing_policy():
    return SignaturePolicy.from_mapping({
        "build": False,
        "challenge_module": "Challenge",
        "solution_module": "Solution",
        "theorem_names": ["Demo.theorem", "Demo.openPlaceholder"],
        "expected_missing_solution_theorems": ["Demo.openPlaceholder"],
    })


def test_expected_missing_right_is_skipped_not_an_error(tmp_path):
    """A declaration the config declares absent on the right is not a failure.

    The DKPS comparator legitimately pins statements the Leaderboard does not
    re-export.  Reporting those as `signature-right-unresolved` made them
    indistinguishable from a rename that orphaned a pinned name.
    """
    (tmp_path / "lakefile.toml").write_text("")
    outputs = [
        # left module: both declarations resolve
        "theorem Demo.theorem.{u} : P := by trivial\n",
        "@Demo.theorem : @Eq.{u} Nat Nat\n",
        "theorem Demo.openPlaceholder.{u} : Q := by trivial\n",
        "@Demo.openPlaceholder : @Eq.{u} Int Int\n",
        # right module: only the first resolves
        "theorem Demo.theorem.{u} : P := by trivial\n",
        "@Demo.theorem : @Eq.{u} Nat Nat\n",
        "",
        "",
    ]
    report = compare_signatures(
        _expected_missing_policy(), root=tmp_path, backend=SequenceBackend(outputs))
    assert report.ok
    statuses = {row.declaration: row.status for row in report.comparisons}
    assert statuses == {"Demo.theorem": "PASS", "Demo.openPlaceholder": "SKIP"}


def test_expected_missing_right_that_resolves_is_reported(tmp_path):
    """A stale `expected_missing` entry is itself a finding.

    If the solution module grows the declaration, the config is out of date and
    the comparison it suppresses is one nobody is running.
    """
    (tmp_path / "lakefile.toml").write_text("")
    outputs = [
        "theorem Demo.theorem.{u} : P := by trivial\n",
        "@Demo.theorem : @Eq.{u} Nat Nat\n",
        "theorem Demo.openPlaceholder.{u} : Q := by trivial\n",
        "@Demo.openPlaceholder : @Eq.{u} Int Int\n",
        "theorem Demo.theorem.{u} : P := by trivial\n",
        "@Demo.theorem : @Eq.{u} Nat Nat\n",
        "theorem Demo.openPlaceholder.{u} : Q := by trivial\n",
        "@Demo.openPlaceholder : @Eq.{u} Int Int\n",
    ]
    report = compare_signatures(
        _expected_missing_policy(), root=tmp_path, backend=SequenceBackend(outputs))
    assert not report.ok
    codes = {f.code for f in report.findings}
    assert "signature-unexpectedly-present" in codes


def test_expected_missing_right_must_name_a_pinned_declaration():
    import pytest
    from aiq_lean_tools.errors import ValidationError
    with pytest.raises(ValidationError):
        SignaturePolicy.from_mapping({
            "build": False,
            "challenge_module": "Challenge",
            "solution_module": "Solution",
            "theorem_names": ["Demo.theorem"],
            "expected_missing_solution_theorems": ["Demo.notPinned"],
        })

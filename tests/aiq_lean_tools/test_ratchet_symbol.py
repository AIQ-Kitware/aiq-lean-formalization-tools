from __future__ import annotations

from aiq_lean_tools.ratchet import evaluate_ratchets, load_ratchet_policy
from aiq_lean_tools.symbol_census import symbol_census


def test_ratchet_policy(tmp_path):
    (tmp_path / "Lib").mkdir()
    (tmp_path / "Lib/A.lean").write_text("@[expose]\ndef a := 1\n-- @[expose]\n")
    (tmp_path / "Lib/B.lean").write_text("@[expose]\ndef b := 2\n@[expose]\ndef c := 3\n")
    policy = tmp_path / "ratchet.yaml"
    policy.write_text(
        """rules:
  - id: exposed-declarations
    paths: [Lib/*.lean]
    pattern: '^@\\[expose\\]$'
    maximum: 2
    count: matches
    strip_comments: true
  - id: files-with-expose
    paths: [Lib/*.lean]
    pattern: '^@\\[expose\\]$'
    maximum: 2
    count: files
    strip_comments: true
"""
    )
    rows = evaluate_ratchets(tmp_path, load_ratchet_policy(policy))
    assert rows[0].value == 3
    assert not rows[0].ok
    assert rows[1].value == 2
    assert rows[1].ok


def test_symbol_census_removes_prose_imports_and_commands(tmp_path):
    (tmp_path / "Lib").mkdir()
    (tmp_path / "Lib/A.lean").write_text(
        """import ClosedOperator
/-- ClosedOperator in docs -/
#check ClosedOperator
def f (x : ClosedOperator) := x
-- ClosedOperator comment
"""
    )
    report = symbol_census(tmp_path, symbol="ClosedOperator", include=("Lib/*.lean",))
    assert report.raw_total == 5
    assert report.code_total == 1
    assert report.noise_total == 4

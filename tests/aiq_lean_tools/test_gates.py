from __future__ import annotations

import sys

from aiq_lean_tools.gates import GateSuiteConfig, discover_gates, run_gate_suite


def _script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_gate_discovery_and_run(tmp_path):
    _script(
        tmp_path / "scripts/check_soft.py",
        """import argparse
p=argparse.ArgumentParser(); p.add_argument('--check', action='store_true'); a=p.parse_args()
raise SystemExit(1 if a.check else 0)
""",
    )
    _script(tmp_path / "scripts/check_strict.py", "raise SystemExit(0)\n")
    _script(
        tmp_path / "scripts/check_stronger.py",
        """import argparse
p=argparse.ArgumentParser(); p.add_argument('--check', action='store_true'); a=p.parse_args()
raise SystemExit(1 if a.check else 0)
""",
    )
    _script(tmp_path / "scripts/check_missing.py", "raise SystemExit(3)\n")
    _script(tmp_path / "scripts/check_note.py", "print('candidate note'); raise SystemExit(1)\n")

    config = GateSuiteConfig(
        slow=frozenset({"check_strict"}),
        check_is_stronger={"check_stronger": "completion mode"},
        advisory={"check_note": "human-review candidate"},
        unavailable_exit_code=3,
    )
    gates = {gate.name: gate for gate in discover_gates(tmp_path, config)}
    assert gates["check_soft"].soft
    assert gates["check_soft"].pass_check
    assert not gates["check_stronger"].pass_check
    assert gates["check_strict"].slow

    results, skipped = run_gate_suite(tmp_path, config=config, python=sys.executable)
    status = {row.gate.name: row.status for row in results}
    assert status == {
        "check_missing": "unavailable",
        "check_note": "advisory",
        "check_soft": "failed",
        "check_strict": "passed",
        "check_stronger": "passed",
    }
    assert skipped == []

    results, skipped = run_gate_suite(tmp_path, config=config, fast=True, python=sys.executable)
    assert [gate.name for gate in skipped] == ["check_strict"]
    assert "check_strict" not in {row.gate.name for row in results}

from __future__ import annotations

import sys

import pytest

from aiq_lean_tools.errors import FormalizationToolsError
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


def test_declared_command_gates_run_alongside_scripts(tmp_path):
    _script(tmp_path / "scripts/check_script.py", "raise SystemExit(0)\n")
    (tmp_path / "suite.yaml").write_text(
        "pattern: scripts/check_*.py\n"
        "gates:\n"
        "  - name: policy-ok\n"
        "    command: [\"%s\", \"-c\", \"print('fine')\"]\n"
        "    description: declared command gate\n"
        "  - name: policy-slow\n"
        "    command: [\"%s\", \"-c\", \"raise SystemExit(1)\"]\n"
        "    slow: true\n" % (sys.executable, sys.executable)
    )
    config = GateSuiteConfig.load(tmp_path / "suite.yaml")
    gates = {gate.name: gate for gate in discover_gates(tmp_path, config)}
    assert set(gates) == {"check_script", "policy-ok", "policy-slow"}
    assert gates["policy-ok"].declared
    assert not gates["check_script"].declared
    assert gates["policy-slow"].slow
    assert gates["policy-ok"].command() == [sys.executable, "-c", "print('fine')"]

    results, skipped = run_gate_suite(tmp_path, config=config, fast=True)
    assert [gate.name for gate in skipped] == ["policy-slow"]
    assert {row.gate.name: row.status for row in results} == {
        "check_script": "passed",
        "policy-ok": "passed",
    }

    results, _ = run_gate_suite(tmp_path, config=config)
    assert {row.gate.name: row.status for row in results}["policy-slow"] == "failed"


def test_empty_pattern_disables_script_discovery(tmp_path):
    _script(tmp_path / "scripts/check_script.py", "raise SystemExit(0)\n")
    (tmp_path / "suite.yaml").write_text(
        "pattern: \"\"\ngates:\n  - name: only\n    command: [\"%s\", \"-c\", \"pass\"]\n" % sys.executable
    )
    config = GateSuiteConfig.load(tmp_path / "suite.yaml")
    assert [gate.name for gate in discover_gates(tmp_path, config)] == ["only"]


def test_declared_gate_requires_an_argv_list(tmp_path):
    (tmp_path / "suite.yaml").write_text("gates:\n  - name: shell\n    command: \"echo hi\"\n")
    with pytest.raises(FormalizationToolsError):
        GateSuiteConfig.load(tmp_path / "suite.yaml")


def test_declared_gate_names_must_be_unique(tmp_path):
    (tmp_path / "suite.yaml").write_text(
        "gates:\n"
        "  - name: dup\n    command: [\"true\"]\n"
        "  - name: dup\n    command: [\"true\"]\n"
    )
    with pytest.raises(FormalizationToolsError):
        GateSuiteConfig.load(tmp_path / "suite.yaml")

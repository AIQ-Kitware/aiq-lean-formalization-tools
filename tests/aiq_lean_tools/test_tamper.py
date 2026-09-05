import subprocess
import sys

import pytest

from aiq_lean_tools.errors import FormalizationToolsError
from aiq_lean_tools.tamper import TamperConfig, run_tamper_suite


def _git_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    return root


def _commit(root):
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=root, check=True)


def _write_config(root, *, find="GOOD", replace="BAD", occurrences=1, gate="gate.py"):
    config = root.parent / "tamper.yaml"
    config.write_text(
        "mutations:\n"
        "  - name: swap\n"
        "    file: data.txt\n"
        f"    find: {find}\n"
        f"    replace: {replace}\n"
        f"    occurrences: {occurrences}\n"
        f'    detected_by: ["{sys.executable}", "{gate}"]\n',
        encoding="utf-8",
    )
    return TamperConfig.load(config)


def test_a_gate_that_reads_the_file_detects_the_mutation_and_the_tree_is_restored(tmp_path):
    root = _git_repo(tmp_path)
    (root / "data.txt").write_text("value GOOD\n", encoding="utf-8")
    (root / "gate.py").write_text(
        "import pathlib, sys\n"
        "sys.exit(0 if 'GOOD' in pathlib.Path('data.txt').read_text() else 1)\n",
        encoding="utf-8",
    )
    _commit(root)

    report = run_tamper_suite(_write_config(root), root=root)

    assert report.ok
    assert report.restored
    assert [r.status for r in report.results] == ["detected"]
    assert (root / "data.txt").read_text(encoding="utf-8") == "value GOOD\n"


def test_a_gate_that_ignores_the_field_is_reported_undetected(tmp_path):
    """The whole point: a check that never looks is indistinguishable from a
    passing one until something tampers with what it claims to check."""
    root = _git_repo(tmp_path)
    (root / "data.txt").write_text("value GOOD\n", encoding="utf-8")
    (root / "gate.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    _commit(root)

    report = run_tamper_suite(_write_config(root), root=root)

    assert not report.ok
    assert [r.name for r in report.undetected] == ["swap"]
    assert (root / "data.txt").read_text(encoding="utf-8") == "value GOOD\n"


def test_a_gate_already_failing_on_the_clean_tree_proves_nothing(tmp_path):
    root = _git_repo(tmp_path)
    (root / "data.txt").write_text("value GOOD\n", encoding="utf-8")
    (root / "gate.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    _commit(root)

    report = run_tamper_suite(_write_config(root), root=root)

    assert not report.ok
    assert [r.status for r in report.results] == ["error"]
    assert "clean tree" in report.results[0].detail


def test_a_find_string_that_does_not_match_exactly_is_an_error_not_a_pass(tmp_path):
    root = _git_repo(tmp_path)
    (root / "data.txt").write_text("GOOD GOOD\n", encoding="utf-8")
    (root / "gate.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    _commit(root)

    report = run_tamper_suite(_write_config(root, occurrences=1), root=root)

    assert not report.ok
    assert report.results[0].status == "error"
    assert "found 2" in report.results[0].detail


def test_a_dirty_tree_is_refused_because_restoration_could_not_be_verified(tmp_path):
    root = _git_repo(tmp_path)
    (root / "data.txt").write_text("value GOOD\n", encoding="utf-8")
    (root / "gate.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    _commit(root)
    (root / "data.txt").write_text("value GOOD edited\n", encoding="utf-8")

    with pytest.raises(FormalizationToolsError, match="dirty"):
        run_tamper_suite(_write_config(root), root=root)


def test_the_file_is_restored_even_when_the_gate_command_explodes(tmp_path):
    root = _git_repo(tmp_path)
    (root / "data.txt").write_text("value GOOD\n", encoding="utf-8")
    (root / "gate.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    _commit(root)

    report = run_tamper_suite(_write_config(root), root=root)

    assert report.restored
    assert (root / "data.txt").read_text(encoding="utf-8") == "value GOOD\n"


def test_a_mutation_that_changes_nothing_is_rejected_at_load(tmp_path):
    (tmp_path / "tamper.yaml").write_text(
        "mutations:\n"
        "  - name: noop\n"
        "    file: data.txt\n"
        "    find: SAME\n"
        "    replace: SAME\n"
        '    detected_by: ["true"]\n',
        encoding="utf-8",
    )
    with pytest.raises(FormalizationToolsError, match="identical"):
        TamperConfig.load(tmp_path / "tamper.yaml")

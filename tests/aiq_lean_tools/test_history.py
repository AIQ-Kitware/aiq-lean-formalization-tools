import shutil
import subprocess
from pathlib import Path

import pytest

from aiq_lean_tools.history import history_summary, load_git_history, parse_coauthors, render_history_html


def test_parse_coauthors_variants():
    body = """Message\n\nCo-authored-by: Model One <model@example.com>\nCo Authored By: Model Two model2@example.com\n"""
    assert parse_coauthors(body) == [
        ("Model One", "model@example.com"),
        ("Model Two", "model2@example.com"),
    ]


@pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")
def test_git_history(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=tmp_path, check=True)
    (tmp_path / "Foo.lean").write_text("theorem foo : True := by trivial\n")
    subprocess.run(["git", "add", "Foo.lean"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "add theorem", "-m", "Co-authored-by: Agent Model <agent@example.com>"],
        cwd=tmp_path,
        check=True,
    )
    commits = load_git_history(tmp_path)
    assert len(commits) == 1
    assert commits[0].lean_additions == 1
    data = history_summary(commits)
    assert data["coauthors"] == {"Agent Model": 1}
    assert "add theorem" in render_history_html(commits)

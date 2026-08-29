from __future__ import annotations

import subprocess

from aiq_lean_tools.checklist import update_checklists


def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def test_checklist_preserves_marks_across_rename(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "Lib").mkdir()
    (tmp_path / "Lib/A.lean").write_text("def a := 1\n")
    _git(tmp_path, "add", "Lib/A.lean")
    _git(tmp_path, "commit", "-m", "initial")

    update_checklists(
        tmp_path,
        file_path="dev/audit/FILE-CHECKLIST.md",
        group_path="dev/audit/GROUP-CHECKLIST.md",
        include=("*.lean",),
    )
    file_list = tmp_path / "dev/audit/FILE-CHECKLIST.md"
    file_list.write_text(file_list.read_text().replace("- [ ] `Lib/A.lean`", "- [x] `Lib/A.lean`"))

    _git(tmp_path, "mv", "Lib/A.lean", "Lib/B.lean")
    result = update_checklists(
        tmp_path,
        file_path="dev/audit/FILE-CHECKLIST.md",
        group_path="dev/audit/GROUP-CHECKLIST.md",
        include=("*.lean",),
    )
    text = file_list.read_text()
    assert "- [x] `Lib/B.lean`" in text
    assert result.files_done == 1
    assert result.files_total == 1

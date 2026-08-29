import json
import subprocess

from aiq_lean_tools.certification import (
    CertificationPlan,
    archive_certification_bundle,
    build_certification_bundle,
)
from aiq_lean_tools.lean_backend import CommandResult


class Runner:
    def __init__(self, codes):
        self.codes = list(codes)
    def run(self, root, argv, *, timeout=3600):
        code = self.codes.pop(0)
        return CommandResult(tuple(argv), code, "hello\n", "")


def _git_init(root):
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def test_certification_bundle(tmp_path):
    (tmp_path / "formalization.yaml").write_text("version: '1'\nproject:\n  name: Demo\n")
    (tmp_path / "Demo.lean").write_text("theorem demo : True := by trivial\n")
    _git_init(tmp_path)
    plan = CertificationPlan.from_mapping({
        "name": "Demo certificate",
        "inputs": ["formalization.yaml"],
        "source_tree": {"include": ["**/*.lean", "*.lean"]},
        "tool_versions": {"lean": ["lean", "--version"]},
        "commands": [{"name": "validate", "argv": ["aiq-lean", "workspace", "validate"]}],
    })
    out = tmp_path.parent / "certificate"
    report = build_certification_bundle(plan, root=tmp_path, output_dir=out, runner=Runner([0, 0]))
    assert report.ok
    metadata = json.loads((out / "metadata.json").read_text())
    assert metadata["source_tree"]["files"][0]["path"] == "Demo.lean"
    assert metadata["git"]["commit"]
    assert (out / "inputs/formalization.yaml").exists()
    assert (out / "logs/validate.txt").exists()
    assert "Demo certificate" in (out / "README.md").read_text()
    archive = archive_certification_bundle(out)
    assert archive.exists()


def test_certification_required_command_failure(tmp_path):
    (tmp_path / "formalization.yaml").write_text("version: '1'\nproject:\n  name: Demo\n")
    (tmp_path / "Demo.lean").write_text("")
    plan = CertificationPlan.from_mapping({
        "inputs": ["formalization.yaml"],
        "commands": [{"name": "gate", "argv": ["false"]}],
    })
    report = build_certification_bundle(plan, root=tmp_path, output_dir=tmp_path.parent / "badcert", runner=Runner([1]))
    assert not report.ok
    assert "certification-command" in {f.code for f in report.findings}

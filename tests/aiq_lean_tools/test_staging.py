from aiq_lean_tools.lean_backend import CommandResult
from aiq_lean_tools.staging import StagingPolicy, check_staging


def _write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class Backend:
    def __init__(self, returncodes):
        self.returncodes = list(returncodes)
    def run(self, root, argv, *, timeout=3600):
        code = self.returncodes.pop(0)
        return CommandResult(tuple(argv), code, "", "")
    def probe_queries(self, *args, **kwargs):
        raise AssertionError
    def probe_declarations(self, *args, **kwargs):
        raise AssertionError


def test_staging_static_accepts_legacy_path(tmp_path):
    (tmp_path / "lakefile.toml").write_text("")
    _write(tmp_path / "Demo/Experimental/A.lean")
    policy = StagingPolicy.from_mapping({
        "roots": [{"module": "Demo/Experimental/A.lean", "status": "parked", "reason": "blocked"}],
    })
    report = check_staging(policy, root=tmp_path)
    assert report.ok
    assert report.normalized_modules == ("Demo.Experimental.A",)
    assert not report.compilation_checked


def test_staging_compile_and_stale_aggregate(tmp_path):
    (tmp_path / "lakefile.toml").write_text("")
    _write(tmp_path / "Demo/Experimental/A.lean")
    policy = StagingPolicy.from_mapping({
        "records": [{"module": "Demo.Experimental.A", "status": "active", "reason": "under repair"}],
        "aggregate": {"target": "Demo.Experimental", "require_success": True, "require_empty_on_success": True},
    })
    report = check_staging(policy, root=tmp_path, compile=True, backend=Backend([0, 0]))
    assert not report.ok
    assert "staging-stale-registry" in {f.code for f in report.findings}


def test_staging_compile_failure(tmp_path):
    (tmp_path / "lakefile.toml").write_text("")
    _write(tmp_path / "Demo/Experimental/A.lean")
    policy = StagingPolicy.from_mapping({
        "records": [{"module": "Demo.Experimental.A", "status": "active", "reason": "under repair"}],
    })
    report = check_staging(policy, root=tmp_path, compile=True, backend=Backend([1]))
    assert "staging-compile" in {f.code for f in report.findings}

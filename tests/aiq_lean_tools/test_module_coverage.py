from aiq_lean_tools.module_coverage import ModuleCoveragePolicy, check_module_coverage
from aiq_lean_tools.lean_source import scan_lean_project


def _write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_module_coverage_finds_unreached_module(tmp_path):
    _write(tmp_path / "Proj" / "Experimental.lean", "import Proj.Experimental.A\n")
    _write(tmp_path / "Proj" / "Experimental" / "A.lean", "")
    _write(tmp_path / "Proj" / "Experimental" / "B.lean", "")
    policy = ModuleCoveragePolicy.from_mapping({
        "roots": ["Proj.Experimental"],
        "scope": ["Proj.Experimental"],
    })
    report = check_module_coverage(policy, index=scan_lean_project(tmp_path))
    assert report.uncovered == ("Proj.Experimental.B",)
    assert {f.code for f in report.findings} == {"module-coverage-unreached"}


def test_module_coverage_exclusions_and_inheritance(tmp_path):
    _write(tmp_path / "Proj" / "Experimental.lean", "import Proj.Experimental.A\n")
    _write(tmp_path / "Proj" / "Experimental" / "A.lean", "")
    _write(tmp_path / "Proj" / "Experimental" / "Broken.lean", "")
    _write(tmp_path / "Proj" / "Experimental" / "Depends.lean", "import Proj.Experimental.Broken\n")
    _write(tmp_path / "Proj" / "Experimental" / "Scratch" / "Idea.lean", "")
    policy = ModuleCoveragePolicy.from_mapping({
        "roots": "Proj.Experimental",
        "scope": "Proj.Experimental",
        "excluded_modules": {"Proj.Experimental.Broken": "does not elaborate yet"},
        "excluded_prefixes": {"Proj.Experimental.Scratch": "scratch modules are intentionally not aggregated"},
    })
    report = check_module_coverage(policy, index=scan_lean_project(tmp_path))
    assert report.uncovered == ()
    assert report.explicitly_excluded == ("Proj.Experimental.Broken",)
    assert report.inherited_excluded == ("Proj.Experimental.Depends",)
    assert report.prefix_excluded == ("Proj.Experimental.Scratch.Idea",)
    assert not [f for f in report.findings if f.level == "error"]


def test_module_coverage_reports_stale_exclusion(tmp_path):
    _write(tmp_path / "Proj" / "Experimental.lean", "")
    policy = ModuleCoveragePolicy.from_mapping({
        "roots": "Proj.Experimental",
        "excluded_modules": {"Proj.Experimental.Gone": "old failure"},
    })
    report = check_module_coverage(policy, index=scan_lean_project(tmp_path))
    assert "module-coverage-stale-exclusion" in {f.code for f in report.findings}

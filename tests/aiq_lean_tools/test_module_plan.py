from aiq_lean_tools.module_plan import ModulePlanPolicy, check_module_plan


def _write(path, text=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_module_plan_topics_and_prerequisites(tmp_path):
    _write(tmp_path / "Proj" / "A.lean", "")
    _write(tmp_path / "Proj" / "B.lean", "import Proj.A\n")
    _write(tmp_path / "Proj" / "C.lean", "import Proj.B\n")
    policy = ModulePlanPolicy.from_mapping({
        "library": "Proj",
        "topics": [
            {"id": "base", "modules": ["A"]},
            {"id": "middle", "modules": ["B"]},
            {"id": "top", "modules": ["C"]},
        ],
    })
    report = check_module_plan(policy, root=tmp_path)
    assert report.ok
    assert report.module_count == 3
    assert [row.prerequisites for row in report.topics] == [(), ("base",), ("middle",)]
    assert report.unassigned == ()


def test_module_plan_detects_partition_and_order_failures(tmp_path):
    _write(tmp_path / "Proj" / "A.lean", "import Proj.B\n")
    _write(tmp_path / "Proj" / "B.lean", "")
    _write(tmp_path / "Proj" / "C.lean", "")
    policy = ModulePlanPolicy.from_mapping({
        "library": "Proj",
        "topics": [
            {"id": "first", "modules": ["A", "B"]},
            {"id": "second", "modules": ["B"]},
        ],
    })
    report = check_module_plan(policy, root=tmp_path)
    codes = {f.code for f in report.findings}
    assert "module-plan-duplicate" in codes
    assert "module-plan-unassigned" in codes
    assert not report.ok

    forward = ModulePlanPolicy.from_mapping({
        "library": "Proj",
        "require_total_topics": False,
        "topics": [
            {"id": "first", "modules": ["A"]},
            {"id": "second", "modules": ["B"]},
        ],
    })
    report = check_module_plan(forward, root=tmp_path)
    assert "module-plan-forward-ref" in {f.code for f in report.findings}


def test_module_plan_submission_rungs_are_dependency_closed(tmp_path):
    _write(tmp_path / "src" / "Proj" / "A.lean", "")
    _write(tmp_path / "src" / "Proj" / "B.lean", "import Proj.A\n")
    _write(tmp_path / "src" / "Proj" / "C.lean", "import Proj.B\n")
    _write(tmp_path / "src" / "Proj" / "Unused.lean", "")
    policy = ModulePlanPolicy.from_mapping({
        "library": "Proj",
        "source_dir": "src",
        "topics": [{"id": "all", "modules": ["A", "B", "C", "Unused"]}],
        "rungs": [
            {"id": "r1", "seeds": ["B"]},
            {"id": "r2", "seeds": ["C"]},
        ],
    })
    report = check_module_plan(policy, root=tmp_path)
    assert report.ok
    assert report.rungs[0].new_modules == ("Proj.A", "Proj.B")
    assert report.rungs[0].closed_slice == 2
    assert report.rungs[1].new_modules == ("Proj.C",)
    assert report.rungs[1].closed_slice == 3
    assert report.off_ladder == ("Proj.Unused",)

    strict = ModulePlanPolicy.from_mapping({
        "library": "Proj",
        "source_dir": "src",
        "topics": [{"id": "all", "modules": ["A", "B", "C", "Unused"]}],
        "rungs": [{"id": "r1", "seeds": ["C"]}],
        "require_total_ladder": True,
    })
    report = check_module_plan(strict, root=tmp_path)
    assert "module-plan-off-ladder" in {f.code for f in report.findings}

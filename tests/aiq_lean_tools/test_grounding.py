from pathlib import Path

from aiq_lean_tools.grounding import GroundingPolicy, check_grounding_policy, load_grounding_policy


def test_grounding_policy(tmp_path: Path):
    (tmp_path / "lakefile.toml").write_text('name = "x"\n')
    lib = tmp_path / "MyLib"
    lib.mkdir()
    (lib / "Good.lean").write_text(
        "namespace MyLib\n/-- documented -/\ntheorem kept : True := by trivial\nend MyLib\n"
    )
    (lib / "Bad.lean").write_text("axiom bad : True\ntheorem unfinished : True := by sorry\n")
    policy = GroundingPolicy.from_mapping({
        "required_files": ["MyLib/Good.lean"],
        "required_declarations": {"MyLib/Good.lean": ["MyLib.kept"]},
        "forbid": {"include": ["MyLib/**/*.lean", "MyLib/*.lean"], "admissions": True, "axioms": True},
    })
    findings = check_grounding_policy(policy, root=tmp_path)
    codes = {f.code for f in findings}
    assert "grounding-admission" in codes
    assert "grounding-axiom" in codes
    assert "grounding-declaration" not in codes


def test_load_grounding_policy(tmp_path: Path):
    p = tmp_path / "grounding.yaml"
    p.write_text("required_files:\n  - MyLib/Foo.lean\nforbid:\n  admissions: true\n")
    policy = load_grounding_policy(p)
    assert policy.required_files == ("MyLib/Foo.lean",)
    assert policy.forbid_admissions is True

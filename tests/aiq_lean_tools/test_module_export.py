from aiq_lean_tools.module_export import ModuleExportPolicy, export_modules, rewrite_imports


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def policy():
    return ModuleExportPolicy.from_mapping({
        "source_prefix": "Stage",
        "target_prefix": "Upstream",
        "allowed_import_prefixes": ["Mathlib", "Upstream"],
        "clusters": [{"name": "core", "source_modules": ["Stage.A", "Stage.B"]}],
        "records": [{"source_module": "Stage.B", "target_module": "Upstream.RenamedB"}],
    })


def test_rewrite_imports_is_narrow():
    text = "public import Stage.A\nimport Mathlib.Data.Nat.Basic\nnamespace Stage\n"
    new, forbidden = rewrite_imports(text, policy())
    assert new == "public import Upstream.A\nimport Mathlib.Data.Nat.Basic\nnamespace Stage\n"
    assert not forbidden


def test_export_check_and_write(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "Stage/A.lean", "import Mathlib\ntheorem a : True := by trivial\n")
    _write(src / "Stage/B.lean", "import Stage.A\nlemma b : True := by trivial\n")
    report = export_modules(policy(), source_root=src, target_root=dst, cluster="core")
    assert report.ok
    assert [x.status for x in report.items] == ["NEW", "NEW"]
    report = export_modules(policy(), source_root=src, target_root=dst, cluster="core", write=True)
    assert report.ok
    assert (dst / "Upstream/A.lean").exists()
    assert (dst / "Upstream/RenamedB.lean").read_text().startswith("import Upstream.A")
    report = export_modules(policy(), source_root=src, target_root=dst, cluster="core")
    assert [x.status for x in report.items] == ["MATCH", "MATCH"]


def test_export_rejects_forbidden_import(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "Stage/A.lean", "import Private.Other\n")
    _write(src / "Stage/B.lean", "import Stage.A\n")
    report = export_modules(policy(), source_root=src, target_root=dst, cluster="core")
    assert not report.ok
    assert "export-forbidden-import" in {f.code for f in report.findings}


def test_legacy_record_prefers_staging_module_over_original_source_module():
    cfg = ModuleExportPolicy.from_mapping({
        "clusters": [{"cluster": "core", "staging_modules": ["ForTauCeti.A"]}],
        "records": [{
            "source_module": "ForMathlib.OriginalA",
            "staging_module": "ForTauCeti.A",
            "final_tauceti_module": "TauCeti.RenamedA",
        }],
    })
    assert cfg.target_for("ForTauCeti.A") == "TauCeti.RenamedA"

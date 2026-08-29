from aiq_lean_tools.foundations import FoundationMap, check_foundation_map
from aiq_lean_tools.lean_backend import MockLeanBackend


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def example_map():
    return FoundationMap.from_mapping({
        "title": "Demo foundations",
        "kind_meanings": {"proof": "proof candidate", "interface": "missing contract"},
        "nodes": [
            {"id": "base", "module": "Demo.Base", "file": "Demo/Base.lean", "declaration": "Demo.base", "kind": "proof"},
            {"id": "top", "module": "Demo.Top", "file": "Demo/Top.lean", "declaration": "Demo.top", "kind": "interface", "dependencies": ["base"]},
        ],
        "campaigns": [{"id": "main", "title": "Main", "nodes": ["base", "top"]}],
        "escape_scan": {"include": ["Demo/**/*.lean"]},
    })


def test_foundation_map_static_and_mock_probe(tmp_path):
    (tmp_path / "lakefile.toml").write_text("")
    _write(tmp_path / "Demo/Base.lean", "namespace Demo\ntheorem base : True := by trivial\nend Demo\n")
    _write(tmp_path / "Demo/Top.lean", "import Demo.Base\nnamespace Demo\ntheorem top : True := by trivial\nend Demo\n")
    backend = MockLeanBackend({"Demo.base": True, "Demo.top": True})
    report = check_foundation_map(example_map(), root=tmp_path, lean_probe=True, backend=backend)
    assert report.ok
    assert report.summary()["source_present"] == 2
    assert "Main" in report.render_markdown()
    assert "Foundation campaign map" in report.render_html()


def test_foundation_map_detects_graph_and_source_failures(tmp_path):
    (tmp_path / "lakefile.toml").write_text("")
    _write(tmp_path / "Demo/Base.lean", "namespace Demo\ntheorem other : True := by trivial\nend Demo\n")
    _write(tmp_path / "Demo/Top.lean", "theorem top : True := by\n  sorry\n")
    fmap = FoundationMap.from_mapping({
        "nodes": [
            {"id": "a", "module": "Demo.Base", "file": "Demo/Base.lean", "declaration": "Demo.base", "kind": "proof", "dependencies": ["b"]},
            {"id": "b", "module": "Demo.Top", "file": "Demo/Top.lean", "declaration": "top", "kind": "proof", "dependencies": ["a", "missing"]},
        ],
        "escape_scan": {"include": ["Demo/**/*.lean"]},
    })
    report = check_foundation_map(fmap, root=tmp_path)
    codes = {f.code for f in report.findings}
    assert "foundation-cycle" in codes
    assert "foundation-missing-dependency" in codes
    assert "foundation-declaration" in codes
    assert "foundation-escape" in codes

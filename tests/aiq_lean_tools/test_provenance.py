from pathlib import Path

from aiq_lean_tools.provenance import provenance_blocks, provenance_inventory


def test_provenance_inventory(tmp_path: Path):
    (tmp_path / "lakefile.toml").write_text('name = "x"\n')
    p = tmp_path / "MyLib.lean"
    p.write_text('''/-!\n# Module\n\n## Provenance\nAdapted from an external donor proof, with API rewriting.\n## Notes\nOther text.\n-/\ntheorem x : True := by trivial\n''')
    rows = provenance_blocks(p)
    assert len(rows) == 1
    assert "external donor proof" in rows[0].text
    data = provenance_inventory(tmp_path, markers={"adaptation": r"adapted|ported", "external": r"external"})
    assert data["block_count"] == 1
    assert data["marker_counts"] == {"adaptation": 1, "external": 1}
    assert data["blocks"][0]["markers"]["adaptation"] is True

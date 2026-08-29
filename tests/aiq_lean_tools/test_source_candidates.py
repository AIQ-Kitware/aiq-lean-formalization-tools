from __future__ import annotations

from aiq_lean_tools.source_candidates import (
    dead_definition_candidates,
    definitional_escape_candidates,
    duplicate_candidates,
    largest_declarations,
    name_candidates,
)


def _write_tree(root):
    (root / "A.lean").write_text(
        """namespace Demo

def deadThing : Nat := 3

def usedThing : Nat := 4

def oldLegacyHelper : Nat := usedThing

def escapedTerm : Nat := sorry

theorem alpha (x y : Nat) (h : x = y) (extra : x + 0 = x) : x = y := by
  exact h

end Demo
"""
    )
    (root / "B.lean").write_text(
        """namespace Demo2

theorem beta (x y : Nat) (h : x = y) (extra : x + 0 = x) : x = y := by
  exact h

end Demo2
"""
    )


def test_candidate_audits(tmp_path):
    _write_tree(tmp_path)
    dup = duplicate_candidates(tmp_path, minimum_chars=20, tracked_only=False)
    assert len(dup) == 1
    assert {row.name for row in dup[0].declarations} == {"alpha", "beta"}

    dead = dead_definition_candidates(tmp_path, tracked_only=False)
    assert any(row.short_name == "deadThing" for row in dead)
    assert not any(row.short_name == "usedThing" for row in dead)

    names = name_candidates(tmp_path, tracked_only=False)
    assert [row.declaration.name for row in names] == ["oldLegacyHelper"]

    escapes = definitional_escape_candidates(tmp_path, tracked_only=False)
    assert [row.declaration.name for row in escapes] == ["escapedTerm"]

    rows = largest_declarations(tmp_path, tracked_only=False)
    assert {row.name for row in rows} >= {"alpha", "beta", "escapedTerm"}


def test_forwarding_alias_can_be_suppressed(tmp_path):
    (tmp_path / "A.lean").write_text(
        """theorem alpha (x y z : Nat) (h : x = y) (h2 : y = z) : x = z := by
  omega
"""
    )
    (tmp_path / "B.lean").write_text(
        """theorem beta (x y z : Nat) (h : x = y) (h2 : y = z) : x = z := by
  exact alpha x y z h h2
"""
    )
    assert duplicate_candidates(tmp_path, minimum_chars=20, tracked_only=False) == []
    groups = duplicate_candidates(tmp_path, minimum_chars=20, include_forwarders=True, tracked_only=False)
    assert len(groups) == 1
    assert groups[0].forwarding_alias


def test_review_profile_combines_signals(tmp_path):
    from aiq_lean_tools.source_candidates import review_profiles

    _write_tree(tmp_path)
    rows = review_profiles(tmp_path, minimum_duplicate_chars=20, tracked_only=False)
    by_name = {row.path.name: row for row in rows}
    assert "alpha" in by_name["A.lean"].duplicate_candidates
    assert "deadThing" in by_name["A.lean"].dead_candidates
    assert "oldLegacyHelper" in by_name["A.lean"].name_candidates
    assert "escapedTerm" in by_name["A.lean"].definitional_escapes

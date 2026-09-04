"""Choosing which modules to import for a statement sidecar."""

from __future__ import annotations

import types

from aiq_lean_tools.statement_pins import _prefer_ordinary


class _Project:
    """Just the two questions `_prefer_ordinary` asks of a Lean project."""

    def __init__(self, owners: dict[str, str]) -> None:
        self._owners = owners

    def library_for_module(self, module: str) -> str | None:
        return self._owners.get(module)


def test_a_mirror_library_never_wins_a_name_contest():
    # A conformance library restates library statements under the same names.
    # Importing both aborts Lean with "environment already contains", which took
    # a pin gate out of service for as long as that library happened to be built.
    project = _Project({"DavisKahan.Sources.Thm": "DavisKahan",
                        "Challenge.Conformance": "Challenge"})
    chosen = _prefer_ordinary(project, ["DavisKahan.Sources.Thm", "Challenge.Conformance"],
                              {"DavisKahan"})
    assert chosen == ["DavisKahan.Sources.Thm"]


def test_a_seed_that_lives_only_outside_the_build_surface_still_places():
    project = _Project({"Challenge.Conformance": "Challenge"})
    assert _prefer_ordinary(project, ["Challenge.Conformance"], {"DavisKahan"}) == [
        "Challenge.Conformance"
    ]


def test_ambiguous_short_names_in_ordinary_libraries_are_all_kept():
    # Two different declarations sharing a short name are not a restatement, and
    # the elaborator resolves the full name; keeping both is the point.
    project = _Project({"A.One": "A", "B.Two": "B"})
    assert _prefer_ordinary(project, ["A.One", "B.Two"], {"A", "B"}) == ["A.One", "B.Two"]


def test_no_readable_default_targets_keeps_every_candidate():
    project = _Project({"A.One": "A", "Challenge.Conformance": "Challenge"})
    assert _prefer_ordinary(project, ["A.One", "Challenge.Conformance"], set()) == [
        "A.One", "Challenge.Conformance"
    ]

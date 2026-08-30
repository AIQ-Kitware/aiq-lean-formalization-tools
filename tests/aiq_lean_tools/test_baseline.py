from __future__ import annotations

import json

import pytest

from aiq_lean_tools.baseline import Baseline
from aiq_lean_tools.errors import FormalizationToolsError


def test_missing_path_is_an_empty_baseline(tmp_path):
    baseline = Baseline.load(tmp_path / "absent.json")
    assert baseline.keys == frozenset()


def test_partition_separates_new_and_stale(tmp_path):
    path = tmp_path / "accepted.json"
    path.write_text(json.dumps({"accepted": {"a": "reviewed wrapper", "gone": "retired"}}))
    partition = Baseline.load(path).partition(["a", "b"])
    assert partition.accepted == ("a",)
    assert partition.new == ("b",)
    assert partition.stale == ("gone",)
    assert not partition.ok


def test_plain_list_form_is_accepted(tmp_path):
    path = tmp_path / "accepted.json"
    path.write_text(json.dumps(["a", "b"]))
    assert Baseline.load(path).partition(["a", "b"]).ok


def test_yaml_baseline_keeps_reasons(tmp_path):
    path = tmp_path / "accepted.yaml"
    path.write_text("accepted:\n  a: why this one is fine\n")
    assert Baseline.load(path).reasons["a"] == "why this one is fine"


def test_write_preserves_existing_reasons(tmp_path):
    path = tmp_path / "accepted.json"
    path.write_text(json.dumps({"accepted": {"a": "reviewed wrapper"}}))
    baseline = Baseline.load(path)
    baseline.write(path, ["a", "b"])
    written = json.loads(path.read_text())["accepted"]
    assert written == {"a": "reviewed wrapper", "b": ""}


def test_malformed_baseline_is_rejected(tmp_path):
    path = tmp_path / "accepted.json"
    path.write_text(json.dumps({"accepted": [1, 2]}))
    with pytest.raises(FormalizationToolsError):
        Baseline.load(path)

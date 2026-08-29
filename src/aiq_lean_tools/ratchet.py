"""Configurable regex-count ratchets for formalization source policy."""
from __future__ import annotations

import fnmatch
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import yaml

from .common import Path
from .errors import FormalizationToolsError
from .lean_source import strip_comments


@dataclass(frozen=True)
class RatchetRule:
    id: str
    pattern: str
    maximum: int
    paths: tuple[str, ...] = ("**/*.lean",)
    exclude: tuple[str, ...] = ()
    count: str = "matches"
    strip_comments_first: bool = False
    description: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], index: int) -> "RatchetRule":
        try:
            rule_id = str(data["id"])
            pattern = str(data["pattern"])
            maximum = int(data["maximum"])
        except (KeyError, TypeError, ValueError) as ex:
            raise FormalizationToolsError(f"invalid ratchet rule #{index}: {ex}") from ex
        paths = data.get("paths", ["**/*.lean"])
        exclude = data.get("exclude", [])
        if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
            raise FormalizationToolsError(f"ratchet rule {rule_id!r}: paths must be a list of globs")
        if not isinstance(exclude, list) or not all(isinstance(x, str) for x in exclude):
            raise FormalizationToolsError(f"ratchet rule {rule_id!r}: exclude must be a list of globs")
        count = str(data.get("count", "matches"))
        if count not in {"matches", "files"}:
            raise FormalizationToolsError(f"ratchet rule {rule_id!r}: count must be 'matches' or 'files'")
        if maximum < 0:
            raise FormalizationToolsError(f"ratchet rule {rule_id!r}: maximum cannot be negative")
        return cls(
            id=rule_id,
            pattern=pattern,
            maximum=maximum,
            paths=tuple(paths),
            exclude=tuple(exclude),
            count=count,
            strip_comments_first=bool(data.get("strip_comments", False)),
            description=str(data.get("description", "")),
        )


@dataclass(frozen=True)
class RatchetResult:
    rule: RatchetRule
    value: int
    files_scanned: int
    matching_files: tuple[str, ...]

    @property
    def status(self) -> str:
        if self.value > self.rule.maximum:
            return "above"
        if self.value < self.rule.maximum:
            return "below"
        return "at"

    @property
    def ok(self) -> bool:
        return self.value <= self.rule.maximum

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.rule.id,
            "description": self.rule.description,
            "count": self.rule.count,
            "value": self.value,
            "maximum": self.rule.maximum,
            "status": self.status,
            "files_scanned": self.files_scanned,
            "matching_files": list(self.matching_files),
        }


def load_ratchet_policy(path: str | pathlib.Path) -> list[RatchetRule]:
    source = Path(path).expanduser()
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(data, Mapping):
        raise FormalizationToolsError(f"ratchet policy must be a mapping: {source}")
    rules = data.get("rules", [])
    if not isinstance(rules, list) or not rules:
        raise FormalizationToolsError("ratchet policy must contain a nonempty rules list")
    parsed = [RatchetRule.from_mapping(row, idx) for idx, row in enumerate(rules) if isinstance(row, Mapping)]
    if len(parsed) != len(rules):
        raise FormalizationToolsError("every ratchet rule must be an object")
    ids = [rule.id for rule in parsed]
    if len(ids) != len(set(ids)):
        raise FormalizationToolsError("ratchet rule ids must be unique")
    return parsed


def _matching_paths(root: Path, rule: RatchetRule) -> list[Path]:
    candidates: set[Path] = set()
    for pattern in rule.paths:
        candidates.update(path for path in root.glob(pattern) if path.is_file())
    out: list[Path] = []
    for path in sorted(candidates):
        rel = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(rel, pat) for pat in rule.exclude):
            continue
        out.append(path)
    return out


def evaluate_ratchets(root: str | pathlib.Path, rules: Sequence[RatchetRule]) -> list[RatchetResult]:
    base = Path(root).expanduser().resolve()
    results: list[RatchetResult] = []
    for rule in rules:
        try:
            regex = re.compile(rule.pattern, re.MULTILINE)
        except re.error as ex:
            raise FormalizationToolsError(f"ratchet rule {rule.id!r} has invalid regex: {ex}") from ex
        paths = _matching_paths(base, rule)
        total = 0
        matching: list[str] = []
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            if rule.strip_comments_first:
                text = strip_comments(text)
            hits = list(regex.finditer(text))
            if not hits:
                continue
            matching.append(path.relative_to(base).as_posix())
            total += 1 if rule.count == "files" else len(hits)
        results.append(RatchetResult(rule, total, len(paths), tuple(matching)))
    return results

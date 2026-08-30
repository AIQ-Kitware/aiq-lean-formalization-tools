"""Accepted-finding baselines for structural source checks.

Several structural checks are useful long before a repository can get their
count to zero, and a few of their findings are legitimate forever.  A count
threshold is the wrong shape for that: it accepts *any* finding once the number
is high enough, so retiring one accepted case silently makes room for a new
defect.  A baseline of named findings does not, and it makes accepting one a
deliberate edit that records the reason.

A stale entry -- one naming a finding that no longer occurs -- is reported as a
failure for the same reason: left in place it silently pre-accepts whatever next
takes that name.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .common import Path
from .errors import FormalizationToolsError


@dataclass(frozen=True)
class BaselinePartition:
    accepted: tuple[str, ...]
    new: tuple[str, ...]
    stale: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.new and not self.stale

    def to_json(self) -> dict[str, Any]:
        return {
            "accepted": list(self.accepted),
            "new": list(self.new),
            "stale": list(self.stale),
        }


@dataclass(frozen=True)
class Baseline:
    """Findings a project has reviewed and accepted, each with a reason."""

    path: Path | None
    reasons: Mapping[str, str]

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(self.reasons)

    @classmethod
    def empty(cls) -> "Baseline":
        return cls(None, {})

    @classmethod
    def from_data(cls, data: Any, path: Path | None = None) -> "Baseline":
        if data is None:
            return cls(path, {})
        if isinstance(data, Mapping):
            accepted = data.get("accepted", data)
        else:
            accepted = data
        if isinstance(accepted, Mapping):
            return cls(path, {str(k): str(v) for k, v in accepted.items()})
        if isinstance(accepted, list) and all(isinstance(x, str) for x in accepted):
            return cls(path, {x: "" for x in accepted})
        raise FormalizationToolsError(
            "a baseline must be a list of finding keys or a mapping from key to reason"
        )

    @classmethod
    def load(cls, path: str | pathlib.Path | None) -> "Baseline":
        if path is None:
            return cls.empty()
        source = Path(path).expanduser()
        if not source.is_file():
            return cls(source, {})
        text = source.read_text(encoding="utf-8")
        data = json.loads(text) if source.suffix.lower() == ".json" else yaml.safe_load(text)
        return cls.from_data(data, source)

    def partition(self, found: Iterable[str]) -> BaselinePartition:
        found_set = set(found)
        return BaselinePartition(
            accepted=tuple(sorted(found_set & self.keys)),
            new=tuple(sorted(found_set - self.keys)),
            stale=tuple(sorted(self.keys - found_set)),
        )

    def write(self, path: str | pathlib.Path, found: Sequence[str]) -> Path:
        """Record ``found`` as accepted, preserving the reason for existing keys."""
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        accepted = {key: self.reasons.get(key, "") for key in sorted(set(found))}
        payload = json.dumps({"accepted": accepted}, indent=2, ensure_ascii=False) + "\n"
        target.write_text(payload, encoding="utf-8")
        return target

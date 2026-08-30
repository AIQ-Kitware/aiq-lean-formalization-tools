"""Structural comparison of suggested Lean APIs against delivered declarations.

A name match is a migration/planning signal only.  It does not establish that a
donor declaration has the same type or semantics as the roadmap signature.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Iterable, Sequence

from .common import Path
from .lean_source import LeanSourceIndex, SourceDecl, scan_lean_project


@dataclass(frozen=True)
class RoadmapTopic:
    topic: str
    total: int
    delivered: int
    missing: tuple[str, ...]
    mapping: dict[str, str]
    ambiguous: dict[str, tuple[str, ...]]

    def to_json(self) -> dict:
        return {
            "topic": self.topic,
            "total": self.total,
            "delivered": self.delivered,
            "missing": list(self.missing),
            "map": dict(self.mapping),
            "ambiguous": {key: list(value) for key, value in self.ambiguous.items()},
        }


@dataclass
class RoadmapDeliveryReport:
    roadmap_root: Path
    project_root: Path
    topics: list[RoadmapTopic]

    @property
    def total(self) -> int:
        return sum(topic.total for topic in self.topics)

    @property
    def delivered(self) -> int:
        return sum(topic.delivered for topic in self.topics)

    def to_json(self) -> dict:
        return {
            "roadmap_root": str(self.roadmap_root),
            "project_root": str(self.project_root),
            "total": self.total,
            "delivered": self.delivered,
            "topics": [topic.to_json() for topic in self.topics],
            "semantic_warning": "Declaration-name matches do not establish statement equivalence or roadmap completion.",
        }


def _matches_prefix(decl: SourceDecl, root: Path, prefixes: Sequence[str]) -> bool:
    if not prefixes:
        return True
    rel = decl.path.relative_to(root).as_posix()
    return any(
        rel == prefix.rstrip("/")
        or rel.startswith(prefix.rstrip("/") + "/")
        or decl.module == prefix.rstrip(".")
        or decl.module.startswith(prefix.rstrip(".") + ".")
        for prefix in prefixes
    )


def _rank_decl(decl: SourceDecl, root: Path, preferred: Sequence[str]) -> tuple[int, int, str]:
    rel = decl.path.relative_to(root).as_posix()
    pref_rank = len(preferred)
    for index, prefix in enumerate(preferred):
        clean_path = prefix.rstrip("/")
        clean_module = prefix.rstrip(".")
        if rel == clean_path or rel.startswith(clean_path + "/") or decl.module == clean_module or decl.module.startswith(clean_module + "."):
            pref_rank = index
            break
    return pref_rank, len(decl.path.relative_to(root).parts), rel


def _topic_declarations(index: LeanSourceIndex, roadmap_root: Path, suggested_glob: str) -> dict[str, list[SourceDecl]]:
    selected = set(path.resolve() for path in roadmap_root.glob(suggested_glob) if path.is_file())
    out: dict[str, list[SourceDecl]] = {}
    for decl in index.named_declarations:
        if decl.path.resolve() not in selected:
            continue
        topic = decl.path.parent.relative_to(roadmap_root).as_posix() or "."
        out.setdefault(topic, []).append(decl)
    return out


def compare_roadmap(
    roadmap_root: str | pathlib.Path,
    project_root: str | pathlib.Path,
    *,
    suggested_glob: str = "**/Suggested.lean",
    libraries: Sequence[str] = (),
    preferred_prefixes: Sequence[str] = (),
) -> RoadmapDeliveryReport:
    roadmap = Path(roadmap_root).expanduser().resolve()
    project = Path(project_root).expanduser().resolve()
    roadmap_index = scan_lean_project(roadmap, exclude_dirs=(".git", ".lake", "build"))
    project_index = scan_lean_project(project)
    by_topic = _topic_declarations(roadmap_index, roadmap, suggested_glob)

    topics: list[RoadmapTopic] = []
    for topic, declarations in sorted(by_topic.items()):
        seen: set[str] = set()
        missing: list[str] = []
        mapping: dict[str, str] = {}
        ambiguous: dict[str, tuple[str, ...]] = {}
        for suggested in declarations:
            name = suggested.name
            if name in seen:
                continue
            seen.add(name)
            hits = project_index.by_name.get(name, [])
            if not hits:
                hits = project_index.by_short_name.get(suggested.short_name, [])
            hits = [row for row in hits if _matches_prefix(row, project, libraries)]
            if not hits:
                missing.append(name)
                continue
            ranked = sorted(hits, key=lambda row: _rank_decl(row, project, preferred_prefixes))
            chosen = ranked[0]
            mapping[name] = chosen.path.relative_to(project).as_posix()
            if len(ranked) > 1:
                ambiguous[name] = tuple(row.path.relative_to(project).as_posix() for row in ranked)
        topics.append(RoadmapTopic(
            topic=topic,
            total=len(seen),
            delivered=len(mapping),
            missing=tuple(missing),
            mapping=mapping,
            ambiguous=ambiguous,
        ))
    return RoadmapDeliveryReport(roadmap, project, topics)

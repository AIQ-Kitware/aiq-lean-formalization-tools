"""Curated presentation views over exact Lean declaration dependency graphs.

A presentation spec names declarations exactly.  It never infers mathematical
roles from substrings: semantic reachability comes from the elaborated graph,
while labels and headline selection remain an explicit editorial choice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .graph import DependencyGraph, projected_reduction, resolve_decl_name
from .project import ProjectError


@dataclass(frozen=True)
class HeadlineSpec:
    """One declaration selected for the compact presentation graph."""

    name: str
    label: str | None = None
    subtitle: str | None = None
    group: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class PresentationSpec:
    """Human-owned presentation metadata layered over compiler facts."""

    title: str | None
    subtitle: str | None
    targets: tuple[str, ...]
    headlines: tuple[HeadlineSpec, ...]
    include_targets: bool = True

    @classmethod
    def from_json(cls, obj: Mapping) -> "PresentationSpec":
        version = obj.get("schemaVersion", 1)
        if version != 1:
            raise ProjectError(
                f"unsupported presentation schemaVersion {version!r}; expected 1"
            )
        raw_targets = obj.get("targets", [])
        if not isinstance(raw_targets, list) or not all(
            isinstance(item, str) and item for item in raw_targets
        ):
            raise ProjectError("presentation targets must be a list of declaration names")
        raw_headlines = obj.get("headlines", ())
        if not isinstance(raw_headlines, list):
            raise ProjectError("presentation headlines must be a list")
        headlines = []
        for index, row in enumerate(raw_headlines):
            if isinstance(row, str):
                headlines.append(HeadlineSpec(name=row))
                continue
            if not isinstance(row, Mapping):
                raise ProjectError(
                    f"presentation headline {index} must be a name or object"
                )
            name = row.get("name")
            if not isinstance(name, str) or not name:
                raise ProjectError(
                    f"presentation headline {index} needs a non-empty name"
                )
            values = {}
            for key in ("label", "subtitle", "group", "description"):
                value = row.get(key)
                if value is not None and not isinstance(value, str):
                    raise ProjectError(
                        f"presentation headline {index} field {key!r} must be a string"
                    )
                values[key] = value
            headlines.append(HeadlineSpec(name=name, **values))
        title = obj.get("title")
        subtitle = obj.get("subtitle")
        if title is not None and not isinstance(title, str):
            raise ProjectError("presentation title must be a string")
        if subtitle is not None and not isinstance(subtitle, str):
            raise ProjectError("presentation subtitle must be a string")
        include_targets = obj.get("includeTargets", True)
        if not isinstance(include_targets, bool):
            raise ProjectError("presentation includeTargets must be a boolean")
        return cls(
            title=title,
            subtitle=subtitle,
            targets=tuple(raw_targets),
            headlines=tuple(headlines),
            include_targets=include_targets,
        )


def load_presentation(path: Path) -> PresentationSpec:
    """Load a JSON presentation specification."""
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectError(f"no presentation spec at {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProjectError(f"invalid presentation JSON in {path}: {exc}") from exc
    if not isinstance(obj, Mapping):
        raise ProjectError("presentation JSON must contain an object")
    return PresentationSpec.from_json(obj)


def _default_label(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def build_presentation(
    graph: DependencyGraph,
    spec: PresentationSpec | None,
    *,
    extra_headlines: Iterable[str] = (),
    title: str | None = None,
    subtitle: str | None = None,
    strict: bool = False,
) -> dict:
    """Resolve a presentation spec against one exact target dependency graph.

    Missing curated headlines are recorded and omitted by default so stale editorial
    metadata cannot prevent the exact graph or viewer from being written.  Pass
    ``strict=True`` when a presentation spec is itself an audited artifact and any
    missing headline should fail the command.
    """
    rows = list(graph.nodes.values())
    ordered: list[HeadlineSpec] = list(spec.headlines if spec else ())
    ordered.extend(HeadlineSpec(name=name) for name in extra_headlines)
    if spec is None or spec.include_targets:
        ordered.extend(HeadlineSpec(name=name) for name in graph.targets)

    resolved_rows: list[tuple[str, HeadlineSpec]] = []
    missing_headlines: list[str] = []
    seen: set[str] = set()
    for headline in ordered:
        try:
            resolved = resolve_decl_name(rows, headline.name)
        except ProjectError as exc:
            if strict:
                raise ProjectError(
                    f"presentation headline {headline.name!r} is not in the target dependency closure"
                ) from exc
            if headline.name not in missing_headlines:
                missing_headlines.append(headline.name)
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        resolved_rows.append((resolved, headline))

    keep = [name for name, _ in resolved_rows]
    projected = projected_reduction(graph, keep)
    nodes = []
    for name, headline in resolved_rows:
        decl = graph.nodes[name]
        nodes.append(
            {
                "id": name,
                "label": headline.label or _default_label(name),
                "subtitle": headline.subtitle,
                "group": headline.group or decl.library or decl.module.split(".", 1)[0],
                "description": headline.description,
                "target": name in graph.targets,
            }
        )
    edges = []
    for edge in projected:
        witness = edge["witness"]
        edges.append(
            {
                **edge,
                "direct": len(witness) == 2,
                "collapsedNodeCount": max(0, len(witness) - 2),
            }
        )

    resolved_title = title or (spec.title if spec else None)
    if resolved_title is None:
        target_labels = ", ".join(_default_label(name) for name in graph.targets)
        resolved_title = f"Proof dependencies: {target_labels}"
    resolved_subtitle = subtitle or (spec.subtitle if spec else None)
    return {
        "schemaVersion": 1,
        "title": resolved_title,
        "subtitle": resolved_subtitle,
        "nodes": nodes,
        "edges": edges,
        "missingHeadlineCount": len(missing_headlines),
        "missingHeadlines": missing_headlines,
    }

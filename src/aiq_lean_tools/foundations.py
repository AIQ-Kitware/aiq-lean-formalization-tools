"""Track recursive foundational obligations behind a formalization campaign.

A foundation map is deliberately separate from an elaborated dependency graph.
It records *human project state*: proof candidates, constructions, bridges, and
explicit interfaces for mathematics that is still missing.  Dependency edges
are stable node identifiers chosen by the project.  Static source checks and an
optional Lean probe keep those annotations connected to real declarations.
"""
from __future__ import annotations

import html
import importlib.resources as resources
import json
import pathlib
import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import yaml

from .common import Finding, Path, find_workspace_root
from .errors import ValidationError
from .lean_backend import LeanBackend, SubprocessLeanBackend
from .lean_source import LeanSourceIndex, scan_lean_project, strip_comments

DEFAULT_ESCAPE_PATTERNS = {
    "sorry": r"\bsorry\b",
    "admit": r"\badmit\b",
    "native_decide": r"\bnative_decide\b",
}


def _strings(value: Any, name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValidationError(f"{name} must be a list of strings")
    out = tuple(str(x).strip() for x in value if isinstance(x, str) and x.strip())
    if len(out) != len(value) or (not allow_empty and not out):
        suffix = " and cannot be empty" if not allow_empty else ""
        raise ValidationError(f"{name} must contain only non-empty strings{suffix}")
    return out


@dataclass(frozen=True)
class FoundationNode:
    id: str
    title: str
    module: str
    file: str
    declaration: str
    kind: str
    dependencies: tuple[str, ...] = ()
    confidence: str = ""
    notes: str = ""
    frontier_nodes: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, index: int) -> "FoundationNode":
        required = ("id", "module", "file", "declaration", "kind")
        missing = [key for key in required if not isinstance(data.get(key), str) or not str(data.get(key)).strip()]
        if missing:
            raise ValidationError(f"foundation node {index} missing required fields: {', '.join(missing)}")
        return cls(
            id=str(data["id"]).strip(),
            title=str(data.get("title") or data["id"]).strip(),
            module=str(data["module"]).strip(),
            file=str(data["file"]).strip(),
            declaration=str(data["declaration"]).strip(),
            kind=str(data["kind"]).strip(),
            dependencies=_strings(data.get("dependencies", []), f"nodes[{index}].dependencies"),
            confidence=str(data.get("confidence", "")).strip(),
            notes=str(data.get("notes", "")).strip(),
            frontier_nodes=_strings(data.get("frontier_nodes", []), f"nodes[{index}].frontier_nodes"),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "module": self.module,
            "file": self.file,
            "declaration": self.declaration,
            "kind": self.kind,
            "dependencies": list(self.dependencies),
            "confidence": self.confidence,
            "notes": self.notes,
            "frontier_nodes": list(self.frontier_nodes),
        }


@dataclass(frozen=True)
class FoundationCampaign:
    id: str
    title: str
    nodes: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, index: int) -> "FoundationCampaign":
        ident = data.get("id")
        if not isinstance(ident, str) or not ident.strip():
            raise ValidationError(f"campaign {index} requires id")
        return cls(
            id=ident.strip(),
            title=str(data.get("title") or ident).strip(),
            nodes=_strings(data.get("nodes", []), f"campaigns[{index}].nodes"),
        )

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "nodes": list(self.nodes)}


@dataclass(frozen=True)
class FoundationMap:
    path: Path | None
    title: str
    description: str
    nodes: tuple[FoundationNode, ...]
    campaigns: tuple[FoundationCampaign, ...]
    kind_meanings: Mapping[str, str]
    escape_include: tuple[str, ...]
    escape_patterns: Mapping[str, str]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, path: Path | None = None) -> "FoundationMap":
        raw_nodes = data.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise ValidationError("foundation map requires a non-empty nodes list")
        nodes = []
        for index, row in enumerate(raw_nodes):
            if not isinstance(row, Mapping):
                raise ValidationError(f"foundation node {index} must be a mapping")
            nodes.append(FoundationNode.from_mapping(row, index=index))
        raw_campaigns = data.get("campaigns", [])
        if not isinstance(raw_campaigns, list):
            raise ValidationError("campaigns must be a list")
        campaigns = []
        for index, row in enumerate(raw_campaigns):
            if not isinstance(row, Mapping):
                raise ValidationError(f"campaign {index} must be a mapping")
            campaigns.append(FoundationCampaign.from_mapping(row, index=index))
        meanings = data.get("kind_meanings", data.get("status_meanings", {}))
        if meanings is None:
            meanings = {}
        if not isinstance(meanings, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in meanings.items()):
            raise ValidationError("kind_meanings must be a mapping of strings to strings")
        scan = data.get("escape_scan", {}) or {}
        if not isinstance(scan, Mapping):
            raise ValidationError("escape_scan must be a mapping")
        include = _strings(scan.get("include", []), "escape_scan.include")
        raw_patterns = scan.get("patterns", DEFAULT_ESCAPE_PATTERNS)
        if raw_patterns is False:
            raw_patterns = {}
        if not isinstance(raw_patterns, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw_patterns.items()):
            raise ValidationError("escape_scan.patterns must map labels to regular expressions")
        # Compile once during validation of the document itself.
        for label, pattern in raw_patterns.items():
            try:
                re.compile(pattern)
            except re.error as ex:
                raise ValidationError(f"invalid escape pattern {label!r}: {ex}") from ex
        return cls(
            path=path,
            title=str(data.get("title") or data.get("description") or "Foundation map"),
            description=str(data.get("description", "")),
            nodes=tuple(nodes),
            campaigns=tuple(campaigns),
            kind_meanings=dict(meanings),
            escape_include=include,
            escape_patterns=dict(raw_patterns),
        )

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "FoundationMap":
        p = Path(path).expanduser().resolve()
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError as ex:
            raise ValidationError(f"foundation map does not exist: {p}") from ex
        try:
            data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
        except (json.JSONDecodeError, yaml.YAMLError) as ex:
            raise ValidationError(f"invalid foundation map {p}: {ex}") from ex
        if not isinstance(data, Mapping):
            raise ValidationError("foundation map root must be a mapping")
        return cls.from_mapping(data, path=p)


@dataclass(frozen=True)
class FoundationNodeStatus:
    node: FoundationNode
    source_present: bool
    lean_probe: bool | None

    def to_json(self) -> dict[str, Any]:
        row = self.node.to_json()
        row.update({"source_present": self.source_present, "lean_probe": self.lean_probe})
        return row


@dataclass(frozen=True)
class FoundationReport:
    foundation_map: FoundationMap
    node_status: tuple[FoundationNodeStatus, ...]
    findings: tuple[Finding, ...]
    cycles: tuple[tuple[str, ...], ...]

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def summary(self) -> dict[str, Any]:
        kinds = Counter(row.node.kind for row in self.node_status)
        return {
            "title": self.foundation_map.title,
            "nodes": len(self.node_status),
            "source_present": sum(row.source_present for row in self.node_status),
            "lean_probe_success": sum(row.lean_probe is True for row in self.node_status),
            "lean_probe_failure": sum(row.lean_probe is False for row in self.node_status),
            "lean_probe_unknown": sum(row.lean_probe is None for row in self.node_status),
            "campaigns": len(self.foundation_map.campaigns),
            "kinds": dict(sorted(kinds.items())),
            "errors": sum(f.level == "error" for f in self.findings),
            "warnings": sum(f.level == "warning" for f in self.findings),
            "cycles": len(self.cycles),
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "summary": self.summary(),
            "description": self.foundation_map.description,
            "kind_meanings": dict(self.foundation_map.kind_meanings),
            "nodes": [row.to_json() for row in self.node_status],
            "campaigns": [row.to_json() for row in self.foundation_map.campaigns],
            "cycles": [list(row) for row in self.cycles],
            "findings": [f.to_json() for f in self.findings],
            "semantic_warning": "Foundation edges are project-tracking relationships, not Lean elaborator dependency edges.",
        }

    def render_markdown(self) -> str:
        summary = self.summary()
        lines = [f"# {self.foundation_map.title}", ""]
        if self.foundation_map.description:
            lines += [self.foundation_map.description, ""]
        lines += [
            "## Summary", "",
            f"- Tracked nodes: **{summary['nodes']}**",
            f"- Textually present declarations: **{summary['source_present']}/{summary['nodes']}**",
            f"- Campaigns: **{summary['campaigns']}**",
            f"- Findings: **{summary['errors']} errors**, **{summary['warnings']} warnings**",
            "",
        ]
        if any(row.lean_probe is not None for row in self.node_status):
            lines += [
                f"- Lean probes: **{summary['lean_probe_success']} successful**, **{summary['lean_probe_failure']} failed**",
                "",
            ]
        if self.foundation_map.kind_meanings:
            lines += ["## Node kinds", ""]
            for key, meaning in self.foundation_map.kind_meanings.items():
                lines.append(f"- `{key}`: {meaning}")
            lines.append("")
        if self.foundation_map.campaigns:
            lines += ["## Campaigns", ""]
            by_id = {row.node.id: row.node for row in self.node_status}
            for campaign in self.foundation_map.campaigns:
                kinds = Counter(by_id[node].kind for node in campaign.nodes if node in by_id)
                kind_text = ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())) or "no nodes"
                lines += [f"### {campaign.title}", "", f"`{campaign.id}` · {kind_text}", ""]
        lines += [
            "## Nodes", "",
            "| Node | Kind | Confidence | Source | Lean | Declaration |",
            "| --- | --- | --- | :---: | :---: | --- |",
        ]
        for row in self.node_status:
            lean = "yes" if row.lean_probe is True else "no" if row.lean_probe is False else "-"
            lines.append(
                f"| `{row.node.id}` | {row.node.kind} | {row.node.confidence or '-'} | "
                f"{'yes' if row.source_present else 'no'} | {lean} | `{row.node.declaration}` |"
            )
        lines += ["", "## Dependency graph", ""]
        for row in self.node_status:
            deps = ", ".join(f"`{x}`" for x in row.node.dependencies) or "(root)"
            lines.append(f"- `{row.node.id}` <- {deps}")
        if self.findings:
            lines += ["", "## Findings", ""]
            for finding in self.findings:
                loc = f" `{finding.location}`" if finding.location else ""
                lines.append(f"- **{finding.level.upper()}** `{finding.code}`{loc}: {finding.message}")
        return "\n".join(lines).rstrip() + "\n"

    @property
    def title(self) -> str:
        return self.foundation_map.title

    def payload(self) -> dict:
        return self.to_json()

    def render_html(self) -> str:
        from .viewer import viewer_html

        return viewer_html("foundation_viewer.html", self.title, self.payload())


def _dependency_cycles(nodes: Sequence[FoundationNode]) -> tuple[set[str], tuple[tuple[str, ...], ...]]:
    ids = {node.id for node in nodes}
    missing = {dep for node in nodes for dep in node.dependencies if dep not in ids}
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = Counter({node.id: 0 for node in nodes})
    for node in nodes:
        for dep in node.dependencies:
            if dep in ids:
                outgoing[dep].append(node.id)
                indegree[node.id] += 1
    queue = deque(sorted(name for name, degree in indegree.items() if degree == 0))
    seen = 0
    while queue:
        name = queue.popleft()
        seen += 1
        for child in outgoing[name]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    cycles: tuple[tuple[str, ...], ...] = ()
    if seen != len(nodes):
        cycles = (tuple(sorted(name for name, degree in indegree.items() if degree > 0)),)
    return missing, cycles


def _source_present(node: FoundationNode, index: LeanSourceIndex, base: Path) -> bool:
    path = (base / node.file).resolve()
    candidates = index.by_name.get(node.declaration, [])
    if not candidates:
        candidates = index.by_short_name.get(node.declaration.rsplit(".", 1)[-1], [])
    return any(row.path.resolve() == path for row in candidates)


def _escape_findings(fmap: FoundationMap, base: Path) -> list[Finding]:
    if not fmap.escape_patterns:
        return []
    paths: set[Path] = set()
    if fmap.escape_include:
        for pattern in fmap.escape_include:
            paths.update(path for path in base.glob(pattern) if path.is_file() and path.suffix == ".lean")
    else:
        # With no explicit scan scope, inspect the tracked node files.  This
        # preserves the useful default of the historical hidden-foundations
        # gate without imposing a project-specific directory name.
        for node in fmap.nodes:
            path = base / node.file
            if path.is_file() and path.suffix == ".lean":
                paths.add(path)
    compiled = [(label, re.compile(pattern)) for label, pattern in fmap.escape_patterns.items()]
    findings: list[Finding] = []
    for path in sorted(paths):
        clean = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        for label, regex in compiled:
            for match in regex.finditer(clean):
                line = clean.count("\n", 0, match.start()) + 1
                findings.append(Finding(
                    "error", "foundation-escape", f"forbidden proof escape matched: {label}",
                    f"{path.relative_to(base).as_posix()}:{line}",
                ))
    return findings


def check_foundation_map(
    fmap: FoundationMap,
    *,
    root: str | pathlib.Path | None = None,
    index: LeanSourceIndex | None = None,
    lean_probe: bool = False,
    backend: LeanBackend | None = None,
    timeout: int = 600,
) -> FoundationReport:
    base = find_workspace_root(root or (fmap.path.parent if fmap.path else None))
    source_index = index or scan_lean_project(base)
    findings: list[Finding] = []

    ids = [node.id for node in fmap.nodes]
    duplicates = sorted(name for name, count in Counter(ids).items() if count > 1)
    for ident in duplicates:
        findings.append(Finding("error", "foundation-duplicate-id", f"duplicate foundation node id: {ident}", ident))

    missing_deps, cycles = _dependency_cycles(fmap.nodes)
    for ident in sorted(missing_deps):
        findings.append(Finding("error", "foundation-missing-dependency", f"dependency id is not defined: {ident}", ident))
    for cycle in cycles:
        findings.append(Finding("error", "foundation-cycle", "foundation dependency graph contains a cycle", ", ".join(cycle)))

    known_ids = set(ids)
    campaign_ids: set[str] = set()
    for campaign in fmap.campaigns:
        if campaign.id in campaign_ids:
            findings.append(Finding("error", "foundation-duplicate-campaign", f"duplicate campaign id: {campaign.id}", campaign.id))
        campaign_ids.add(campaign.id)
        for ident in campaign.nodes:
            if ident not in known_ids:
                findings.append(Finding("error", "foundation-campaign-node", f"campaign references unknown node: {ident}", campaign.id))

    source_presence = {node.id: _source_present(node, source_index, base) for node in fmap.nodes}
    for node in fmap.nodes:
        if not (base / node.file).is_file():
            findings.append(Finding("error", "foundation-file", "foundation source file is missing", node.file))
        elif not source_presence[node.id]:
            findings.append(Finding("error", "foundation-declaration", f"tracked declaration is not present in the recorded file: {node.declaration}", node.file))

    findings.extend(_escape_findings(fmap, base))

    probes: dict[str, bool | None] = {node.id: None for node in fmap.nodes}
    if lean_probe:
        engine = backend or SubprocessLeanBackend()
        by_module: dict[str, list[FoundationNode]] = defaultdict(list)
        for node in fmap.nodes:
            by_module[node.module].append(node)
        for module, group in sorted(by_module.items()):
            rows = engine.probe_declarations(
                base,
                [node.declaration for node in group],
                [module],
                timeout=timeout,
            )
            for node, probe in zip(group, rows):
                probes[node.id] = probe.resolved
                if not probe.resolved:
                    findings.append(Finding("error", "foundation-lean-probe", f"Lean did not resolve tracked declaration: {node.declaration}", node.id))

    status = tuple(
        FoundationNodeStatus(node, source_presence.get(node.id, False), probes.get(node.id))
        for node in fmap.nodes
    )
    return FoundationReport(fmap, status, tuple(findings), cycles)

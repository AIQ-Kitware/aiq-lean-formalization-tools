"""Ordered Lean module-topic plans and dependency-closed submission ladders.

This module extracts the source-level planning mechanics that grew around the
Tau Ceti staging library.  A project supplies the editorial decisions -- topic
membership and optional rung seeds -- while the package derives totality,
disjointness, import-order constraints, prerequisite topics, closure sizes, and
unplaced modules from the current Lean source tree.

The graph is source-import structure.  It does not claim declaration-level proof
dependence or successful elaboration.
"""
from __future__ import annotations

import json
import pathlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import yaml

from .common import Finding, Path, find_workspace_root
from .errors import ValidationError
from .import_graph import imports_of


def _strings(value: Any, name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if value is None:
        value = []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValidationError(f"{name} must be a list of strings")
    out = tuple(str(x).strip() for x in value if isinstance(x, str) and x.strip())
    if len(out) != len(value) or (not allow_empty and not out):
        raise ValidationError(f"{name} must contain only non-empty strings")
    return out


@dataclass(frozen=True)
class ModuleTopic:
    id: str
    title: str
    modules: tuple[str, ...]


@dataclass(frozen=True)
class SubmissionRung:
    id: str
    title: str
    seeds: tuple[str, ...]


@dataclass(frozen=True)
class ModulePlanPolicy:
    library: str
    source_dir: str
    topics: tuple[ModuleTopic, ...]
    rungs: tuple[SubmissionRung, ...] = ()
    require_total_topics: bool = True
    require_total_ladder: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ModulePlanPolicy":
        library = data.get("library", data.get("library_prefix"))
        if not isinstance(library, str) or not library.strip():
            raise ValidationError("module plan requires a non-empty library")
        source_dir = data.get("source_dir", ".")
        if not isinstance(source_dir, str) or not source_dir.strip():
            raise ValidationError("source_dir must be a non-empty relative path")
        raw_topics = data.get("topics")
        if not isinstance(raw_topics, list) or not raw_topics:
            raise ValidationError("module plan requires a non-empty topics list")
        topics: list[ModuleTopic] = []
        for index, row in enumerate(raw_topics):
            if not isinstance(row, Mapping):
                raise ValidationError(f"topics[{index}] must be a mapping")
            ident = row.get("id", row.get("key"))
            if not isinstance(ident, str) or not ident.strip():
                raise ValidationError(f"topics[{index}] requires id")
            topics.append(ModuleTopic(
                ident.strip(),
                str(row.get("title") or ident).strip(),
                _strings(row.get("modules"), f"topics[{index}].modules", allow_empty=False),
            ))
        raw_rungs = data.get("rungs", [])
        if not isinstance(raw_rungs, list):
            raise ValidationError("rungs must be a list")
        rungs: list[SubmissionRung] = []
        for index, row in enumerate(raw_rungs):
            if not isinstance(row, Mapping):
                raise ValidationError(f"rungs[{index}] must be a mapping")
            ident = row.get("id", row.get("key"))
            if not isinstance(ident, str) or not ident.strip():
                raise ValidationError(f"rungs[{index}] requires id")
            rungs.append(SubmissionRung(
                ident.strip(),
                str(row.get("title") or ident).strip(),
                _strings(row.get("seeds", row.get("modules")), f"rungs[{index}].seeds", allow_empty=False),
            ))
        return cls(
            library=library.strip("."),
            source_dir=source_dir,
            topics=tuple(topics),
            rungs=tuple(rungs),
            require_total_topics=bool(data.get("require_total_topics", True)),
            require_total_ladder=bool(data.get("require_total_ladder", False)),
        )

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "ModulePlanPolicy":
        p = Path(path).expanduser().resolve()
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError as ex:
            raise ValidationError(f"module plan does not exist: {p}") from ex
        try:
            data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
        except (json.JSONDecodeError, yaml.YAMLError) as ex:
            raise ValidationError(f"invalid module plan {p}: {ex}") from ex
        if not isinstance(data, Mapping):
            raise ValidationError("module plan root must be a mapping")
        return cls.from_mapping(data)

    def qualify(self, module: str) -> str:
        module = module.strip(".")
        return module if module == self.library or module.startswith(self.library + ".") else f"{self.library}.{module}"


@dataclass(frozen=True)
class TopicStatus:
    id: str
    title: str
    modules: tuple[str, ...]
    prerequisites: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "modules": list(self.modules), "prerequisites": list(self.prerequisites)}


@dataclass(frozen=True)
class RungStatus:
    id: str
    title: str
    seeds: tuple[str, ...]
    new_modules: tuple[str, ...]
    closed_slice: int
    unknown_seeds: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "seeds": list(self.seeds),
            "new_modules": list(self.new_modules), "new_count": len(self.new_modules),
            "closed_slice": self.closed_slice, "unknown_seeds": list(self.unknown_seeds),
        }


@dataclass(frozen=True)
class ModulePlanReport:
    policy: ModulePlanPolicy
    module_count: int
    topics: tuple[TopicStatus, ...]
    rungs: tuple[RungStatus, ...]
    unassigned: tuple[str, ...]
    off_ladder: tuple[str, ...]
    findings: tuple[Finding, ...]

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "library": self.policy.library,
            "module_count": self.module_count,
            "topics": [row.to_json() for row in self.topics],
            "rungs": [row.to_json() for row in self.rungs],
            "unassigned": list(self.unassigned),
            "off_ladder": list(self.off_ladder),
            "findings": [f.to_json() for f in self.findings],
            "semantic_warning": "Topic/rung edges are source-import planning data, not declaration-level proof dependencies or elaboration evidence.",
        }

    def render_markdown(self) -> str:
        lines = [f"# Module plan: {self.policy.library}", "", f"Tracked source modules: **{self.module_count}**", ""]
        lines += ["## Ordered topics", "", "| Topic | Modules | Prerequisites |", "| --- | ---: | --- |"]
        for row in self.topics:
            needs = ", ".join(f"`{x}`" for x in row.prerequisites) or "-"
            lines.append(f"| `{row.id}` {row.title} | {len(row.modules)} | {needs} |")
        if self.rungs:
            lines += ["", "## Submission ladder", "", "| Rung | New modules | Closed slice |", "| --- | ---: | ---: |"]
            for row in self.rungs:
                lines.append(f"| `{row.id}` {row.title} | {len(row.new_modules)} | {row.closed_slice} |")
        if self.unassigned:
            lines += ["", "## Unassigned modules", ""] + [f"- `{x}`" for x in self.unassigned]
        if self.off_ladder:
            lines += ["", "## Off-ladder modules", ""] + [f"- `{x}`" for x in self.off_ladder]
        if self.findings:
            lines += ["", "## Findings", ""]
            for finding in self.findings:
                loc = f" `{finding.location}`" if finding.location else ""
                lines.append(f"- **{finding.level.upper()}** `{finding.code}`{loc}: {finding.message}")
        return "\n".join(lines).rstrip() + "\n"


def _library_graph(policy: ModulePlanPolicy, base: Path) -> dict[str, set[str]]:
    source_base = (base / policy.source_dir).resolve()
    library_root = source_base / Path(*policy.library.split("."))
    if not library_root.is_dir():
        return {}
    graph: dict[str, set[str]] = {}
    for path in sorted(library_root.rglob("*.lean")):
        module = ".".join(path.relative_to(source_base).with_suffix("").parts)
        graph[module] = {
            dep for dep in imports_of(path)
            if dep == policy.library or dep.startswith(policy.library + ".")
        }
    return graph


def _closure(seeds: set[str], graph: Mapping[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(seeds)
    while stack:
        module = stack.pop()
        if module in seen or module not in graph:
            continue
        seen.add(module)
        stack.extend(graph[module])
    return seen


def check_module_plan(policy: ModulePlanPolicy, *, root: str | pathlib.Path | None = None) -> ModulePlanReport:
    base = find_workspace_root(root)
    graph = _library_graph(policy, base)
    findings: list[Finding] = []
    if not graph:
        findings.append(Finding("error", "module-plan-library", f"library source tree is missing or empty: {policy.library}", policy.library))

    topic_ids = [row.id for row in policy.topics]
    for ident in sorted({x for x in topic_ids if topic_ids.count(x) > 1}):
        findings.append(Finding("error", "module-plan-topic-id", f"duplicate topic id: {ident}", ident))
    assignment: dict[str, str] = {}
    duplicate_modules: dict[str, list[str]] = defaultdict(list)
    for topic in policy.topics:
        for raw in topic.modules:
            module = policy.qualify(raw)
            if module in assignment:
                duplicate_modules[module].extend([assignment[module], topic.id])
            else:
                assignment[module] = topic.id
    for module, owners in sorted(duplicate_modules.items()):
        findings.append(Finding("error", "module-plan-duplicate", f"module is assigned to multiple topics: {', '.join(dict.fromkeys(owners))}", module))
    unknown = sorted(module for module in assignment if module not in graph)
    for module in unknown:
        findings.append(Finding("error", "module-plan-unknown", "topic assigns a module that does not exist", module))
    unassigned = tuple(sorted(set(graph) - set(assignment)))
    if policy.require_total_topics:
        for module in unassigned:
            findings.append(Finding("error", "module-plan-unassigned", "library module is not assigned to any topic", module))

    order = {topic.id: index for index, topic in enumerate(policy.topics)}
    prerequisites: dict[str, set[str]] = {topic.id: set() for topic in policy.topics}
    for module, deps in graph.items():
        owner = assignment.get(module)
        if owner is None:
            continue
        for dep in deps:
            dep_owner = assignment.get(dep)
            if dep_owner is None or dep_owner == owner:
                continue
            prerequisites[owner].add(dep_owner)
            if order.get(dep_owner, -1) > order.get(owner, -1):
                findings.append(Finding(
                    "error", "module-plan-forward-ref",
                    f"topic {owner} imports later topic {dep_owner} through {module} -> {dep}", module,
                ))
    topics = tuple(TopicStatus(
        row.id, row.title,
        tuple(policy.qualify(x) for x in row.modules),
        tuple(sorted(prerequisites[row.id], key=lambda x: order.get(x, 10**9))),
    ) for row in policy.topics)

    rungs: list[RungStatus] = []
    cumulative: set[str] = set()
    seeds_so_far: set[str] = set()
    rung_ids: set[str] = set()
    for rung in policy.rungs:
        if rung.id in rung_ids:
            findings.append(Finding("error", "module-plan-rung-id", f"duplicate rung id: {rung.id}", rung.id))
        rung_ids.add(rung.id)
        qualified = {policy.qualify(x) for x in rung.seeds}
        unknown_seeds = tuple(sorted(qualified - set(graph)))
        for module in unknown_seeds:
            findings.append(Finding("error", "module-plan-rung-seed", "rung seed does not exist", module))
        seeds_so_far.update(qualified)
        closed = _closure(seeds_so_far, graph)
        new = tuple(sorted(closed - cumulative))
        cumulative = closed
        rungs.append(RungStatus(rung.id, rung.title, tuple(sorted(qualified)), new, len(closed), unknown_seeds))
    off_ladder = tuple(sorted(set(graph) - cumulative)) if policy.rungs else ()
    if policy.rungs and policy.require_total_ladder:
        for module in off_ladder:
            findings.append(Finding("error", "module-plan-off-ladder", "library module is not reached by the submission ladder", module))

    return ModulePlanReport(policy, len(graph), topics, tuple(rungs), unassigned, off_ladder, tuple(findings))

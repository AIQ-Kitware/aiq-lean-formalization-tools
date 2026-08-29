"""Configurable import-layer policies for Lean source trees.

The source repository accumulated several valuable dependency gates, but their
actual layers were project-specific.  This module keeps the mechanism and moves
all architecture choices into a small YAML/JSON policy file.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from fnmatch import fnmatchcase
import json
from pathlib import Path
from typing import Any, Iterable

import yaml

from .errors import ValidationError
from .lean_source import LeanSourceIndex, scan_lean_project


@dataclass(frozen=True)
class ImportViolation:
    rule: str
    module: str
    imported: str
    detail: str
    path: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "module": self.module,
            "imported": self.imported,
            "detail": self.detail,
            "path": list(self.path),
        }


@dataclass(frozen=True)
class ImportRule:
    id: str
    source_groups: tuple[str, ...]
    allow_groups: tuple[str, ...] = ()
    deny_groups: tuple[str, ...] = ()
    transitive: bool = False
    message: str = ""


@dataclass
class ImportPolicy:
    groups: dict[str, tuple[str, ...]]
    rules: list[ImportRule]
    exceptions: dict[str, set[str]]

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> "ImportPolicy":
        groups_data = data.get("groups", {})
        if not isinstance(groups_data, dict) or not groups_data:
            raise ValidationError("import policy requires a non-empty 'groups' mapping")
        groups: dict[str, tuple[str, ...]] = {}
        for name, patterns in groups_data.items():
            if isinstance(patterns, str):
                patterns = [patterns]
            if not isinstance(patterns, list) or not all(isinstance(x, str) for x in patterns):
                raise ValidationError(f"group {name!r} must be a string or list of patterns")
            groups[str(name)] = tuple(patterns)

        rules_data = data.get("rules", [])
        if not isinstance(rules_data, list):
            raise ValidationError("import policy 'rules' must be a list")
        rules: list[ImportRule] = []
        seen: set[str] = set()
        for raw in rules_data:
            if not isinstance(raw, dict):
                raise ValidationError("every import policy rule must be an object")
            rid = str(raw.get("id", "")).strip()
            if not rid or rid in seen:
                raise ValidationError(f"invalid or duplicate rule id: {rid!r}")
            seen.add(rid)
            source = raw.get("from", raw.get("source", []))
            if isinstance(source, str):
                source = [source]
            allow = raw.get("allow", [])
            deny = raw.get("deny", [])
            if isinstance(allow, str): allow = [allow]
            if isinstance(deny, str): deny = [deny]
            for field, values in (("from", source), ("allow", allow), ("deny", deny)):
                if not isinstance(values, list) or not all(isinstance(x, str) for x in values):
                    raise ValidationError(f"rule {rid!r} field {field!r} must be a list of group names")
                missing = [x for x in values if x not in groups]
                if missing:
                    raise ValidationError(f"rule {rid!r} references unknown group(s): {', '.join(missing)}")
            if not source:
                raise ValidationError(f"rule {rid!r} requires at least one source group")
            if not allow and not deny:
                raise ValidationError(f"rule {rid!r} requires 'allow' or 'deny'")
            rules.append(ImportRule(
                id=rid,
                source_groups=tuple(source),
                allow_groups=tuple(allow),
                deny_groups=tuple(deny),
                transitive=bool(raw.get("transitive", False)),
                message=str(raw.get("message", "")),
            ))
        exc_data = data.get("exceptions", {})
        if not isinstance(exc_data, dict):
            raise ValidationError("import policy 'exceptions' must be a mapping")
        exceptions: dict[str, set[str]] = {}
        for rid, modules in exc_data.items():
            if isinstance(modules, str): modules = [modules]
            if not isinstance(modules, list) or not all(isinstance(x, str) for x in modules):
                raise ValidationError(f"exceptions for {rid!r} must be a list of module patterns")
            exceptions[str(rid)] = set(modules)
        return cls(groups, rules, exceptions)

    @classmethod
    def load(cls, path: str | Path) -> "ImportPolicy":
        p = Path(path).expanduser().resolve()
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValidationError("import policy must be a mapping")
        return cls.from_data(data)

    def classify(self, module: str) -> set[str]:
        return {
            group
            for group, patterns in self.groups.items()
            if any(_module_match(module, pattern) for pattern in patterns)
        }

    def exempt(self, rule: str, module: str) -> bool:
        return any(_module_match(module, pattern) for pattern in self.exceptions.get(rule, ()))


def _module_match(module: str, pattern: str) -> bool:
    if any(ch in pattern for ch in "*?["):
        return fnmatchcase(module, pattern)
    return module == pattern or module.startswith(pattern + ".")


def _in_groups(policy: ImportPolicy, module: str, groups: Iterable[str]) -> bool:
    classes = policy.classify(module)
    return bool(classes.intersection(groups))


def _shortest_forbidden_path(
    index: LeanSourceIndex,
    policy: ImportPolicy,
    start: str,
    predicate,
) -> tuple[str, ...] | None:
    queue: deque[tuple[str, ...]] = deque([(start,)])
    seen = {start}
    while queue:
        path = queue.popleft()
        current = path[-1]
        for dep in sorted(index.imports.get(current, ())):
            new_path = (*path, dep)
            if predicate(dep):
                return new_path
            if dep in index.modules and dep not in seen:
                seen.add(dep)
                queue.append(new_path)
    return None


def check_import_policy(index: LeanSourceIndex, policy: ImportPolicy) -> list[ImportViolation]:
    violations: list[ImportViolation] = []
    for rule in policy.rules:
        for module in sorted(index.modules):
            if not _in_groups(policy, module, rule.source_groups) or policy.exempt(rule.id, module):
                continue

            def forbidden(dep: str) -> bool:
                if rule.deny_groups and _in_groups(policy, dep, rule.deny_groups):
                    return True
                if rule.allow_groups and not _in_groups(policy, dep, rule.allow_groups):
                    return True
                return False

            if rule.transitive:
                path = _shortest_forbidden_path(index, policy, module, forbidden)
                if path is not None:
                    dep = path[-1]
                    violations.append(ImportViolation(
                        rule=rule.id,
                        module=module,
                        imported=dep,
                        detail=rule.message or f"transitively reaches forbidden import {dep}",
                        path=path,
                    ))
                continue

            for dep in sorted(index.imports.get(module, ())):
                if forbidden(dep):
                    violations.append(ImportViolation(
                        rule=rule.id,
                        module=module,
                        imported=dep,
                        detail=rule.message or f"imports forbidden module {dep}",
                        path=(module, dep),
                    ))
    return violations


def check_import_policy_path(
    policy_path: str | Path,
    *,
    root: str | Path | None = None,
) -> list[ImportViolation]:
    return check_import_policy(scan_lean_project(root), ImportPolicy.load(policy_path))

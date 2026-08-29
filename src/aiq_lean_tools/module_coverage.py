"""Policy-driven coverage checks for Lean module subtrees.

The check answers a structural question: does at least one declared root reach every
module in a named source scope?  A module outside the root import closure must be
listed explicitly with a reason, or fall under an explicitly excluded subtree.
This prevents a green root build from being mistaken for coverage of files that the
root never imports.

Source reachability does not certify that a module elaborates.  Pair this check with
a real ``lake build``/Lean gate when compilation is part of the contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .common import Finding
from .errors import ValidationError
from .lean_source import LeanSourceIndex, scan_lean_project


def _module_match(module: str, pattern: str) -> bool:
    if any(ch in pattern for ch in "*?["):
        return fnmatchcase(module, pattern)
    return module == pattern or module.startswith(pattern + ".")


def _string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x.strip() for x in value):
        raise ValidationError(f"{field_name} must be a non-empty string or list of strings")
    return tuple(x.strip() for x in value)


def _reason_map(value: Any, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field_name} must be a mapping of module/prefix to reason")
    out: dict[str, str] = {}
    for key, reason in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValidationError(f"{field_name} keys must be non-empty strings")
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError(f"{field_name}[{key!r}] requires a non-empty reason")
        out[key.strip()] = reason.strip()
    return out


@dataclass(frozen=True)
class ModuleCoveragePolicy:
    roots: tuple[str, ...]
    scope: tuple[str, ...]
    excluded_modules: Mapping[str, str] = field(default_factory=dict)
    excluded_prefixes: Mapping[str, str] = field(default_factory=dict)
    inherit_exclusions: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ModuleCoveragePolicy":
        roots = _string_list(data.get("roots", []), "roots")
        raw_scope = data.get("scope", list(roots))
        scope = _string_list(raw_scope, "scope")
        return cls(
            roots=roots,
            scope=scope,
            excluded_modules=_reason_map(data.get("excluded_modules", {}), "excluded_modules"),
            excluded_prefixes=_reason_map(data.get("excluded_prefixes", {}), "excluded_prefixes"),
            inherit_exclusions=bool(data.get("inherit_exclusions", True)),
        )

    @classmethod
    def load(cls, path: str | Path) -> "ModuleCoveragePolicy":
        p = Path(path).expanduser().resolve()
        text = p.read_text(encoding="utf-8")
        data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
        if not isinstance(data, Mapping):
            raise ValidationError("module coverage policy must be a mapping")
        return cls.from_mapping(data)


@dataclass(frozen=True)
class ModuleCoverageReport:
    roots: tuple[str, ...]
    scope_modules: tuple[str, ...]
    covered: tuple[str, ...]
    explicitly_excluded: tuple[str, ...]
    prefix_excluded: tuple[str, ...]
    inherited_excluded: tuple[str, ...]
    uncovered: tuple[str, ...]
    findings: tuple[Finding, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "roots": list(self.roots),
            "scope_module_count": len(self.scope_modules),
            "covered_count": len(self.covered),
            "explicitly_excluded_count": len(self.explicitly_excluded),
            "prefix_excluded_count": len(self.prefix_excluded),
            "inherited_excluded_count": len(self.inherited_excluded),
            "uncovered_count": len(self.uncovered),
            "scope_modules": list(self.scope_modules),
            "covered": list(self.covered),
            "explicitly_excluded": list(self.explicitly_excluded),
            "prefix_excluded": list(self.prefix_excluded),
            "inherited_excluded": list(self.inherited_excluded),
            "uncovered": list(self.uncovered),
            "findings": [finding.to_json() for finding in self.findings],
            "semantic_warning": "Source import reachability does not certify Lean elaboration or successful compilation.",
        }


def check_module_coverage(
    policy: ModuleCoveragePolicy,
    *,
    root: str | Path | None = None,
    index: LeanSourceIndex | None = None,
) -> ModuleCoverageReport:
    source_index = index or scan_lean_project(root)
    findings: list[Finding] = []

    missing_roots = [module for module in policy.roots if module not in source_index.modules]
    for module in missing_roots:
        findings.append(Finding("error", "module-coverage-root", f"coverage root module is missing: {module}", module))

    scope_modules = sorted(
        module for module in source_index.modules
        if any(_module_match(module, pattern) for pattern in policy.scope)
    )
    if not scope_modules:
        findings.append(Finding("error", "module-coverage-scope", "module coverage scope matched no local Lean modules", ", ".join(policy.scope)))

    covered_set = source_index.import_closure(module for module in policy.roots if module in source_index.modules)
    covered = sorted(module for module in scope_modules if module in covered_set)

    direct_excluded = set(policy.excluded_modules)
    stale_modules = sorted(module for module in direct_excluded if module not in source_index.modules)
    for module in stale_modules:
        findings.append(Finding("error", "module-coverage-stale-exclusion", f"excluded module no longer exists: {module}", module))

    outside_scope = sorted(module for module in direct_excluded if module in source_index.modules and module not in scope_modules)
    for module in outside_scope:
        findings.append(Finding("warning", "module-coverage-exclusion-outside-scope", f"excluded module is outside the configured coverage scope: {module}", module))

    prefix_excluded_set = {
        module for module in scope_modules
        if any(_module_match(module, prefix) for prefix in policy.excluded_prefixes)
    }
    for prefix in policy.excluded_prefixes:
        if not any(_module_match(module, prefix) for module in scope_modules):
            findings.append(Finding("warning", "module-coverage-stale-prefix", f"excluded prefix matches no module in scope: {prefix}", prefix))

    inherited_set: set[str] = set()
    if policy.inherit_exclusions and direct_excluded:
        live_direct = direct_excluded.intersection(source_index.modules)
        for module in scope_modules:
            if module in direct_excluded:
                continue
            if source_index.import_closure([module]).intersection(live_direct):
                inherited_set.add(module)

    excused = direct_excluded | prefix_excluded_set | inherited_set
    uncovered = sorted(module for module in scope_modules if module not in covered_set and module not in excused)
    for module in uncovered:
        findings.append(Finding("error", "module-coverage-unreached", "module is not reachable from a coverage root and has no recorded exclusion", module))

    reached_exclusions = sorted(module for module in direct_excluded if module in covered_set)
    for module in reached_exclusions:
        findings.append(Finding("warning", "module-coverage-reached-exclusion", f"excluded module is already reached by a coverage root: {module}", module))

    return ModuleCoverageReport(
        roots=policy.roots,
        scope_modules=tuple(scope_modules),
        covered=tuple(covered),
        explicitly_excluded=tuple(sorted(direct_excluded.intersection(scope_modules))),
        prefix_excluded=tuple(sorted(prefix_excluded_set)),
        inherited_excluded=tuple(sorted(inherited_set)),
        uncovered=tuple(uncovered),
        findings=tuple(findings),
    )


def check_module_coverage_path(
    policy_path: str | Path,
    *,
    root: str | Path | None = None,
) -> ModuleCoverageReport:
    return check_module_coverage(ModuleCoveragePolicy.load(policy_path), root=root)

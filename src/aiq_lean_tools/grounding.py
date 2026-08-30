"""Policy-driven structural grounding checks for Lean formalization lanes.

A grounding policy records concrete files and declarations that must remain present,
plus source-level placeholder restrictions.  It is intentionally a structural gate:
compiler-backed declaration existence and dependency claims belong in ``leanq`` or a
``LeanBackend`` probe.
"""
from __future__ import annotations

import fnmatch
import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import yaml

from .common import Finding, Path, find_workspace_root
from .errors import ValidationError
from .lean_source import LeanSourceIndex, lean_files, scan_lean_project, strip_comments


@dataclass(frozen=True)
class GroundingPolicy:
    required_files: tuple[str, ...] = ()
    required_declarations: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    include: tuple[str, ...] = ("**/*.lean", "*.lean")
    forbid_admissions: bool = False
    forbid_axioms: bool = False
    forbidden_patterns: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "GroundingPolicy":
        required_files = _string_list(data.get("required_files", []), "required_files")
        raw_decls = data.get("required_declarations", {})
        if raw_decls is None:
            raw_decls = {}
        if not isinstance(raw_decls, Mapping):
            raise ValidationError("required_declarations must be a mapping of path to declaration names")
        required_declarations: dict[str, tuple[str, ...]] = {}
        for path, names in raw_decls.items():
            if not isinstance(path, str) or not path.strip():
                raise ValidationError("required_declarations keys must be non-empty paths")
            required_declarations[path] = _string_list(names, f"required_declarations.{path}")

        forbid = data.get("forbid", {})
        if forbid is None:
            forbid = {}
        if not isinstance(forbid, Mapping):
            raise ValidationError("forbid must be a mapping")
        include = _string_list(forbid.get("include", data.get("include", ["**/*.lean", "*.lean"])), "forbid.include")
        patterns = _string_list(forbid.get("patterns", []), "forbid.patterns")
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as ex:
                raise ValidationError(f"invalid forbidden regex {pattern!r}: {ex}") from ex
        return cls(
            required_files=required_files,
            required_declarations=required_declarations,
            include=include,
            forbid_admissions=bool(forbid.get("admissions", False)),
            forbid_axioms=bool(forbid.get("axioms", False)),
            forbidden_patterns=patterns,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "required_files": list(self.required_files),
            "required_declarations": {k: list(v) for k, v in self.required_declarations.items()},
            "forbid": {
                "include": list(self.include),
                "admissions": self.forbid_admissions,
                "axioms": self.forbid_axioms,
                "patterns": list(self.forbidden_patterns),
            },
        }


def _string_list(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise ValidationError(f"{name} must be a list of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError(f"{name} must contain only non-empty strings")
        out.append(item)
    return tuple(out)


def load_grounding_policy(path: str | pathlib.Path) -> GroundingPolicy:
    p = Path(path).expanduser().resolve()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as ex:
        raise ValidationError(f"grounding policy does not exist: {p}") from ex
    try:
        data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as ex:
        raise ValidationError(f"invalid grounding policy {p}: {ex}") from ex
    if not isinstance(data, Mapping):
        raise ValidationError("grounding policy root must be a mapping")
    return GroundingPolicy.from_mapping(data)


def _matches(rel: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(rel, pattern) or Path(rel).match(pattern) for pattern in patterns)


def check_grounding_policy(
    policy: GroundingPolicy,
    *,
    root: str | pathlib.Path | None = None,
    index: LeanSourceIndex | None = None,
) -> list[Finding]:
    base = find_workspace_root(root)
    source_index = index or scan_lean_project(base)
    findings: list[Finding] = []

    for rel in policy.required_files:
        path = base / rel
        if not path.is_file():
            findings.append(Finding("error", "grounding-file", "required grounded file is missing", rel))

    by_rel: dict[str, list] = {}
    for decl in source_index.declarations:
        try:
            rel = decl.path.relative_to(base).as_posix()
        except ValueError:
            continue
        by_rel.setdefault(rel, []).append(decl)

    for rel, names in policy.required_declarations.items():
        path = base / rel
        if not path.is_file():
            # The missing-file finding above is sufficient if the path was also listed there,
            # but declaration policies may refer to a file without duplicating required_files.
            findings.append(Finding("error", "grounding-declaration-file", "file for required declarations is missing", rel))
            continue
        rows = by_rel.get(Path(rel).as_posix(), [])
        full_names = {row.name for row in rows}
        short_names = {row.short_name for row in rows}
        for name in names:
            if name not in full_names and name.rsplit(".", 1)[-1] not in short_names:
                findings.append(Finding("error", "grounding-declaration", f"required declaration is missing: {name}", rel))

    if not (policy.forbid_admissions or policy.forbid_axioms or policy.forbidden_patterns):
        return findings

    selected_paths = {
        path.resolve()
        for path in lean_files(base)
        if _matches(path.relative_to(base).as_posix(), policy.include)
    }

    if policy.forbid_admissions:
        for module in sorted(source_index.admitted_modules):
            path = source_index.modules.get(module)
            if path is not None and path.resolve() in selected_paths:
                findings.append(Finding("error", "grounding-admission", "forbidden sorry/admit placeholder", path.relative_to(base).as_posix()))

    if policy.forbid_axioms:
        for decl in source_index.declarations:
            if decl.kind == "axiom" and decl.path.resolve() in selected_paths:
                findings.append(Finding("error", "grounding-axiom", f"forbidden axiom declaration: {decl.name}", f"{decl.path.relative_to(base).as_posix()}:{decl.line}"))

    compiled_patterns = [(pattern, re.compile(pattern)) for pattern in policy.forbidden_patterns]
    if compiled_patterns:
        for path in sorted(selected_paths):
            clean = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
            for label, regex in compiled_patterns:
                match = regex.search(clean)
                if match:
                    line = clean.count("\n", 0, match.start()) + 1
                    findings.append(Finding("error", "grounding-pattern", f"forbidden source pattern matched: {label}", f"{path.relative_to(base).as_posix()}:{line}"))

    return findings

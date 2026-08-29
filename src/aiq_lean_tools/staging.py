"""Status registry for Lean modules that are not yet in a stable aggregate.

The registry makes staging debt explicit without confusing annotations with
compilation evidence.  Static validation checks status/reason/module presence.
Optional compiler checks can elaborate selected statuses and build an aggregate
target, with a policy that can require the registry to become empty once the
aggregate succeeds.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import yaml

from .common import Finding, Path, find_workspace_root
from .errors import ValidationError
from .lean_backend import CommandResult, LeanBackend, SubprocessLeanBackend
from .lean_source import LeanSourceIndex, scan_lean_project


def _strings(value: Any, name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValidationError(f"{name} must be a list of strings")
    out = tuple(str(x).strip() for x in value if isinstance(x, str) and x.strip())
    if len(out) != len(value) or (not allow_empty and not out):
        raise ValidationError(f"{name} must contain only non-empty strings")
    return out


@dataclass(frozen=True)
class StagingRecord:
    module: str
    status: str
    reason: str


@dataclass(frozen=True)
class StagingPolicy:
    records: tuple[StagingRecord, ...]
    allowed_statuses: tuple[str, ...] = ("active", "parked")
    compile_statuses: tuple[str, ...] = ("active",)
    aggregate_target: str | None = None
    require_aggregate_success: bool = False
    require_empty_on_aggregate_success: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "StagingPolicy":
        allowed = _strings(data.get("allowed_statuses", ["active", "parked"]), "allowed_statuses", allow_empty=False)
        compile_statuses = _strings(data.get("compile_statuses", ["active"]), "compile_statuses")
        unknown = sorted(set(compile_statuses) - set(allowed))
        if unknown:
            raise ValidationError(f"compile_statuses are not in allowed_statuses: {', '.join(unknown)}")
        raw = data.get("records", data.get("roots", []))
        if not isinstance(raw, list):
            raise ValidationError("staging records must be a list")
        records: list[StagingRecord] = []
        for index, row in enumerate(raw):
            if not isinstance(row, Mapping):
                raise ValidationError(f"records[{index}] must be a mapping")
            module = row.get("module", row.get("path"))
            status = row.get("status")
            reason = row.get("reason")
            if not isinstance(module, str) or not module.strip():
                raise ValidationError(f"records[{index}] requires module/path")
            if not isinstance(status, str) or not status.strip():
                raise ValidationError(f"records[{index}] requires status")
            if not isinstance(reason, str) or not reason.strip():
                raise ValidationError(f"records[{index}] requires a non-empty reason")
            records.append(StagingRecord(module.strip(), status.strip(), reason.strip()))
        aggregate = data.get("aggregate", {}) or {}
        if isinstance(aggregate, str):
            aggregate = {"target": aggregate}
        if not isinstance(aggregate, Mapping):
            raise ValidationError("aggregate must be a mapping or target string")
        target = aggregate.get("target", data.get("aggregate_target"))
        if target is not None and (not isinstance(target, str) or not target.strip()):
            raise ValidationError("aggregate target must be a non-empty string or null")
        return cls(
            records=tuple(records),
            allowed_statuses=allowed,
            compile_statuses=compile_statuses,
            aggregate_target=target.strip() if isinstance(target, str) else None,
            require_aggregate_success=bool(aggregate.get("require_success", data.get("require_aggregate_success", False))),
            require_empty_on_aggregate_success=bool(aggregate.get("require_empty_on_success", data.get("require_empty_on_aggregate_success", True))),
        )

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "StagingPolicy":
        p = Path(path).expanduser().resolve()
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError as ex:
            raise ValidationError(f"staging policy does not exist: {p}") from ex
        try:
            data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
        except (json.JSONDecodeError, yaml.YAMLError) as ex:
            raise ValidationError(f"invalid staging policy {p}: {ex}") from ex
        if not isinstance(data, Mapping):
            raise ValidationError("staging policy root must be a mapping")
        return cls.from_mapping(data)


@dataclass(frozen=True)
class StagingReport:
    policy: StagingPolicy
    normalized_modules: tuple[str, ...]
    findings: tuple[Finding, ...]
    compile_results: tuple[CommandResult, ...] = ()
    aggregate_result: CommandResult | None = None
    compilation_checked: bool = False

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def to_json(self) -> dict[str, Any]:
        counts = {status: sum(record.status == status for record in self.policy.records) for status in self.policy.allowed_statuses}
        return {
            "ok": self.ok,
            "compilation_checked": self.compilation_checked,
            "counts": counts,
            "records": [
                {"module": module, "status": record.status, "reason": record.reason}
                for module, record in zip(self.normalized_modules, self.policy.records)
            ],
            "aggregate_target": self.policy.aggregate_target,
            "aggregate_returncode": self.aggregate_result.returncode if self.aggregate_result else None,
            "compile_results": [
                {"argv": list(row.argv), "returncode": row.returncode, "stdout": row.stdout, "stderr": row.stderr}
                for row in self.compile_results
            ],
            "findings": [f.to_json() for f in self.findings],
        }


def _normalize_module(value: str, index: LeanSourceIndex, base: Path) -> str | None:
    if value.endswith(".lean") or "/" in value:
        candidate = (base / value).resolve()
        for module, path in index.modules.items():
            if path.resolve() == candidate:
                return module
        return None
    return value


def check_staging(
    policy: StagingPolicy,
    *,
    root: str | pathlib.Path | None = None,
    index: LeanSourceIndex | None = None,
    compile: bool = False,
    backend: LeanBackend | None = None,
    timeout: int = 600,
) -> StagingReport:
    base = find_workspace_root(root)
    source_index = index or scan_lean_project(base)
    findings: list[Finding] = []
    normalized: list[str] = []
    seen: set[str] = set()

    for record in policy.records:
        module = _normalize_module(record.module, source_index, base)
        if module is None:
            findings.append(Finding("error", "staging-module", "registered staging module is missing", record.module))
            normalized.append(record.module)
            continue
        normalized.append(module)
        if module in seen:
            findings.append(Finding("error", "staging-duplicate", f"duplicate staging module: {module}", module))
        seen.add(module)
        if record.status not in policy.allowed_statuses:
            findings.append(Finding("error", "staging-status", f"invalid staging status {record.status!r}; expected one of {', '.join(policy.allowed_statuses)}", module))
        if module not in source_index.modules:
            findings.append(Finding("error", "staging-module", "registered staging module is missing", module))

    compile_results: list[CommandResult] = []
    aggregate_result: CommandResult | None = None
    if compile:
        engine = backend or SubprocessLeanBackend()
        for module, record in zip(normalized, policy.records):
            if record.status not in policy.compile_statuses or module not in source_index.modules:
                continue
            path = source_index.modules[module]
            rel = path.relative_to(base)
            result = engine.run(base, ["lake", "env", "lean", str(rel)], timeout=timeout)
            compile_results.append(result)
            if result.returncode != 0:
                findings.append(Finding("error", "staging-compile", f"staging module did not elaborate: {module}", module))
        if policy.aggregate_target:
            aggregate_result = engine.run(base, ["lake", "build", policy.aggregate_target], timeout=timeout)
            if aggregate_result.returncode != 0 and policy.require_aggregate_success:
                findings.append(Finding("error", "staging-aggregate-build", f"aggregate target did not build: {policy.aggregate_target}", policy.aggregate_target))
            if aggregate_result.returncode == 0 and policy.require_empty_on_aggregate_success and policy.records:
                findings.append(Finding(
                    "error", "staging-stale-registry",
                    f"aggregate target {policy.aggregate_target} builds, but {len(policy.records)} staging record(s) remain",
                    policy.aggregate_target,
                ))

    return StagingReport(
        policy=policy,
        normalized_modules=tuple(normalized),
        findings=tuple(findings),
        compile_results=tuple(compile_results),
        aggregate_result=aggregate_result,
        compilation_checked=compile,
    )

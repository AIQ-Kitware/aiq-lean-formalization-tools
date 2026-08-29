"""Deterministic Lean module export between staging and upstream namespaces.

The exporter performs one intentionally narrow transformation: local import
module prefixes are rewritten while all other source text is copied verbatim.
Project-specific cluster membership, namespace prefixes, and permitted import
roots live in a YAML/JSON manifest.
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import yaml

from .common import Finding, Path, atomic_write_text
from .errors import ValidationError

IMPORT_RE = re.compile(
    r"^(?P<indent>\s*)(?P<kw>(?:(?:public|private|meta)\s+)*import\s+)"
    r"(?P<module>[A-Za-z0-9_.]+)(?P<rest>.*)$"
)
DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|noncomputable|scoped|unsafe|partial)\s+)*"
    r"(?:def|theorem|lemma|abbrev|structure|instance|inductive|class|axiom|opaque)\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_'.!?]*)"
)


def _string_list(value: Any, name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
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


def _matches_prefix(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


@dataclass(frozen=True)
class ExportRecord:
    source_module: str
    target_module: str | None = None


@dataclass(frozen=True)
class ExportCluster:
    name: str
    source_modules: tuple[str, ...]


@dataclass(frozen=True)
class ModuleExportPolicy:
    source_prefix: str
    target_prefix: str
    clusters: tuple[ExportCluster, ...]
    records: tuple[ExportRecord, ...]
    allowed_import_prefixes: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ModuleExportPolicy":
        source_prefix = data.get("source_prefix", "ForTauCeti")
        target_prefix = data.get("target_prefix", "TauCeti")
        if not isinstance(source_prefix, str) or not source_prefix.strip():
            raise ValidationError("source_prefix must be a non-empty string")
        if not isinstance(target_prefix, str) or not target_prefix.strip():
            raise ValidationError("target_prefix must be a non-empty string")
        raw_clusters = data.get("clusters")
        if not isinstance(raw_clusters, list) or not raw_clusters:
            raise ValidationError("module export policy requires a non-empty clusters list")
        clusters: list[ExportCluster] = []
        for index, row in enumerate(raw_clusters):
            if not isinstance(row, Mapping):
                raise ValidationError(f"clusters[{index}] must be a mapping")
            name = row.get("name", row.get("cluster"))
            if not isinstance(name, str) or not name.strip():
                raise ValidationError(f"clusters[{index}] requires name/cluster")
            modules = row.get("source_modules", row.get("staging_modules"))
            clusters.append(ExportCluster(name.strip(), _string_list(modules, f"clusters[{index}].source_modules", allow_empty=False)))
        raw_records = data.get("records", [])
        if not isinstance(raw_records, list):
            raise ValidationError("records must be a list")
        records: list[ExportRecord] = []
        for index, row in enumerate(raw_records):
            if not isinstance(row, Mapping):
                raise ValidationError(f"records[{index}] must be a mapping")
            # Historical extraction manifests also had a ``source_module``
            # field naming the pre-staging origin.  When ``staging_module`` is
            # present it is the export source and must take precedence.
            source = row.get("staging_module", row.get("source_module"))
            target = row.get("target_module", row.get("final_tauceti_module"))
            if not isinstance(source, str) or not source.strip():
                raise ValidationError(f"records[{index}] requires source_module")
            if target is not None and (not isinstance(target, str) or not target.strip()):
                raise ValidationError(f"records[{index}].target_module must be a non-empty string or null")
            records.append(ExportRecord(source.strip(), target.strip() if isinstance(target, str) else None))
        allowed = data.get("allowed_import_prefixes", ["Mathlib", target_prefix])
        allowed_tuple = _string_list(allowed, "allowed_import_prefixes", allow_empty=False)
        return cls(source_prefix.strip("."), target_prefix.strip("."), tuple(clusters), tuple(records), allowed_tuple)

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "ModuleExportPolicy":
        p = Path(path).expanduser().resolve()
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError as ex:
            raise ValidationError(f"module export policy does not exist: {p}") from ex
        try:
            data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
        except (json.JSONDecodeError, yaml.YAMLError) as ex:
            raise ValidationError(f"invalid module export policy {p}: {ex}") from ex
        if not isinstance(data, Mapping):
            raise ValidationError("module export policy root must be a mapping")
        return cls.from_mapping(data)

    def target_for(self, source_module: str) -> str:
        for record in self.records:
            if record.source_module == source_module and record.target_module:
                return record.target_module
        if not _matches_prefix(source_module, self.source_prefix):
            raise ValidationError(f"source module {source_module!r} is outside source_prefix {self.source_prefix!r}")
        suffix = source_module[len(self.source_prefix):]
        return self.target_prefix + suffix

    def modules(self, cluster: str | None = None) -> tuple[str, ...]:
        values: list[str] = []
        for row in self.clusters:
            if cluster is None or row.name == cluster:
                values.extend(row.source_modules)
        return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class ExportItem:
    source_module: str
    target_module: str
    source_path: str
    target_path: str
    status: str
    declarations: tuple[str, ...]
    forbidden_imports: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "source_module": self.source_module,
            "target_module": self.target_module,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "status": self.status,
            "declarations": list(self.declarations),
            "forbidden_imports": list(self.forbidden_imports),
        }


@dataclass(frozen=True)
class ModuleExportReport:
    items: tuple[ExportItem, ...]
    findings: tuple[Finding, ...]
    wrote: bool

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "wrote": self.wrote,
            "items": [row.to_json() for row in self.items],
            "findings": [f.to_json() for f in self.findings],
            "transformation": "rewrite configured source import prefix to target prefix; preserve all other source text verbatim",
        }


def module_path(root: Path, module: str) -> Path:
    return root / (module.replace(".", "/") + ".lean")


def rewrite_imports(text: str, policy: ModuleExportPolicy) -> tuple[str, tuple[str, ...]]:
    out: list[str] = []
    forbidden: list[str] = []
    for line in text.splitlines():
        match = IMPORT_RE.match(line)
        if not match:
            out.append(line)
            continue
        module = match.group("module")
        if _matches_prefix(module, policy.source_prefix):
            module = policy.target_prefix + module[len(policy.source_prefix):]
        if not any(_matches_prefix(module, prefix) for prefix in policy.allowed_import_prefixes):
            forbidden.append(module)
        out.append(match.group("indent") + match.group("kw") + module + match.group("rest"))
    trailing = "\n" if text.endswith("\n") else ""
    return "\n".join(out) + trailing, tuple(dict.fromkeys(forbidden))


def declaration_names(text: str) -> tuple[str, ...]:
    out = []
    for line in text.splitlines():
        match = DECL_RE.match(line)
        if match:
            out.append(match.group("name"))
    return tuple(out)


def export_modules(
    policy: ModuleExportPolicy,
    *,
    source_root: str | pathlib.Path,
    target_root: str | pathlib.Path,
    cluster: str | None = None,
    write: bool = False,
) -> ModuleExportReport:
    source_base = Path(source_root).expanduser().resolve()
    target_base = Path(target_root).expanduser().resolve()
    modules = policy.modules(cluster)
    findings: list[Finding] = []
    items: list[ExportItem] = []
    if not modules:
        findings.append(Finding("error", "export-cluster", f"no source modules found for cluster {cluster!r}"))
        return ModuleExportReport((), tuple(findings), write)

    seen_targets: dict[str, str] = {}
    for source_module in modules:
        try:
            target_module = policy.target_for(source_module)
        except ValidationError as ex:
            findings.append(Finding("error", "export-source-prefix", str(ex), source_module))
            continue
        other = seen_targets.get(target_module)
        if other and other != source_module:
            findings.append(Finding("error", "export-target-collision", f"multiple source modules map to {target_module}: {other}, {source_module}", target_module))
            continue
        seen_targets[target_module] = source_module
        source_path = module_path(source_base, source_module)
        target_path = module_path(target_base, target_module)
        try:
            target_path.relative_to(target_base)
        except ValueError:
            findings.append(Finding("error", "export-target-path", "target module escapes target root", target_module))
            continue
        if not source_path.is_file():
            findings.append(Finding("error", "export-source-missing", "source module file is missing", source_path.as_posix()))
            items.append(ExportItem(source_module, target_module, str(source_path), str(target_path), "ERROR", ()))
            continue
        text = source_path.read_text(encoding="utf-8")
        transformed, forbidden = rewrite_imports(text, policy)
        decls = declaration_names(transformed)
        if forbidden:
            findings.append(Finding("error", "export-forbidden-import", f"forbidden imports after rewrite: {', '.join(forbidden)}", source_module))
            items.append(ExportItem(source_module, target_module, str(source_path), str(target_path), "ERROR", decls, forbidden))
            continue
        if target_path.exists():
            existing = target_path.read_text(encoding="utf-8")
            if existing == transformed:
                status = "UNCHANGED" if write else "MATCH"
            elif write:
                atomic_write_text(target_path, transformed)
                status = "WROTE"
            else:
                findings.append(Finding("error", "export-drift", "target copy differs from transformed source", target_path.as_posix()))
                status = "DIFF"
        elif write:
            atomic_write_text(target_path, transformed)
            status = "WROTE"
        else:
            status = "NEW"
        items.append(ExportItem(source_module, target_module, str(source_path), str(target_path), status, decls))

    return ModuleExportReport(tuple(items), tuple(findings), write)

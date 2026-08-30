"""Reusable Python-only audits for Lean source trees."""
from __future__ import annotations

import collections
import json
import pathlib
import re
import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .common import Path, atomic_write_json
from .lean_source import LeanSourceIndex, SourceDecl, module_in_scope, scan_lean_project

SCAFFOLD_DEFNS: dict[str, str] = {
    "published": r"^\s*(let\b|set\b|obtain\b|haveI\b|letI\b|classical\b)",
    "have-term": r"^\s*(let\b|set\b|obtain\b|haveI\b|letI\b|classical\b|have\s+[\w'⟨⟩,\s]+:=(?!\s*by\b))",
    "have-all": r"^\s*(let\b|set\b|obtain\b|haveI\b|letI\b|classical\b|have\b)",
    "have-and-cases": r"^\s*(let\b|set\b|obtain\b|haveI\b|letI\b|classical\b|have\b|rcases\b|cases\b)",
}
PROOF_DECL_RE = re.compile(
    r"^(?P<indent>\s*)(?:@\[[^]]*\]\s*)?"
    r"(?:public |private |protected |noncomputable |partial |unsafe )*"
    r"(?:theorem|lemma|def|abbrev|instance)\s+(?P<name>[A-Za-z_][\w.'!?]*)"
)
PROOF_BOUNDARY_RE = re.compile(
    r"^(end\b|namespace\b|section\b|/-!|/--|"
    r"\s*(?:@\[[^]]*\]\s*)?(?:public |private |protected |noncomputable |partial |unsafe )*"
    r"(?:theorem|lemma|def|abbrev|instance|structure|class|inductive|variable|open|import)\b)"
)
BINDER_RE = re.compile(r"^\s*(?:have|set|let|obtain)\s+([\w'⟨⟩,\s]+?)\s*[:←:=]")
IDENT_RE = re.compile(r"[A-Za-z_][\w'!?]*")


@dataclass(frozen=True)
class ProofMetric:
    name: str
    path: Path
    body_lines: int
    scaffold_lines: int
    extraction_costs: tuple[int, ...]

    @property
    def scaffold_percent(self) -> int:
        return 100 * self.scaffold_lines // self.body_lines if self.body_lines else 0

    def to_json(self, root: Path | None = None) -> dict[str, Any]:
        path = self.path
        if root:
            try:
                path = path.relative_to(root)
            except ValueError:
                pass
        costs = self.extraction_costs
        return {
            "name": self.name,
            "path": path.as_posix(),
            "body_lines": self.body_lines,
            "scaffold_lines": self.scaffold_lines,
            "scaffold_percent": self.scaffold_percent,
            "extractable_steps": len(costs),
            "median_extraction_cost": statistics.median(costs) if costs else None,
            "max_extraction_cost": max(costs) if costs else None,
        }


def _proofs_in(path: Path, scaffold_re: re.Pattern[str]) -> list[ProofMetric]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[ProofMetric] = []
    index = 0
    while index < len(lines):
        match = PROOF_DECL_RE.match(lines[index])
        if not match:
            index += 1
            continue
        start = index
        body_start = None
        cursor = index
        while cursor < len(lines):
            if ":=" in lines[cursor]:
                body_start = cursor + 1 if lines[cursor].rstrip().endswith((":=", "by")) else cursor
                break
            if cursor > start and PROOF_BOUNDARY_RE.match(lines[cursor]):
                break
            cursor += 1
        if body_start is None:
            index = start + 1
            continue
        end = body_start
        while end < len(lines) and not PROOF_BOUNDARY_RE.match(lines[end]):
            end += 1
        body = lines[body_start:end]
        nonempty = [line for line in body if line.strip()]
        scaffold = 0
        inside = False
        for line in body:
            if not line.strip():
                continue
            if scaffold_re.match(line):
                inside = True
                scaffold += 1
                continue
            if inside and line.startswith(("      ", "\t\t")) and not line.lstrip().startswith("·"):
                scaffold += 1
                continue
            inside = False
        known: set[str] = set()
        costs: list[int] = []
        i = 0
        while i < len(nonempty):
            binder = BINDER_RE.match(nonempty[i])
            if not binder:
                i += 1
                continue
            indent = len(nonempty[i]) - len(nonempty[i].lstrip())
            j = i + 1
            while j < len(nonempty) and (len(nonempty[j]) - len(nonempty[j].lstrip())) > indent:
                j += 1
            block = " ".join(nonempty[i:j])
            if j - i > 1:
                costs.append(len({word for word in IDENT_RE.findall(block) if word in known}))
            known.update(IDENT_RE.findall(binder.group(1)))
            i = j
        out.append(ProofMetric(match.group("name"), path, len(nonempty), scaffold, tuple(costs)))
        index = max(end, start + 1)
    return out


def proof_length_census(
    root: str | pathlib.Path,
    *,
    roots: Sequence[str] = (),
    minimum: int = 50,
    scaffold_definition: str = "published",
    exclude_parts: Iterable[str] = ("Experimental", ".lake", ".git", "build", "vendor", "external"),
) -> list[ProofMetric]:
    base = Path(root).expanduser().resolve()
    if scaffold_definition not in SCAFFOLD_DEFNS:
        raise ValueError(f"unknown scaffold definition {scaffold_definition!r}")
    scaffold_re = re.compile(SCAFFOLD_DEFNS[scaffold_definition])
    excluded = set(exclude_parts)
    start_paths = [base / r for r in roots] if roots else [base]
    rows: list[ProofMetric] = []
    for start in start_paths:
        if not start.exists():
            continue
        for path in sorted(start.rglob("*.lean")):
            try:
                rel = path.relative_to(base)
            except ValueError:
                continue
            if excluded.intersection(rel.parts):
                continue
            rows.extend(metric for metric in _proofs_in(path, scaffold_re) if metric.body_lines >= minimum)
    return sorted(rows, key=lambda row: (-row.body_lines, row.path.as_posix(), row.name))


def admission_report(index: LeanSourceIndex) -> list[dict[str, Any]]:
    closure = index.admission_closure()
    rows = []
    for module, path in sorted(index.modules.items()):
        rows.append({
            "module": module,
            "path": path.relative_to(index.root).as_posix(),
            "has_own_admission": module in index.admitted_modules,
            "admission_closure": sorted(closure.get(module, ())),
        })
    return rows


def undocumented_public(
    index: LeanSourceIndex,
    *,
    roots: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> list[SourceDecl]:
    return [
        row for row in index.declarations
        if not row.private and not row.documented and module_in_scope(row.module, roots, exclude)
    ]


def declaration_snapshot(index: LeanSourceIndex) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "root": str(index.root),
        "declarations": [row.to_json(index.root) for row in sorted(index.named_declarations, key=lambda r: (r.name, r.module, r.line)) if not row.private],
    }


def write_declaration_snapshot(index: LeanSourceIndex, path: Path) -> None:
    atomic_write_json(path, declaration_snapshot(index))


def declaration_drift(index: LeanSourceIndex, baseline: Mapping[str, Any]) -> dict[str, Any]:
    old_rows = baseline.get("declarations", []) if isinstance(baseline, Mapping) else []
    old = {str(row.get("name")): row for row in old_rows if isinstance(row, Mapping) and row.get("name")}
    new = {row.name: row for row in index.named_declarations if not row.private}
    removed = sorted(set(old) - set(new))
    added = sorted(set(new) - set(old))
    moved = []
    for name in sorted(set(old) & set(new)):
        row = new[name]
        old_module = old[name].get("module")
        if old_module and old_module != row.module:
            moved.append({"name": name, "from": old_module, "to": row.module})
    # Candidate renames: disappeared and appeared declarations sharing a final
    # component.  This is deliberately a hint rather than a semantic assertion.
    added_by_short: dict[str, list[str]] = collections.defaultdict(list)
    for name in added:
        added_by_short[name.rsplit(".", 1)[-1]].append(name)
    rename_hints = []
    for name in removed:
        short = name.rsplit(".", 1)[-1]
        for candidate in added_by_short.get(short, []):
            rename_hints.append({"from": name, "to": candidate})
    return {"removed": removed, "added": added, "moved": moved, "rename_hints": rename_hints}


def source_audit_summary(index: LeanSourceIndex) -> dict[str, Any]:
    duplicates = index.duplicate_public_names()
    closure = index.admission_closure()
    undocumented = undocumented_public(index)
    return {
        "root": str(index.root),
        "modules": len(index.modules),
        "declarations": len(index.declarations),
        "public_declarations": sum(not row.private for row in index.declarations),
        "duplicate_public_names": len(duplicates),
        "modules_with_own_admission": len(index.admitted_modules),
        "modules_in_admission_closure": len(closure),
        "undocumented_public_declarations": len(undocumented),
    }

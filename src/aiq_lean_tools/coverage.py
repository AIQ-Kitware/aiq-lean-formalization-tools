"""Cross-linked source-fidelity atoms and formalization result inventories.

The Davis--Kahan audit separated fine-grained source fidelity from the hard
formalization denominator.  This module keeps that distinction generic:

* source atoms record everything that must be accounted for while reading the
  source faithfully;
* result rows identify the subset that counts as formalization obligations;
* a coverage bundle validates the cross-links without imposing one paper's
  result taxonomy or terminal-status vocabulary.
"""
from __future__ import annotations

import collections
import html
import json
import pathlib
from dataclasses import dataclass
from importlib import resources
from typing import Any, Mapping, Sequence

from .common import Finding, Path, atomic_write_json, dotted_delete, dotted_set, infer_artifact_root, load_json, md_escape
from .errors import ValidationError
from .lean_source import LeanSourceIndex, scan_lean_project


class CoverageValidationError(ValidationError):
    pass


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and bool(item) for item in value)


def _counter(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    return dict(collections.Counter(str(row[field]) for row in rows if row.get(field) is not None))


@dataclass
class SourceAtomInventoryDocument:
    path: Path
    data: dict[str, Any]
    root: Path

    @property
    def atoms(self) -> list[dict[str, Any]]:
        value = self.data.get("atoms")
        return value if isinstance(value, list) else []

    def atom(self, atom_id: str) -> dict[str, Any]:
        matches = [row for row in self.atoms if row.get("id") == atom_id]
        if not matches:
            raise CoverageValidationError(f"no source atom with id {atom_id!r}")
        if len(matches) > 1:
            raise CoverageValidationError(f"duplicate source atom id {atom_id!r}")
        return matches[0]

    def validate(self) -> list[Finding]:
        out: list[Finding] = []
        raw = self.data.get("atoms")
        if not isinstance(raw, list) or not raw:
            return [Finding("error", "atoms", "atoms must be a non-empty list")]
        seen: set[str] = set()
        orders: set[int] = set()
        previous_order: int | None = None
        for index, atom in enumerate(raw):
            loc = f"atoms[{index}]"
            if not isinstance(atom, dict):
                out.append(Finding("error", "atom-type", "atom must be an object", loc))
                continue
            atom_id = atom.get("id")
            if not isinstance(atom_id, str) or not atom_id.strip():
                out.append(Finding("error", "atom-id", "atom id must be a non-empty string", loc))
                atom_id = loc
            elif atom_id in seen:
                out.append(Finding("error", "duplicate-atom-id", f"duplicate atom id {atom_id!r}", loc))
            seen.add(str(atom_id))
            loc = str(atom_id)

            order = atom.get("order")
            if order is not None:
                if not isinstance(order, int) or order < 1:
                    out.append(Finding("error", "atom-order", "order must be a positive integer", loc))
                else:
                    if order in orders:
                        out.append(Finding("error", "duplicate-atom-order", f"duplicate source order {order}", loc))
                    orders.add(order)
                    if previous_order is not None and order < previous_order:
                        out.append(Finding("warning", "atom-order-list", "atom list is not in source-order order", loc))
                    previous_order = order

            refs = atom.get("formalization_result_ids", [])
            if not _string_list(refs):
                out.append(Finding("error", "atom-result-refs", "formalization_result_ids must be a list of names", loc))
            elif len(refs) != len(set(refs)):
                out.append(Finding("error", "atom-result-refs", "formalization_result_ids contains duplicates", loc))

            support = atom.get("interpretation_support")
            if support is not None:
                if not isinstance(support, Mapping):
                    out.append(Finding("error", "interpretation-support", "interpretation_support must be an object", loc))
                elif not _string_list(support.get("result_ids", [])):
                    out.append(Finding("error", "interpretation-support", "interpretation_support.result_ids must be a list of names", loc))
        return out

    def summary(self) -> dict[str, Any]:
        rows = self.atoms
        result_refs = {
            value
            for row in rows
            for value in row.get("formalization_result_ids", [])
            if isinstance(value, str)
        }
        return {
            "path": str(self.path),
            "atoms": len(rows),
            "kinds": _counter(rows, "kind"),
            "source_roles": _counter(rows, "source_role"),
            "formalization_roles": _counter(rows, "formalization_role"),
            "referenced_results": len(result_refs),
        }

    def patch_atom(self, atom_id: str, sets: Sequence[tuple[str, Any]], deletes: Sequence[str] = ()) -> None:
        row = self.atom(atom_id)
        for field, value in sets:
            dotted_set(row, field, value)
        for field in deletes:
            dotted_delete(row, field)

    def write(self, path: Path | None = None) -> Path:
        target = path or self.path
        atomic_write_json(target, self.data)
        return target


@dataclass
class ResultInventoryDocument:
    path: Path
    data: dict[str, Any]
    root: Path

    @property
    def results(self) -> list[dict[str, Any]]:
        value = self.data.get("results", self.data.get("items"))
        return value if isinstance(value, list) else []

    def row(self, row_id: str) -> dict[str, Any]:
        matches = [row for row in self.results if row.get("id") == row_id]
        if not matches:
            raise CoverageValidationError(f"no result row with id {row_id!r}")
        if len(matches) > 1:
            raise CoverageValidationError(f"duplicate result row id {row_id!r}")
        return matches[0]

    def validate(self) -> list[Finding]:
        out: list[Finding] = []
        rows = self.data.get("results", self.data.get("items"))
        if not isinstance(rows, list) or not rows:
            return [Finding("error", "results", "results/items must be a non-empty list")]
        declared_count = self.data.get("result_count")
        if declared_count is not None and declared_count != len(rows):
            out.append(Finding("error", "result-count", f"result_count={declared_count!r} but document contains {len(rows)} rows"))

        seen: set[str] = set()
        semantic_present = any(isinstance(row, Mapping) and "semantic_certification" in row for row in rows)
        verification_present = any(isinstance(row, Mapping) and "verification" in row for row in rows)
        disposition_present = any(isinstance(row, Mapping) and ("disposition" in row or "status" in row) for row in rows)
        for index, row in enumerate(rows):
            loc = f"results[{index}]"
            if not isinstance(row, dict):
                out.append(Finding("error", "result-type", "result row must be an object", loc))
                continue
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id.strip():
                out.append(Finding("error", "result-id", "result id must be a non-empty string", loc))
                row_id = loc
            elif row_id in seen:
                out.append(Finding("error", "duplicate-result-id", f"duplicate result id {row_id!r}", loc))
            seen.add(str(row_id))
            loc = str(row_id)

            atom_ids = row.get("source_atom_ids", row.get("source_fidelity_atom_ids", []))
            if not _string_list(atom_ids) or not atom_ids:
                out.append(Finding("error", "source-atom-ids", "source_atom_ids must be a non-empty list of names", loc))
            elif len(atom_ids) != len(set(atom_ids)):
                out.append(Finding("error", "source-atom-ids", "source_atom_ids contains duplicates", loc))

            declarations = row.get("lean_declarations", row.get("review_declarations", []))
            if not _string_list(declarations):
                out.append(Finding("error", "result-declarations", "Lean declarations must be a list of names", loc))

            if row.get("completion_obligation", True):
                if disposition_present and not str(row.get("disposition", row.get("status", ""))).strip():
                    out.append(Finding("error", "result-disposition", "completion result needs a disposition/status", loc))
                if verification_present and not str(row.get("verification", "")).strip():
                    out.append(Finding("error", "result-verification", "completion result needs verification state", loc))
                if semantic_present and not str(row.get("semantic_certification", "")).strip():
                    out.append(Finding("error", "result-semantic", "completion result needs semantic certification", loc))
        return out

    @property
    def declaration_refs(self) -> list[str]:
        return list(dict.fromkeys(
            str(value)
            for row in self.results
            for value in row.get("lean_declarations", row.get("review_declarations", []))
            if isinstance(value, str)
        ))

    def static_declaration_findings(self, source_index: LeanSourceIndex | None = None) -> list[Finding]:
        index = source_index or scan_lean_project(self.root)
        out: list[Finding] = []
        for row in self.results:
            row_id = str(row.get("id", "?"))
            for name in row.get("lean_declarations", row.get("review_declarations", [])) or []:
                if not any(match.name == name for match in index.resolve(name)):
                    out.append(Finding("error", "declaration-missing", f"cited declaration not found in Lean sources: {name}", row_id))
        return out

    def summary(self) -> dict[str, Any]:
        rows = self.results
        return {
            "path": str(self.path),
            "results": len(rows),
            "completion_obligations": sum(row.get("completion_obligation", True) is not False for row in rows),
            "result_kinds": _counter(rows, "result_kind"),
            "dispositions": dict(collections.Counter(str(row.get("disposition", row.get("status"))) for row in rows if row.get("disposition", row.get("status")) is not None)),
            "verification": _counter(rows, "verification"),
            "semantic_certification": _counter(rows, "semantic_certification"),
            "semantic_alignment": _counter(rows, "semantic_alignment"),
            "unique_lean_declarations": len(self.declaration_refs),
        }

    def patch_item(self, row_id: str, sets: Sequence[tuple[str, Any]], deletes: Sequence[str] = ()) -> None:
        row = self.row(row_id)
        for field, value in sets:
            dotted_set(row, field, value)
        for field in deletes:
            dotted_delete(row, field)

    def write(self, path: Path | None = None) -> Path:
        target = path or self.path
        atomic_write_json(target, self.data)
        return target


@dataclass
class CoverageBundle:
    results: ResultInventoryDocument
    atoms: SourceAtomInventoryDocument | None = None

    def validate(self, *, static_declarations: bool = False, source_index: LeanSourceIndex | None = None) -> list[Finding]:
        out = [*self.results.validate()]
        if self.atoms is not None:
            out.extend(self.atoms.validate())
            atom_ids = {row.get("id") for row in self.atoms.atoms if isinstance(row.get("id"), str)}
            result_ids = {row.get("id") for row in self.results.results if isinstance(row.get("id"), str)}
            for row in self.results.results:
                row_id = str(row.get("id", "?"))
                for atom_id in row.get("source_atom_ids", row.get("source_fidelity_atom_ids", [])) or []:
                    if atom_id not in atom_ids:
                        out.append(Finding("error", "result-atom-missing", f"result references unknown source atom {atom_id}", row_id))
            for atom in self.atoms.atoms:
                atom_id = str(atom.get("id", "?"))
                refs = list(atom.get("formalization_result_ids", []) or [])
                support = atom.get("interpretation_support")
                if isinstance(support, Mapping):
                    refs.extend(support.get("result_ids", []) or [])
                for result_id in refs:
                    if result_id not in result_ids:
                        out.append(Finding("error", "atom-result-missing", f"source atom references unknown result {result_id}", atom_id))
        if static_declarations:
            out.extend(self.results.static_declaration_findings(source_index))
        return out

    def summary(self) -> dict[str, Any]:
        data = self.results.summary()
        data["source_atoms"] = self.atoms.summary() if self.atoms else None
        if self.atoms:
            result_ids = {row.get("id") for row in self.results.results}
            linked = {
                result_id
                for atom in self.atoms.atoms
                for result_id in atom.get("formalization_result_ids", []) or []
                if result_id in result_ids
            }
            data["results_backlinked_by_atoms"] = len(linked)
        return data

    def render_markdown(self) -> str:
        summary = self.summary()
        lines = [
            f"# Formalization coverage: {self.results.path.stem}",
            "",
            "The result inventory is the formalization denominator. The source-atom inventory, when present, is the finer source-fidelity accounting surface.",
            "",
            f"- Counted result rows: **{summary['results']}**",
            f"- Completion obligations: **{summary['completion_obligations']}**",
            f"- Unique Lean declarations cited: **{summary['unique_lean_declarations']}**",
        ]
        if self.atoms:
            lines += [f"- Source-fidelity atoms: **{len(self.atoms.atoms)}**"]
        lines += ["", "## Result states", ""]
        for field, label in (
            ("dispositions", "Disposition"),
            ("verification", "Verification"),
            ("semantic_certification", "Semantic certification"),
            ("semantic_alignment", "Semantic alignment"),
        ):
            values = summary.get(field, {})
            if values:
                lines += [f"### {label}", "", *[f"- `{key}`: {value}" for key, value in values.items()], ""]
        lines += ["## Counted results", ""]
        for row in self.results.results:
            lines += [f"### `{row.get('id', '?')}` — {row.get('title', row.get('source_anchor', ''))}", ""]
            if row.get("source_anchor"):
                lines += [f"**Source anchor.** {row['source_anchor']}", ""]
            lines += [
                f"**Disposition:** `{row.get('disposition', row.get('status', ''))}`",
                f"**Verification:** `{row.get('verification', '')}`",
                f"**Semantic certification:** `{row.get('semantic_certification', '')}`",
                "",
            ]
            atom_ids = row.get("source_atom_ids", row.get("source_fidelity_atom_ids", [])) or []
            if atom_ids:
                lines += ["**Source atoms:** " + ", ".join(f"`{md_escape(value)}`" for value in atom_ids), ""]
            declarations = row.get("lean_declarations", row.get("review_declarations", [])) or []
            if declarations:
                lines += ["**Lean declarations:**", "", *[f"- `{name}`" for name in declarations], ""]
            if row.get("review_note"):
                lines += [f"**Review note.** {row['review_note']}", ""]
        return "\n".join(lines).rstrip() + "\n"

    def render_html(self) -> str:
        payload = {
            "summary": self.summary(),
            "results_path": str(self.results.path),
            "atoms_path": str(self.atoms.path) if self.atoms else None,
            "results": self.results.results,
            "atoms": self.atoms.atoms if self.atoms else [],
        }
        template = resources.files("aiq_lean_tools").joinpath("assets/coverage_viewer.html").read_text(encoding="utf-8")
        title = f"Formalization coverage: {self.results.path.stem}"
        encoded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/").replace("<", "\\u003c")
        return template.replace("__TITLE__", html.escape(title)).replace("__PAYLOAD__", encoded)


def load_source_atom_inventory(path: str | pathlib.Path, *, root: str | pathlib.Path | None = None) -> SourceAtomInventoryDocument:
    p = Path(path).expanduser().resolve()
    data = load_json(p)
    if not isinstance(data, dict):
        raise CoverageValidationError(f"source-atom inventory root must be an object: {p}")
    return SourceAtomInventoryDocument(p, data, Path(root).expanduser().resolve() if root else infer_artifact_root(p))


def load_result_inventory(path: str | pathlib.Path, *, root: str | pathlib.Path | None = None) -> ResultInventoryDocument:
    p = Path(path).expanduser().resolve()
    data = load_json(p)
    if not isinstance(data, dict):
        raise CoverageValidationError(f"result inventory root must be an object: {p}")
    return ResultInventoryDocument(p, data, Path(root).expanduser().resolve() if root else infer_artifact_root(p))


def load_coverage_bundle(path: str | pathlib.Path, *, root: str | pathlib.Path | None = None) -> CoverageBundle:
    results = load_result_inventory(path, root=root)
    atom_ref = results.data.get("source_fidelity_inventory")
    atoms = None
    if isinstance(atom_ref, str) and atom_ref.strip():
        atom_path = Path(atom_ref)
        if not atom_path.is_absolute():
            atom_path = results.root / atom_path
        if atom_path.exists():
            atoms = load_source_atom_inventory(atom_path, root=results.root)
    return CoverageBundle(results, atoms)

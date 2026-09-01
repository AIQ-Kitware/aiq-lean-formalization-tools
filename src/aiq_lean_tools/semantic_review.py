from __future__ import annotations

import collections
import html
import json
import pathlib
from dataclasses import dataclass
from importlib import resources
from typing import Any, Mapping, Sequence

from .common import (
    Finding, Path, atomic_write_json, infer_artifact_root, load_json, md_escape,
    dotted_set, dotted_delete, validate_source_locator,
)
from .errors import ValidationError
from .statement_pins import validate_pins


class SemanticReviewValidationError(ValidationError):
    pass


@dataclass
class SemanticReviewDocument:
    path: Path
    data: dict[str, Any]
    root: Path

    @property
    def rows(self) -> list[dict[str, Any]]:
        rows = self.data.get("rows")
        return rows if isinstance(rows, list) else []

    @property
    def title(self) -> str:
        paper = self.data.get("paper")
        if isinstance(paper, Mapping):
            return str(paper.get("citation") or paper.get("title") or self.path.stem)
        return self.path.stem

    def row(self, row_id: str) -> dict[str, Any]:
        found = [row for row in self.rows if row.get("id") == row_id]
        if len(found) != 1:
            raise SemanticReviewValidationError(
                f"expected one semantic-review row {row_id!r}, found {len(found)}"
            )
        return found[0]

    def validate(self, *, check_companion: bool = True) -> list[Finding]:
        findings: list[Finding] = []
        raw_rows = self.data.get("rows")
        if not isinstance(raw_rows, list):
            return [Finding("error", "rows", "rows must be a list")]
        if not raw_rows:
            level = "warning" if self.data.get("draft") is True else "error"
            return [Finding(level, "rows", "rows is empty")]
        relations = set(self.data.get("relation_definitions", {}))
        ids: set[str] = set()
        for index, row in enumerate(self.rows):
            loc = f"rows[{index}]"
            if not isinstance(row, dict):
                findings.append(Finding("error", "row-type", "row must be an object", loc))
                continue
            rid = row.get("id")
            if not isinstance(rid, str) or not rid:
                findings.append(Finding("error", "id", "row requires a non-empty id", loc))
                continue
            if rid in ids:
                findings.append(Finding("error", "duplicate-id", f"duplicate id {rid!r}", rid))
            ids.add(rid)
            for field in ("source_anchor", "title"):
                if field in row and not str(row.get(field, "")).strip():
                    findings.append(Finding("error", field, f"{field} cannot be empty", rid))
            decls = row.get("lean_declarations", [])
            if not isinstance(decls, list) or not all(isinstance(x, str) and x for x in decls):
                findings.append(Finding("error", "lean-declarations", "lean_declarations must be a list", rid))
            clauses = row.get("clauses", [])
            if not isinstance(clauses, list):
                findings.append(Finding("error", "clauses", "clauses must be a list", rid))
                continue
            for cidx, clause in enumerate(clauses):
                cloc = f"{rid}.clauses[{cidx}]"
                if not isinstance(clause, dict):
                    findings.append(Finding("error", "clause-type", "clause must be an object", cloc))
                    continue
                for key in ("source_clause", "lean_clause", "relation"):
                    if not isinstance(clause.get(key), str) or not clause[key].strip():
                        findings.append(Finding("error", f"clause-{key}", f"{key} must be non-empty", cloc))
                relation = clause.get("relation")
                if relations and relation not in relations:
                    findings.append(Finding("error", "relation", f"unknown relation {relation!r}", cloc))
            locator = row.get("source_locator")
            if locator:
                findings.extend(validate_source_locator(locator, rid, self.root))
            findings.extend(
                validate_pins(
                    row,
                    claimed=[str(x) for x in decls] if isinstance(decls, list) else [],
                    location=rid,
                )
            )
        if check_companion:
            findings.extend(self._validate_against_companion())
        return findings

    def _validate_against_companion(self) -> list[Finding]:
        """A review and its companion census must not drift apart.

        The two documents answer different questions -- coverage and statement
        agreement -- about the same rows, so a row present in one and absent from
        the other, or citing a different locator or declaration list, means one of
        them is describing something that is no longer there.
        """
        reference = self.data.get("companion_census")
        if not isinstance(reference, str) or not reference:
            return []
        path = self.root / reference
        if not path.is_file():
            return [Finding("error", "companion-missing",
                            f"companion_census does not exist: {reference}")]
        try:
            census = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as ex:
            return [Finding("error", "companion-unreadable", f"{reference}: {ex}")]
        rows = census.get("items")
        if not isinstance(rows, list):
            return [Finding("error", "companion-items", f"{reference} has no items list")]
        by_id = {row.get("id"): row for row in rows if isinstance(row, dict)}
        findings: list[Finding] = []
        seen: set[str] = set()
        for row in self.rows:
            rid = row.get("id")
            if not isinstance(rid, str):
                continue
            seen.add(rid)
            companion = by_id.get(rid)
            if companion is None:
                findings.append(Finding("error", "companion-row",
                                        f"row is absent from {reference}", rid))
                continue
            if "source_locator" in row and row.get("source_locator") != companion.get("source_locator"):
                findings.append(Finding("error", "companion-locator",
                                        f"source_locator differs from {reference}", rid))
            if "lean_declarations" in row and row.get("lean_declarations") != companion.get("lean_declarations"):
                findings.append(Finding("error", "companion-declarations",
                                        f"lean_declarations differ from {reference}", rid))
        for rid in sorted(set(by_id) - seen):
            if isinstance(rid, str):
                findings.append(Finding("error", "companion-unreviewed",
                                        f"{reference} row is not reviewed here", rid))
        return findings

    def assert_valid(self) -> None:
        errors = [f for f in self.validate() if f.level == "error"]
        if errors:
            raise SemanticReviewValidationError("\n".join(f"{f.location}: [{f.code}] {f.message}" for f in errors))

    def summary(self) -> dict[str, Any]:
        relations = collections.Counter()
        verdicts = collections.Counter()
        importance = collections.Counter()
        covered = collections.Counter()
        decls: set[str] = set()
        for row in self.rows:
            verdicts[str(row.get("verdict", ""))] += 1
            if row.get("importance"):
                importance[str(row["importance"])] += 1
            if "literal_source_covered" in row:
                covered[str(bool(row["literal_source_covered"]))] += 1
            decls.update(str(x) for x in row.get("lean_declarations", []) or [])
            for clause in row.get("clauses", []) or []:
                if isinstance(clause, dict) and clause.get("relation"):
                    relations[str(clause["relation"])] += 1
        return {
            "path": str(self.path),
            "title": self.title,
            "rows": len(self.rows),
            "relations": dict(relations),
            "verdicts": dict(verdicts),
            "importance": dict(importance),
            "literal_source_covered": dict(covered),
            "unique_lean_declarations": len(decls),
        }

    def patch_row(self, row_id: str, sets: Sequence[tuple[str, Any]], deletes: Sequence[str] = ()) -> None:
        row = self.row(row_id)
        for field, value in sets:
            dotted_set(row, field, value)
        for field in deletes:
            dotted_delete(row, field)

    def append_row(self, row: Mapping[str, Any]) -> None:
        item = dict(row)
        rid = item.get("id")
        if not isinstance(rid, str) or not rid:
            raise SemanticReviewValidationError("new row requires a non-empty id")
        if any(existing.get("id") == rid for existing in self.rows):
            raise SemanticReviewValidationError(f"semantic review already has row {rid!r}")
        self.rows.append(item)
        if self.data.get("draft") is True:
            self.data["draft"] = False

    def write(self, path: Path | None = None) -> Path:
        target = path or self.path
        atomic_write_json(target, self.data)
        return target

    def render_markdown(self) -> str:
        out = [
            "<!-- generated by aiq-lean-tools; edit the semantic-review JSON -->",
            "",
            f"# {self.title} — semantic alignment review",
            "",
        ]
        if self.data.get("purpose"):
            out += [str(self.data["purpose"]), ""]
        if self.data.get("companion_census"):
            out += [f"**Companion census:** `{self.data['companion_census']}`", ""]
        summary = self.summary()
        out += [f"**Rows:** {summary['rows']}  ", f"**Unique cited Lean declarations:** {summary['unique_lean_declarations']}", ""]
        if summary["relations"]:
            out += ["## Clause relation summary", "", "| relation | clauses |", "| --- | ---: |"]
            for key, value in summary["relations"].items():
                out.append(f"| `{md_escape(key)}` | {value} |")
            out.append("")
        out += ["## Results", ""]
        for row in self.rows:
            out += [f"### `{row.get('id','?')}` — {row.get('title','')}", ""]
            facts = []
            for key in ("source_anchor", "source_kind", "importance", "verdict"):
                if row.get(key) is not None:
                    facts.append(f"**{key.replace('_',' ')}:** {row[key]}")
            if "literal_source_covered" in row:
                facts.append(f"**literal source covered:** {bool(row['literal_source_covered'])}")
            if facts:
                out += ["  ".join(facts), ""]
            if row.get("source_claim"):
                out += [f"**Source claim.** {row['source_claim']}", ""]
            decls = row.get("lean_declarations", []) or []
            if decls:
                out += ["**Lean declarations:**", "", *[f"- `{x}`" for x in decls], ""]
            clauses = row.get("clauses", []) or []
            if clauses:
                out += ["| source clause | Lean clause | relation | note |", "| --- | --- | --- | --- |"]
                for clause in clauses:
                    out.append("| " + " | ".join(md_escape(clause.get(k, "")) for k in ("source_clause", "lean_clause", "relation", "note")) + " |")
                out.append("")
            for key, label in (("review", "Review"), ("notes", "Notes"), ("next_action", "Next action")):
                if row.get(key):
                    out += [f"**{label}.** {row[key]}", ""]
            if row.get("gap_refs"):
                out += ["**Gap refs:** " + ", ".join(f"`{x}`" for x in row["gap_refs"]), ""]
        return "\n".join(out).rstrip() + "\n"

    def render_html(self) -> str:
        template = resources.files("aiq_lean_tools").joinpath("assets/review_viewer.html").read_text(encoding="utf-8")
        payload = json.dumps({"title": self.title, "data": self.data}, ensure_ascii=False).replace("<", "\\u003c")
        return template.replace("__TITLE__", html.escape(self.title)).replace("__PAYLOAD__", payload)


def load_semantic_review(path: str | pathlib.Path, *, root: str | pathlib.Path | None = None) -> SemanticReviewDocument:
    p = Path(path).expanduser().resolve()
    data = load_json(p)
    if not isinstance(data, dict):
        raise SemanticReviewValidationError(f"semantic review root must be an object: {p}")
    return SemanticReviewDocument(p, data, Path(root).expanduser().resolve() if root else infer_artifact_root(p))

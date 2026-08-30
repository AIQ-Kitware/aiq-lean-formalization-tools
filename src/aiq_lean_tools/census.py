from __future__ import annotations

import collections
import html
import json
import pathlib
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Iterable, Mapping, Sequence

from .common import (
    Finding,
    Path,
    atomic_write_json,
    infer_artifact_root,
    load_json,
    md_escape,
    parse_scalar,
    dotted_set,
    dotted_delete,
    unique_in_order,
)
from .errors import ValidationError
from .lean_backend import DeclarationProbe, LeanBackend, SubprocessLeanBackend
from .lean_source import LeanSourceIndex, scan_lean_project
from .semantic_surface import validate_embedded_surface

KNOWN_IMPORTANCE = ("headline", "major", "supporting", "technical")
PROVED_STATES = {"proved_in_build", "proved_outside_build", "proved_conditional", "partially_in_build"}
ABSENT_STATES = {"absent", "not_represented"}


class CensusValidationError(ValidationError):
    pass


@dataclass(frozen=True)
class ProbeSummary:
    results: tuple[DeclarationProbe, ...]
    imports: tuple[str, ...]
    #: Unresolved names that a `private` declaration of the same short name
    #: exists for, mapped to the module declaring it.  "Gone" and "present but
    #: private" look identical to a `#check` and mean opposite things: a missing
    #: name is lost mathematics, a private name is mathematics that exists and
    #: cannot be cited as evidence.
    private_declarations: Mapping[str, str] = field(default_factory=dict)

    @property
    def unresolved(self) -> list[str]:
        return [row.name for row in self.results if not row.resolved]

    @property
    def resolved(self) -> list[str]:
        return [row.name for row in self.results if row.resolved]

    def to_json(self) -> dict[str, Any]:
        return {
            "imports": list(self.imports),
            "resolved": self.resolved,
            "unresolved": self.unresolved,
            "private_declarations": dict(self.private_declarations),
            "results": [
                {"name": row.name, "resolved": row.resolved, "output": row.output}
                for row in self.results
            ],
        }


@dataclass
class CensusDocument:
    path: Path
    data: dict[str, Any]
    root: Path

    @property
    def items(self) -> list[dict[str, Any]]:
        rows = self.data.get("items")
        return rows if isinstance(rows, list) else []

    @property
    def family(self) -> str:
        if "semantic_alignment_definitions" in self.data:
            return "source-semantic-alignment"
        if "completion_certification_definitions" in self.data:
            return "source-completion-census"
        if "status_definitions" in self.data and "verification_definitions" in self.data:
            return "source-census"
        return "generic-census"

    @property
    def title(self) -> str:
        paper = self.data.get("paper")
        if isinstance(paper, Mapping):
            return str(paper.get("citation") or paper.get("title") or self.path.stem)
        source = self.data.get("primary_source")
        if isinstance(source, Mapping):
            return str(source.get("citation") or self.path.stem)
        return self.path.stem

    @property
    def declaration_refs(self) -> list[str]:
        refs: list[str] = []
        for row in self.items:
            refs.extend(str(x) for x in row.get("lean_declarations", []) if isinstance(x, str))
        return unique_in_order(refs)

    @property
    def planned_declaration_refs(self) -> list[str]:
        refs: list[str] = []
        for row in self.items:
            refs.extend(str(x) for x in row.get("planned_declarations", []) if isinstance(x, str))
        return unique_in_order(refs)

    def row(self, row_id: str) -> dict[str, Any]:
        found = [row for row in self.items if row.get("id") == row_id]
        if not found:
            raise CensusValidationError(f"no census row with id {row_id!r}")
        if len(found) > 1:
            raise CensusValidationError(f"duplicate census row id {row_id!r}")
        return found[0]

    def validate(self, *, check_source_locations: bool = True) -> list[Finding]:
        findings: list[Finding] = []
        items = self.data.get("items")
        if not isinstance(items, list):
            findings.append(Finding("error", "items", "items must be a list"))
            return findings
        if not items:
            level = "warning" if self.data.get("draft") is True else "error"
            findings.append(Finding(level, "items", "items is empty"))
            return findings

        status_defs = self.data.get("status_definitions", {})
        verification_defs = self.data.get("verification_definitions", {})
        alignment_defs = self.data.get("semantic_alignment_definitions", {})
        importance_defs = self.data.get("importance_definitions", {})
        gaps = self.data.get("gaps", {})
        blockers = self.data.get("blockers", {})
        certification_defs = self.data.get("completion_certification_definitions", {})

        embedded_review_mode = (
            self.data.get("embedded_semantic_review") is True
            or (
                self.family != "source-semantic-alignment"
                and isinstance(importance_defs, Mapping)
                and set(KNOWN_IMPORTANCE).issubset(importance_defs)
            )
            or any(
                isinstance(row, dict)
                and row.get("importance") == "headline"
                and isinstance(row.get("semantic_review"), dict)
                for row in items
            )
        )

        seen: set[str] = set()
        used_gaps: set[str] = set()
        used_blockers: set[str] = set()
        for index, row in enumerate(items):
            loc = f"items[{index}]"
            if not isinstance(row, dict):
                findings.append(Finding("error", "row-type", "row must be an object", loc))
                continue
            rid = row.get("id")
            if not isinstance(rid, str) or not rid.strip():
                findings.append(Finding("error", "id", "row id must be a non-empty string", loc))
                rid = f"#{index}"
            elif rid in seen:
                findings.append(Finding("error", "duplicate-id", f"duplicate row id {rid!r}", loc))
            seen.add(str(rid))
            rloc = str(rid)

            for key in ("title",):
                if key in row and (not isinstance(row[key], str) or not row[key].strip()):
                    findings.append(Finding("error", key, f"{key} must be a non-empty string", rloc))

            status = row.get("status")
            if status_defs and status not in status_defs:
                findings.append(Finding("error", "status", f"unknown status {status!r}", rloc))
            verification = row.get("verification")
            if verification_defs and verification not in verification_defs:
                findings.append(Finding("error", "verification", f"unknown verification {verification!r}", rloc))
            importance = row.get("importance")
            if importance is not None:
                known = set(importance_defs or KNOWN_IMPORTANCE)
                if known and importance not in known:
                    findings.append(Finding("error", "importance", f"unknown importance {importance!r}", rloc))

            decls = row.get("lean_declarations", [])
            if not isinstance(decls, list) or not all(isinstance(x, str) and x for x in decls):
                findings.append(Finding("error", "lean-declarations", "lean_declarations must be a list of names", rloc))
                decls = []
            planned = row.get("planned_declarations", [])
            if planned is not None and (
                not isinstance(planned, list) or not all(isinstance(x, str) and x for x in planned)
            ):
                findings.append(Finding("error", "planned-declarations", "planned_declarations must be a list", rloc))

            if verification == "absent" and decls:
                findings.append(Finding("error", "verification-citations", "verification is absent but declarations are cited", rloc))
            if verification == "proved_in_build" and not decls:
                findings.append(Finding("error", "verification-citations", "proved_in_build row cites no Lean declaration", rloc))
            if isinstance(status, str) and status.startswith("compiled") and verification == "absent":
                findings.append(Finding("error", "compiled-absent", f"status {status!r} conflicts with absent verification", rloc))

            for ref in row.get("gap_refs", []) or []:
                if not isinstance(ref, str) or ref not in gaps:
                    findings.append(Finding("error", "gap-ref", f"unknown gap reference {ref!r}", rloc))
                else:
                    used_gaps.add(ref)
            for ref in row.get("blocked_by", []) or []:
                if not isinstance(ref, str) or ref not in blockers:
                    findings.append(Finding("error", "blocker-ref", f"unknown blocker reference {ref!r}", rloc))
                else:
                    used_blockers.add(ref)

            semantic = row.get("semantic_alignment")
            if semantic is not None:
                if not isinstance(semantic, dict):
                    findings.append(Finding("error", "semantic-alignment", "semantic_alignment must be an object", rloc))
                else:
                    cls = semantic.get("classification")
                    if alignment_defs and cls not in alignment_defs:
                        findings.append(Finding("error", "semantic-classification", f"unknown semantic alignment {cls!r}", rloc))
                    if cls and not str(semantic.get("detail", "")).strip():
                        findings.append(Finding("error", "semantic-detail", "semantic alignment needs detail text", rloc))

            cert = row.get("completion_certification")
            if cert is not None and certification_defs and cert not in certification_defs:
                findings.append(Finding("error", "completion-certification", f"unknown completion certification {cert!r}", rloc))

            source_locator = row.get("source_locator")
            if source_locator is not None:
                findings.extend(self._validate_source_locator(source_locator, rloc, check_source_locations))

            # The DK/YWS source censuses used an embedded curated review surface.
            # Application-style source/semantic-alignment censuses keep their
            # clause review in a companion document, so only source/completion
            # censuses with an explicit importance vocabulary require headline
            # rows to carry the embedded contract.
            require_headline_review = (
                embedded_review_mode
                and self.family != "source-semantic-alignment"
                and row.get("importance") == "headline"
            )
            findings.extend(
                validate_embedded_surface(
                    row,
                    row_location=rloc,
                    require_headline_review=require_headline_review,
                )
            )

        for gap in sorted(set(gaps) - used_gaps):
            findings.append(Finding("warning", "orphan-gap", f"gap {gap!r} is not referenced by any row"))
        for blocker in sorted(set(blockers) - used_blockers):
            findings.append(Finding("warning", "orphan-blocker", f"blocker {blocker!r} is not referenced by any row"))
        return findings

    def _validate_source_locator(self, locator: Any, row_id: str, check_source_locations: bool) -> list[Finding]:
        findings: list[Finding] = []
        if not isinstance(locator, dict):
            return [Finding("error", "source-locator", "source_locator must be an object", row_id)]
        file = locator.get("file")
        lines = locator.get("lines")
        if not isinstance(file, str) or not file:
            findings.append(Finding("error", "source-file", "source_locator.file must be a path", row_id))
            return findings
        if not (
            isinstance(lines, list) and len(lines) == 2 and all(isinstance(x, int) and x > 0 for x in lines) and lines[0] <= lines[1]
        ):
            findings.append(Finding("error", "source-lines", "source_locator.lines must be [start, end] positive integers", row_id))
            return findings
        if check_source_locations:
            source = self.root / file
            if not source.is_file():
                findings.append(Finding("error", "source-missing", f"source locator file does not exist: {file}", row_id))
            else:
                total = len(source.read_text(encoding="utf-8", errors="replace").splitlines())
                if lines[1] > total:
                    findings.append(Finding("error", "source-range", f"source range {lines[0]}-{lines[1]} exceeds {file} ({total} lines)", row_id))
        return findings

    def assert_valid(self, *, check_source_locations: bool = True) -> None:
        errors = [f for f in self.validate(check_source_locations=check_source_locations) if f.level == "error"]
        if errors:
            text = "\n".join(f"{f.location or self.path}: [{f.code}] {f.message}" for f in errors)
            raise CensusValidationError(text)

    def static_declaration_findings(self, source_index: LeanSourceIndex | None = None) -> list[Finding]:
        index = source_index or scan_lean_project(self.root)
        findings: list[Finding] = []
        for row in self.items:
            rid = str(row.get("id", "?"))
            for ref in row.get("lean_declarations", []) or []:
                matches = index.resolve(ref)
                exact = [m for m in matches if m.name == ref]
                if not exact:
                    if matches:
                        shown = ", ".join(m.name for m in matches[:4])
                        findings.append(Finding("warning", "declaration-not-exact", f"{ref!r} did not resolve exactly; short-name candidates: {shown}", rid))
                    else:
                        findings.append(Finding("error", "declaration-missing", f"cited declaration not found in Lean sources: {ref}", rid))
            for ref in row.get("planned_declarations", []) or []:
                if any(m.name == ref for m in index.resolve(ref)):
                    findings.append(Finding("error", "planned-now-exists", f"planned declaration already exists: {ref}", rid))
        return findings

    def probe(
        self,
        *,
        backend: LeanBackend | None = None,
        imports: Sequence[str] = (),
        source_index: LeanSourceIndex | None = None,
        timeout: int = 3600,
    ) -> ProbeSummary:
        refs = self.declaration_refs
        source_index = source_index or scan_lean_project(self.root)
        inferred = list(imports)
        if not inferred:
            for ref in refs:
                exact = [row for row in source_index.resolve(ref) if row.name == ref]
                inferred.extend(row.module for row in exact)
            # Generated names may not appear textually.  Import the top-level root
            # when it exists so structure-generated declarations can still resolve.
            for ref in refs:
                prefix = ref.split(".", 1)[0]
                if (self.root / f"{prefix}.lean").exists():
                    inferred.append(prefix)
        inferred = unique_in_order(inferred)
        if refs and not inferred:
            raise CensusValidationError(
                "could not infer any Lean imports for the census; pass --import MODULE"
            )
        runner = backend or SubprocessLeanBackend()
        results = runner.probe_declarations(self.root, refs, inferred, timeout=timeout)
        unresolved = {row.name for row in results if not row.resolved}
        private: dict[str, str] = {}
        if unresolved:
            by_short = source_index.by_short_name
            for name in sorted(unresolved):
                rows = [row for row in by_short.get(name.rsplit(".", 1)[-1], ()) if row.private]
                if rows:
                    private[name] = rows[0].module
        return ProbeSummary(tuple(results), tuple(inferred), private)

    def apply_probe(self, probe: ProbeSummary) -> int:
        """Refresh verification values where the document defines compatible states."""
        defined = set(self.data.get("verification_definitions", {}))
        if not defined:
            return 0
        resolved = {row.name: row.resolved for row in probe.results}
        changed = 0
        for row in self.items:
            refs = [r for r in row.get("lean_declarations", []) or [] if r in resolved]
            if not refs or row.get("verification") == "not_applicable":
                continue
            count = sum(resolved[r] for r in refs)
            if count == len(refs) and "proved_in_build" in defined:
                new = "proved_in_build"
            elif count and "partially_in_build" in defined:
                new = "partially_in_build"
            elif count == 0 and "absent" in defined:
                new = "absent"
            else:
                continue
            if row.get("verification") != new:
                row["verification"] = new
                changed += 1
        return changed

    def summary(self) -> dict[str, Any]:
        def count(field: str) -> dict[str, int]:
            return dict(collections.Counter(str(row[field]) for row in self.items if row.get(field) is not None))

        align = collections.Counter()
        cited = collections.Counter()
        for row in self.items:
            sem = row.get("semantic_alignment")
            if isinstance(sem, dict) and sem.get("classification"):
                align[str(sem["classification"])] += 1
            for ref in row.get("lean_declarations", []) or []:
                cited[str(ref)] += 1
        return {
            "path": str(self.path),
            "family": self.family,
            "title": self.title,
            "items": len(self.items),
            "status": count("status"),
            "verification": count("verification"),
            "importance": count("importance"),
            "semantic_alignment": dict(align),
            "unique_lean_declarations": len(cited),
            "reused_within_census": sorted((name, n) for name, n in cited.items() if n > 1),
            "gaps": len(self.data.get("gaps", {})),
            "blockers": len(self.data.get("blockers", {})),
        }

    def patch_item(self, row_id: str, sets: Sequence[tuple[str, Any]], deletes: Sequence[str] = ()) -> None:
        row = self.row(row_id)
        for field, value in sets:
            dotted_set(row, field, value)
        for field in deletes:
            dotted_delete(row, field)

    def append_item(self, row: Mapping[str, Any]) -> None:
        item = dict(row)
        rid = item.get("id")
        if not isinstance(rid, str) or not rid:
            raise CensusValidationError("new item requires a non-empty id")
        if any(existing.get("id") == rid for existing in self.items):
            raise CensusValidationError(f"census already has row {rid!r}")
        self.items.append(item)
        if self.data.get("draft") is True:
            self.data["draft"] = False

    def write(self, path: Path | None = None) -> Path:
        target = path or self.path
        atomic_write_json(target, self.data)
        return target

    def render_markdown(self) -> str:
        summary = self.summary()
        out = [
            "<!-- generated by aiq-lean-tools; edit the JSON census, not this Markdown view -->",
            "",
            f"# {self.title}",
            "",
            f"**Census family:** `{self.family}`  ",
            f"**Items:** {summary['items']}  ",
            f"**Unique cited Lean declarations:** {summary['unique_lean_declarations']}",
            "",
        ]
        if self.data.get("scope"):
            out += [str(self.data["scope"]), ""]
        if self.data.get("audit_revision"):
            out += [f"**Audit revision:** {self.data['audit_revision']}", ""]
        if self.data.get("how_to_use"):
            out += ["## How to use this census", "", str(self.data["how_to_use"]), ""]
        if self.data.get("notes") and isinstance(self.data.get("notes"), str):
            out += ["## Audit note", "", str(self.data["notes"]), ""]

        for title, field in (
            ("Status summary", "status"),
            ("Verification summary", "verification"),
            ("Importance summary", "importance"),
            ("Semantic-alignment summary", "semantic_alignment"),
        ):
            counts = summary[field]
            if counts:
                out += [f"## {title}", "", "| value | items |", "| --- | ---: |"]
                for key, value in counts.items():
                    out.append(f"| `{md_escape(key)}` | {value} |")
                out.append("")

        if self.data.get("gaps"):
            out += ["## Gaps", ""]
            for key, value in self.data["gaps"].items():
                detail = value.get("description") if isinstance(value, dict) else value
                out += [f"### `{key}`", "", str(detail or value), ""]
        if self.data.get("blockers"):
            out += ["## Blockers", ""]
            for key, value in self.data["blockers"].items():
                detail = value.get("description") if isinstance(value, dict) else value
                out += [f"### `{key}`", "", str(detail or value), ""]

        columns = ["id", "importance", "source_anchor", "status", "verification", "alignment"]
        out += ["## Result table", "", "| " + " | ".join(columns) + " |", "|" + " --- |" * len(columns)]
        for row in self.items:
            sem = row.get("semantic_alignment")
            alignment = sem.get("classification", "") if isinstance(sem, dict) else ""
            values = [row.get("id", ""), row.get("importance", ""), row.get("source_anchor", ""), row.get("status", ""), row.get("verification", ""), alignment]
            out.append("| " + " | ".join(md_escape(v) for v in values) + " |")
        out.append("")

        out += ["## Result details", ""]
        for row in self.items:
            rid = row.get("id", "unnamed")
            title = row.get("title") or row.get("source_anchor") or ""
            out += [f"### `{rid}` — {title}", ""]
            facts: list[str] = []
            for key, label in (
                ("importance", "importance"), ("section", "section"),
                ("source_anchor", "source"), ("source_kind", "kind"),
                ("status", "status"), ("verification", "verification"),
                ("completion_certification", "completion"),
            ):
                if row.get(key) is not None:
                    facts.append(f"**{label}:** `{row[key]}`" if key in {"importance", "status", "verification", "completion_certification"} else f"**{label}:** {row[key]}")
            if facts:
                out += ["  ".join(facts), ""]
            for key, label in (("summary", "Summary"), ("source_claim", "Source claim"), ("scope_gap", "Scope gap")):
                if row.get(key):
                    out += [f"**{label}.** {row[key]}", ""]
            loc = row.get("source_locator")
            if isinstance(loc, dict) and loc.get("file"):
                lines = loc.get("lines")
                suffix = f":{lines[0]}-{lines[1]}" if isinstance(lines, list) and len(lines) == 2 else ""
                out += [f"**Source locator:** `{loc['file']}{suffix}`", ""]
            decls = row.get("lean_declarations", []) or []
            if decls:
                out += ["**Lean declarations:**", "", *[f"- `{decl}`" for decl in decls], ""]
            planned = row.get("planned_declarations", []) or []
            if planned:
                out += ["**Planned declarations:**", "", *[f"- `{decl}`" for decl in planned], ""]
            sem = row.get("semantic_alignment")
            if isinstance(sem, dict):
                out += [f"**Semantic alignment:** `{sem.get('classification', '')}` — {sem.get('detail', '')}", ""]
            review = row.get("semantic_review")
            if isinstance(review, dict):
                out += _render_embedded_semantic_review(review)
            for key, label in (("notes", "Notes"), ("next_action", "Next action")):
                if row.get(key):
                    out += [f"**{label}.** {row[key]}", ""]
            if row.get("gap_refs"):
                out += ["**Gap refs:** " + ", ".join(f"`{x}`" for x in row["gap_refs"]), ""]
            if row.get("blocked_by"):
                out += ["**Blocked by:** " + ", ".join(f"`{x}`" for x in row["blocked_by"]), ""]
            holes = row.get("completion_holes")
            if holes:
                out += ["**Completion holes:**", "", *[f"- {x}" for x in holes], ""]
        return "\n".join(out).rstrip() + "\n"

    def render_html(self) -> str:
        payload = {
            "title": self.title,
            "family": self.family,
            "summary": self.summary(),
            "data": self.data,
        }
        template = resources.files("aiq_lean_tools").joinpath("assets/census_viewer.html").read_text(encoding="utf-8")
        encoded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/").replace("<", "\\u003c")
        return template.replace("__TITLE__", html.escape(self.title)).replace("__PAYLOAD__", encoded)


def _render_embedded_semantic_review(review: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    source = review.get("source_statement")
    if isinstance(source, dict):
        out += ["**Curated source/Lean review:**", ""]
        for key in ("setup", "hypotheses", "conclusions", "scope"):
            values = source.get(key, [])
            if values:
                out += [f"*{key.capitalize()}*", *[f"- {x}" for x in values], ""]
    clauses = review.get("clause_map")
    if isinstance(clauses, list) and clauses:
        out += ["| source clause | Lean realization | status |", "| --- | --- | --- |"]
        for clause in clauses:
            out.append(
                "| " + " | ".join(md_escape(clause.get(k, "")) for k in ("source_clause", "lean_realization", "status")) + " |"
            )
        out.append("")
    return out


def load_census(path: str | pathlib.Path, *, root: str | pathlib.Path | None = None) -> CensusDocument:
    p = Path(path).expanduser().resolve()
    data = load_json(p)
    if not isinstance(data, dict):
        raise CensusValidationError(f"census root must be a JSON object: {p}")
    return CensusDocument(p, data, Path(root).expanduser().resolve() if root else infer_artifact_root(p))

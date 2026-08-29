"""Curated source-to-Lean semantic alignment packets.

Census rows can carry an embedded ``semantic_review`` object.  This module turns
those objects into a compact review packet and can compiler-probe declarations
through the mockable :mod:`aiq_lean_tools.lean_backend` boundary.

The packet presents the human-written Lean declaration header first, followed
by compiler-expanded evidence when probing is requested.  That keeps source
review readable without treating a structural Python scan as semantic evidence.
"""
from __future__ import annotations

import collections
import pathlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .census import CensusDocument, load_census
from .common import Path, md_escape, unique_in_order
from .lean_backend import LeanBackend, LeanQueryProbe, SubprocessLeanBackend
from .lean_source import (
    LeanSourceIndex,
    SourceDeclarationText,
    declaration_source_texts,
    scan_lean_project,
)
from .semantic_surface import IMPORTANCE_ORDER


def _canonical(review: Mapping[str, Any], row: Mapping[str, Any]) -> list[str]:
    values = review.get("canonical_declarations")
    if not values:
        values = review.get("declarations")
    if not values:
        values = list(row.get("lean_declarations", []) or [])[:1]
    return [str(x) for x in values or []]


def _supporting(review: Mapping[str, Any], row: Mapping[str, Any]) -> list[str]:
    values = review.get("supporting_declarations")
    if values is None:
        all_decls = list(row.get("lean_declarations", []) or [])
        canonical = set(_canonical(review, row))
        values = [x for x in all_decls if x not in canonical]
    return [str(x) for x in values or []]


def _context(review: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for value in review.get("context_declarations", []) or []:
        if isinstance(value, str):
            out.append({"name": value, "mathematical_role": ""})
        elif isinstance(value, Mapping) and value.get("name"):
            out.append(dict(value))
    return out


@dataclass
class AlignmentEntry:
    census: CensusDocument
    row: dict[str, Any]
    review: dict[str, Any]

    @property
    def group(self) -> str:
        return str(self.review.get("group") or self.row.get("id"))

    @property
    def title(self) -> str:
        return str(self.review.get("group_title") or self.row.get("title") or self.group)

    @property
    def canonical(self) -> list[str]:
        return _canonical(self.review, self.row)

    @property
    def supporting(self) -> list[str]:
        return _supporting(self.review, self.row)

    @property
    def context(self) -> list[dict[str, Any]]:
        return _context(self.review)


@dataclass
class AlignmentVariant:
    """Derived review target attached to a parent census row."""

    census: CensusDocument
    parent_row: dict[str, Any]
    review: dict[str, Any]

    @property
    def parent_group(self) -> str:
        parent_review = self.parent_row.get("semantic_review")
        if isinstance(parent_review, Mapping) and parent_review.get("group"):
            return str(parent_review["group"])
        return str(self.parent_row.get("id"))

    @property
    def title(self) -> str:
        return str(self.review.get("title") or self.review.get("id") or "derived review")

    @property
    def canonical(self) -> list[str]:
        return _canonical(self.review, self.parent_row)

    @property
    def supporting(self) -> list[str]:
        return _supporting(self.review, self.parent_row)

    @property
    def context(self) -> list[dict[str, Any]]:
        return _context(self.review)


@dataclass
class AlignmentPacket:
    root: Path
    entries: list[AlignmentEntry]
    probes: dict[tuple[str, str], LeanQueryProbe]
    imports: list[str]
    variants: list[AlignmentVariant] = field(default_factory=list)
    source_declarations: dict[str, list[SourceDeclarationText]] = field(default_factory=dict)

    def _render_source_declaration(self, name: str) -> list[str]:
        rows = self.source_declarations.get(name, [])
        if not rows:
            return ["Human-written source declaration not located by the structural scanner.", ""]
        out = ["**Human-written Lean statement**", ""]
        if len(rows) > 1:
            out += [
                f"The source scanner found {len(rows)} candidates for this name; all are shown.",
                "",
            ]
        for row in rows:
            try:
                rel = row.declaration.path.relative_to(self.root)
            except ValueError:
                rel = row.declaration.path
            out += [f"`{rel.as_posix()}:{row.declaration.line}`", "", "~~~~lean", row.render(), "~~~~", ""]
        return out

    def _render_review_body(
        self,
        *,
        review: Mapping[str, Any],
        canonical: Sequence[str],
        supporting: Sequence[str],
        context: Sequence[Mapping[str, Any]],
        next_action: object | None = None,
    ) -> list[str]:
        out: list[str] = []
        source = review.get("source_statement")
        if isinstance(source, Mapping):
            out += ["### Normalized source statement", ""]
            for key in ("setup", "hypotheses", "conclusions", "scope"):
                values = source.get(key, [])
                if values:
                    out += [f"**{key.capitalize()}**", *[f"- {x}" for x in values], ""]

        if canonical:
            out += ["### Canonical Lean declarations", ""]
            for name in canonical:
                out += [f"#### `{name}`", ""]
                out += self._render_source_declaration(name)
                probe = self.probes.get(("check", name))
                if probe is None:
                    out += ["Compiler probe not requested.", ""]
                elif probe.resolved:
                    out += ["**Compiler-resolved type**", "", "~~~~lean", probe.output, "~~~~", ""]
                else:
                    out += ["**Compiler probe failed to resolve this declaration.**", ""]

        if supporting:
            out += ["### Supporting scope declarations", ""]
            for name in supporting:
                probe = self.probes.get(("check", name))
                status = "resolved" if probe and probe.resolved else ("unresolved" if probe else "not probed")
                source_rows = self.source_declarations.get(name, [])
                source_status = "source located" if source_rows else "source not located"
                out.append(f"- `{name}` — {status}; {source_status}")
            out.append("")

        if context:
            out += ["### Local semantic dictionary", ""]
            for item in context:
                name = str(item["name"])
                out += [f"#### `{name}`", "", str(item.get("mathematical_role", "")), ""]
                probe = self.probes.get(("print", name))
                if probe and probe.resolved:
                    out += ["~~~~lean", probe.output, "~~~~", ""]
                elif probe:
                    out += ["**Compiler probe failed to print this declaration.**", ""]

        clauses = review.get("clause_map") or []
        if clauses:
            out += [
                "### Clause correspondence",
                "",
                "| source clause | Lean realization | status |",
                "| --- | --- | --- |",
            ]
            for clause in clauses:
                out.append(
                    "| "
                    + " | ".join(
                        md_escape(clause.get(key, ""))
                        for key in ("source_clause", "lean_realization", "status")
                    )
                    + " |"
                )
            out.append("")
        if review.get("note"):
            out += [f"**Review note.** {review['note']}", ""]
        if next_action:
            out += [f"**Next action.** {next_action}", ""]
        return out

    def render_markdown(self) -> str:
        grouped: collections.OrderedDict[tuple[str, str], list[AlignmentEntry]] = collections.OrderedDict()
        for entry in self.entries:
            key = (entry.census.title, entry.group)
            grouped.setdefault(key, []).append(entry)
        out = [
            "# Semantic alignment review packet",
            "",
            "This packet is generated from curated semantic-review fields in the source censuses. "
            "Human-written Lean headers are structural source evidence. Compiler output, when present, "
            "is elaborator-backed evidence about the Lean surface. The source-to-Lean correspondence "
            "remains the census author's explicit review claim.",
            "",
        ]
        if self.imports:
            out += ["**Compiler imports:** " + ", ".join(f"`{x}`" for x in self.imports), ""]
        for (paper, group), entries in grouped.items():
            first = entries[0]
            out += [f"## {paper}: {first.title}", ""]
            claim = first.review.get("claim") or first.row.get("summary") or first.row.get("source_claim")
            if claim:
                out += [str(claim), ""]
            for entry in entries:
                if len(entries) > 1:
                    out += [f"### `{entry.row.get('id')}`", ""]
                out += self._render_review_body(
                    review=entry.review,
                    canonical=entry.canonical,
                    supporting=entry.supporting,
                    context=entry.context,
                    next_action=entry.row.get("next_action"),
                )

            group_variants = [
                variant
                for variant in self.variants
                if variant.census.title == paper and variant.parent_group == group
            ]
            if group_variants:
                out += ["### Derived review variants", ""]
                for variant in group_variants:
                    out += [f"#### {variant.title}", ""]
                    if variant.review.get("claim"):
                        out += [str(variant.review["claim"]), ""]
                    if variant.review.get("provenance_note"):
                        out += [f"**Provenance.** {variant.review['provenance_note']}", ""]
                    out += self._render_review_body(
                        review=variant.review,
                        canonical=variant.canonical,
                        supporting=variant.supporting,
                        context=variant.context,
                    )
        return "\n".join(out).rstrip() + "\n"


def _fallback_review(row: Mapping[str, Any]) -> dict[str, Any]:
    decls = list(row.get("lean_declarations", []) or [])
    summary = str(row.get("summary") or row.get("source_claim") or row.get("title") or row.get("id") or "")
    return {
        "group": row.get("id"),
        "group_title": row.get("title", row.get("id")),
        "claim": summary,
        "source_statement": {
            "setup": [],
            "hypotheses": ["See the full source census for the uncurated source hypotheses."],
            "conclusions": [summary],
            "scope": [],
        },
        "canonical_declarations": decls[:1],
        "supporting_declarations": decls[1:],
        "context_declarations": [],
        "clause_map": [
            {
                "source_clause": summary,
                "lean_realization": "No curated correspondence is registered for this broader-tier row.",
                "status": "claimed_exact",
            }
        ],
        "note": "This row is outside the curated headline surface; showing the census fallback.",
    }


def collect_alignment_entries(
    censuses: Sequence[CensusDocument],
    *,
    importance: str = "headline",
) -> list[AlignmentEntry]:
    if importance not in IMPORTANCE_ORDER:
        raise ValueError(f"unknown importance threshold {importance!r}")
    threshold = IMPORTANCE_ORDER[importance]
    entries: list[AlignmentEntry] = []
    for census in censuses:
        for row in census.items:
            rank = IMPORTANCE_ORDER.get(str(row.get("importance", "technical")), 3)
            if rank > threshold:
                continue
            review = row.get("semantic_review")
            if not isinstance(review, dict):
                review = _fallback_review(row)
            entries.append(AlignmentEntry(census, row, review))
    return entries


def collect_alignment_variants(entries: Sequence[AlignmentEntry]) -> list[AlignmentVariant]:
    variants: list[AlignmentVariant] = []
    for entry in entries:
        for review in entry.row.get("semantic_review_variants", []) or []:
            if isinstance(review, dict):
                variants.append(AlignmentVariant(entry.census, entry.row, review))
    return variants


def _probe_queries(
    entries: Sequence[AlignmentEntry],
    variants: Sequence[AlignmentVariant],
) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    for entry in entries:
        queries.extend(("check", name) for name in entry.canonical)
        queries.extend(("check", name) for name in entry.supporting)
        queries.extend(("print", str(item["name"])) for item in entry.context)
    for variant in variants:
        queries.extend(("check", name) for name in variant.canonical)
        queries.extend(("check", name) for name in variant.supporting)
        queries.extend(("print", str(item["name"])) for item in variant.context)
    return list(dict.fromkeys(queries))


def _source_declaration_map(
    index: LeanSourceIndex,
    entries: Sequence[AlignmentEntry],
    variants: Sequence[AlignmentVariant],
) -> dict[str, list[SourceDeclarationText]]:
    names: list[str] = []
    for entry in entries:
        names.extend(entry.canonical)
        names.extend(entry.supporting)
    for variant in variants:
        names.extend(variant.canonical)
        names.extend(variant.supporting)
    return {
        name: declaration_source_texts(index, name)
        for name in unique_in_order(names)
    }


def build_alignment_packet(
    census_paths: Sequence[str | pathlib.Path],
    *,
    root: str | pathlib.Path | None = None,
    importance: str = "headline",
    probe: bool = False,
    imports: Sequence[str] = (),
    backend: LeanBackend | None = None,
    timeout: int = 3600,
) -> AlignmentPacket:
    censuses = [load_census(path, root=root) for path in census_paths]
    if not censuses:
        raise ValueError("at least one census is required")
    base = censuses[0].root
    entries = collect_alignment_entries(censuses, importance=importance)
    variants = collect_alignment_variants(entries)
    source_index = scan_lean_project(base)
    source_declarations = _source_declaration_map(source_index, entries, variants)

    probe_map: dict[tuple[str, str], LeanQueryProbe] = {}
    import_list = list(imports)
    if probe:
        queries = _probe_queries(entries, variants)
        if not import_list:
            for _, name in queries:
                exact = [row for row in source_index.resolve(name) if row.name == name]
                import_list.extend(row.module for row in exact)
                prefix = name.split(".", 1)[0]
                if (base / f"{prefix}.lean").is_file():
                    import_list.append(prefix)
        import_list = unique_in_order(import_list)
        runner = backend or SubprocessLeanBackend()
        results = runner.probe_queries(base, queries, import_list, timeout=timeout)
        probe_map = {(row.mode, row.name): row for row in results}
    return AlignmentPacket(
        base,
        entries,
        probe_map,
        import_list,
        variants=variants,
        source_declarations=source_declarations,
    )

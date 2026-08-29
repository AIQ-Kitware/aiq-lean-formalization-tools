"""Curated source-to-Lean semantic alignment packets.

Census rows can carry an embedded ``semantic_review`` object.  This module turns
those objects into a compact review packet and can compiler-probe the declarations
through the mockable :mod:`aiq_lean_tools.lean_backend` boundary.
"""
from __future__ import annotations

import collections
import pathlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .census import CensusDocument, load_census
from .common import Path, md_escape, unique_in_order
from .lean_backend import LeanBackend, LeanQueryProbe, SubprocessLeanBackend
from .lean_source import LeanSourceIndex, scan_lean_project

IMPORTANCE_ORDER = {"headline": 0, "major": 1, "supporting": 2, "technical": 3}


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
        values = self.review.get("canonical_declarations")
        if not values:
            values = self.review.get("declarations")
        if not values:
            values = self.row.get("lean_declarations", [])[:1]
        return [str(x) for x in values or []]

    @property
    def supporting(self) -> list[str]:
        values = self.review.get("supporting_declarations")
        if values is None:
            all_decls = list(self.row.get("lean_declarations", []) or [])
            canonical = set(self.canonical)
            values = [x for x in all_decls if x not in canonical]
        return [str(x) for x in values or []]

    @property
    def context(self) -> list[dict[str, Any]]:
        out = []
        for value in self.review.get("context_declarations", []) or []:
            if isinstance(value, str):
                out.append({"name": value, "mathematical_role": ""})
            elif isinstance(value, Mapping) and value.get("name"):
                out.append(dict(value))
        return out


@dataclass
class AlignmentPacket:
    root: Path
    entries: list[AlignmentEntry]
    probes: dict[tuple[str, str], LeanQueryProbe]
    imports: list[str]

    def render_markdown(self) -> str:
        grouped: collections.OrderedDict[tuple[str, str], list[AlignmentEntry]] = collections.OrderedDict()
        for entry in self.entries:
            key = (entry.census.title, entry.group)
            grouped.setdefault(key, []).append(entry)
        out = [
            "# Semantic alignment review packet",
            "",
            "This packet is generated from curated semantic-review fields in the source censuses. "
            "Compiler output, when present, is evidence about the Lean surface; the source-to-Lean "
            "correspondence remains the census author's explicit claim.",
            "",
        ]
        if self.imports:
            out += ["**Compiler imports:** " + ", ".join(f"`{x}`" for x in self.imports), ""]
        for (paper, group), variants in grouped.items():
            first = variants[0]
            out += [f"## {paper}: {first.title}", ""]
            claim = first.review.get("claim") or first.row.get("summary") or first.row.get("source_claim")
            if claim:
                out += [str(claim), ""]
            for entry in variants:
                if len(variants) > 1:
                    out += [f"### `{entry.row.get('id')}`", ""]
                source = entry.review.get("source_statement")
                if isinstance(source, Mapping):
                    out += ["### Normalized source statement", ""]
                    for key in ("setup", "hypotheses", "conclusions", "scope"):
                        values = source.get(key, [])
                        if values:
                            out += [f"**{key.capitalize()}**", *[f"- {x}" for x in values], ""]
                if entry.canonical:
                    out += ["### Canonical Lean declarations", ""]
                    for name in entry.canonical:
                        out += [f"#### `{name}`", ""]
                        probe = self.probes.get(("check", name))
                        if probe is None:
                            out += ["Compiler probe not requested.", ""]
                        elif probe.resolved:
                            out += ["~~~~lean", probe.output, "~~~~", ""]
                        else:
                            out += ["**Compiler probe failed to resolve this declaration.**", ""]
                if entry.supporting:
                    out += ["### Supporting scope declarations", ""]
                    for name in entry.supporting:
                        probe = self.probes.get(("check", name))
                        status = "resolved" if probe and probe.resolved else ("unresolved" if probe else "not probed")
                        out.append(f"- `{name}` — {status}")
                    out.append("")
                if entry.context:
                    out += ["### Local semantic dictionary", ""]
                    for context in entry.context:
                        name = str(context["name"])
                        out += [f"#### `{name}`", "", str(context.get("mathematical_role", "")), ""]
                        probe = self.probes.get(("print", name))
                        if probe and probe.resolved:
                            out += ["~~~~lean", probe.output, "~~~~", ""]
                        elif probe:
                            out += ["**Compiler probe failed to print this declaration.**", ""]
                clauses = entry.review.get("clause_map") or []
                if clauses:
                    out += ["### Clause correspondence", "", "| source clause | Lean realization | status |", "| --- | --- | --- |"]
                    for clause in clauses:
                        out.append("| " + " | ".join(md_escape(clause.get(k, "")) for k in ("source_clause", "lean_realization", "status")) + " |")
                    out.append("")
                if entry.review.get("note"):
                    out += [f"**Review note.** {entry.review['note']}", ""]
                if entry.row.get("next_action"):
                    out += [f"**Next action.** {entry.row['next_action']}", ""]
        return "\n".join(out).rstrip() + "\n"


def collect_alignment_entries(censuses: Sequence[CensusDocument], *, importance: str = "headline") -> list[AlignmentEntry]:
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
                review = {
                    "group": row.get("id"),
                    "group_title": row.get("title", row.get("id")),
                    "claim": row.get("summary") or row.get("source_claim", ""),
                    "canonical_declarations": list(row.get("lean_declarations", [])[:1]),
                    "supporting_declarations": list(row.get("lean_declarations", [])[1:]),
                    "context_declarations": [],
                    "clause_map": [],
                    "note": "No curated semantic_review object is registered on this row.",
                }
            entries.append(AlignmentEntry(census, row, review))
    return entries


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
    probe_map: dict[tuple[str, str], LeanQueryProbe] = {}
    import_list = list(imports)
    if probe:
        source_index = scan_lean_project(base)
        queries: list[tuple[str, str]] = []
        for entry in entries:
            queries.extend(("check", name) for name in entry.canonical)
            queries.extend(("check", name) for name in entry.supporting)
            queries.extend(("print", str(item["name"])) for item in entry.context)
        queries = list(dict.fromkeys(queries))
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
    return AlignmentPacket(base, entries, probe_map, import_list)

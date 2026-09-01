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
from .statement_pins import PIN_FIELD, pin_status, statement_records


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
    statements: dict[str, Any] = field(default_factory=dict)
    statement_meta: dict[str, Any] = field(default_factory=dict)

    def _render_statement(
        self, name: str, review: Mapping[str, Any], context: Sequence[Mapping[str, Any]]
    ) -> list[str]:
        """Elaborator evidence for one canonical declaration.

        The signature is what the reviewer compares with the paper.  The closure
        report then answers the question the hand-written dictionary cannot be
        trusted to answer: which project constants does this statement actually
        depend on, and does the dictionary disclose every one of them?
        """
        from leanq.statement import closure_summary, render_closure_text

        record = self.statements.get(name)
        if record is None:
            return ["**Statement sidecar has no record for this declaration.**", ""]
        if record.missing:
            return ["**Declaration is not in the elaborated environment.**", ""]
        pins = review.get(PIN_FIELD) or []
        pin = next(
            (p for p in pins if isinstance(p, Mapping) and p.get("declaration") == name), None
        )
        status = pin_status(pin, record)
        out = [f"**Elaborated signature** (statement pin: {status})", ""]
        out += ["~~~~lean", record.signature or record.type, "~~~~", ""]
        out.append(
            f"Structural type hash `{record.type_expr_hash}`, printed-type hash "
            f"`{record.type_text_sha256[:16]}`."
        )
        if pin is not None and status != "current":
            out.append(
                f"Pinned `{pin.get('type_expr_hash')}` / `{str(pin.get('type_text_sha256', ''))[:16]}` "
                f"on {pin.get('pinned_on', '?')}."
            )
        out.append("")

        summary = closure_summary(self.statements, name)
        disclosed = {str(item["name"]) for item in context}
        project_constants = [*summary["unfolded"], *summary["leaves"]]
        undisclosed = [n for n in project_constants if n not in disclosed]
        # A dictionary entry that is a lemma *about* a definition (an `_iff`
        # characterization, say) is explanatory and never appears in a statement.
        # A definition or structure in the dictionary that the statement never
        # reaches is a different matter: the dictionary is describing something
        # this theorem does not say.
        unreachable = [
            n for n in disclosed
            if n not in summary["reached"] and n != name
            and n in self.statements and self.statements[n].kind in {"def", "inductive"}
        ]
        out.append(
            f"Statement closure: {len(summary['unfolded'])} project constant(s) unfolded, "
            f"{len(summary['leaves'])} project leaf/leaves, "
            f"{len(summary['boundary'])} boundary constant(s)."
        )
        if undisclosed:
            out.append(
                "**Project constants in the statement closure that the local semantic "
                "dictionary does not disclose:** " + ", ".join(f"`{n}`" for n in undisclosed)
            )
        else:
            out.append("Every project constant in the statement closure is in the dictionary.")
        if unreachable:
            out.append(
                "Dictionary definitions this statement never reaches: "
                + ", ".join(f"`{n}`" for n in unreachable)
            )
        if summary["boundary"]:
            out.append("Boundary vocabulary: " + ", ".join(f"`{n}`" for n in summary["boundary"]))
        if summary["unknown"]:
            out.append("Unknown constants: " + ", ".join(f"`{n}`" for n in summary["unknown"]))
        out.append("")
        out += [
            "<details><summary>Statement closure tree</summary>", "", "~~~~text",
            render_closure_text(self.statements, name, show_boundary=False).rstrip(),
            "~~~~", "", "</details>", "",
        ]
        return out

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
                if self.statements:
                    out += self._render_statement(name, review, context)
                probe = self.probes.get(("check", name))
                if probe is None and not self.statements:
                    out += ["Compiler probe not requested.", ""]
                elif probe is None:
                    pass
                elif probe.resolved:
                    out += ["**Compiler-resolved type**", "", "~~~~lean", probe.output, "~~~~", ""]
                else:
                    out += ["**Compiler probe failed to resolve this declaration.**", ""]

        if supporting:
            out += ["### Supporting scope declarations", ""]
            for name in supporting:
                probe = self.probes.get(("check", name))
                status = "resolved" if probe and probe.resolved else ("unresolved" if probe else "not probed")
                if self.statements:
                    record = self.statements.get(name)
                    status = (
                        "elaborated" if record is not None and not record.missing
                        else "not in environment"
                    )
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
        if self.statements:
            meta = self.statement_meta
            out += [
                "**Elaborator evidence:** statement sidecar with "
                f"{len(self.statements)} record(s)"
                + (f", toolchain `{meta['toolchain']}`" if meta.get("toolchain") else "")
                + ". Signatures, hashes and closures below are read from the elaborated "
                "environment, not from source text.",
                "",
            ]
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


def _declaration_payload(
    packet: AlignmentPacket,
    name: str,
    review: Mapping[str, Any],
    context: Sequence[Mapping[str, Any]],
    graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    from leanq.statement import closure_edges, closure_summary

    out: dict[str, Any] = {"name": name, "pinStatus": "n/a"}
    rows = packet.source_declarations.get(name, [])
    if rows:
        row = rows[0]
        try:
            rel = row.declaration.path.relative_to(packet.root)
        except ValueError:
            rel = row.declaration.path
        out["source"] = {"path": rel.as_posix(), "line": row.declaration.line, "text": row.render()}
    record = packet.statements.get(name)
    if record is not None and not record.missing:
        pins = review.get(PIN_FIELD) or []
        pin = next(
            (p for p in pins if isinstance(p, Mapping) and p.get("declaration") == name), None
        )
        out.update(
            signature=record.signature or record.type,
            docstring=record.docstring,
            hashes={"expr": record.type_expr_hash, "text": record.type_text_sha256},
            pin=dict(pin) if pin else None,
            pinStatus=pin_status(pin, record),
        )
        summary = closure_summary(packet.statements, name)
        disclosed = {str(item["name"]) for item in context}
        out["closure"] = {
            "summary": summary,
            "edges": [
                {"source": a, "target": b, "via": via}
                for a, b, via in closure_edges(packet.statements, name)
            ],
        }
        out["hasDictionary"] = bool(disclosed)
        out["undisclosed"] = [
            n for n in [*summary["unfolded"], *summary["leaves"]] if n not in disclosed
        ]
        out["unreachableDictionary"] = [
            n for n in disclosed
            if n not in summary["reached"] and n != name
            and n in packet.statements and packet.statements[n].kind in {"def", "inductive"}
        ]
    elif record is not None:
        out["pinStatus"] = "gone"
    if graph is not None:
        out["proof"] = _proof_payload(graph, name)
    return out


def _proof_payload(graph: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    """Project-local proof dependencies of ``name`` from a saved leanq graph index."""
    from leanq.graph import target_dependency_graph

    table = graph["table"]
    if name not in table:
        return None
    dep_graph = target_dependency_graph(table.values(), [name])
    by_library = collections.Counter(
        str(d.library or d.module.split(".", 1)[0]) for d in dep_graph.nodes.values()
    )
    by_module = collections.Counter(d.module for d in dep_graph.nodes.values())
    decl = table[name]
    direct = []
    for dep in decl.deps:
        target = table.get(dep)
        if target is None:
            continue
        direct.append(
            {"name": dep, "role": decl.dependency_role(dep), "kind": target.kind,
             "module": target.module}
        )
    direct.sort(key=lambda d: (d["role"] != "type", d["name"]))
    return {
        "nodeCount": len(dep_graph.nodes),
        "edgeCount": len(dep_graph.edges),
        "byLibrary": dict(sorted(by_library.items(), key=lambda kv: -kv[1])),
        "byModule": sorted(by_module.items(), key=lambda kv: (-kv[1], kv[0])),
        "direct": direct,
        "unresolvedCount": len(dep_graph.unresolved),
        "axioms": list(decl.axioms) if decl.axioms is not None else None,
    }


def load_graph_table(path: str | pathlib.Path) -> dict[str, Any]:
    """A saved ``leanq graph-index`` payload, indexed by declaration name."""
    import json as _json

    from leanq.graph import declarations_from_graph_payload

    payload = _json.loads(Path(path).read_text(encoding="utf-8"))
    decls = declarations_from_graph_payload(payload)
    return {"table": {d.name: d for d in decls}, "nodeCount": len(decls), "path": str(path)}


def alignment_payload(
    packet: AlignmentPacket,
    *,
    graph: Mapping[str, Any] | None = None,
    title: str = "Semantic alignment review",
) -> dict[str, Any]:
    """Everything the HTML page shows, as one JSON object.

    Records of the statement sidecar are embedded once, keyed by name, and every
    closure refers to them; plumbing is left in so a viewer can collapse it
    rather than lose it.
    """
    papers: collections.OrderedDict[str, dict[str, Any]] = collections.OrderedDict()
    used_records: set[str] = set()
    for entry in packet.entries:
        paper = papers.setdefault(
            entry.census.title, {"title": entry.census.title, "path": str(entry.census.path), "rows": []}
        )
        row, review = entry.row, entry.review
        canonical = [
            _declaration_payload(packet, name, review, entry.context, graph)
            for name in entry.canonical
        ]
        variants = []
        for variant in packet.variants:
            if variant.parent_row is row:
                variants.append({
                    "title": variant.title,
                    "claim": variant.review.get("claim"),
                    "canonical": [
                        _declaration_payload(packet, name, variant.review, variant.context, graph)
                        for name in variant.canonical
                    ],
                })
        reached: set[str] = set()
        for decl in canonical:
            if decl.get("closure"):
                reached.update(decl["closure"]["summary"]["reached"])
                reached.add(decl["name"])
                used_records.update(reached)
        statuses = [d["pinStatus"] for d in canonical]
        if not statuses or all(s == "n/a" for s in statuses):
            pin_summary = None
        elif any(s in {"drift", "gone"} for s in statuses):
            pin_summary = "drift"
        elif any(s == "text-drift" for s in statuses):
            pin_summary = "text-drift"
        elif any(s == "unpinned" for s in statuses):
            pin_summary = "unpinned"
        else:
            pin_summary = "current"
        context_rows = []
        for item in entry.context:
            name = str(item["name"])
            record = packet.statements.get(name)
            context_rows.append({
                "name": name,
                "role": str(item.get("mathematical_role", "")),
                "kind": record.kind if record is not None and not record.missing else None,
                "reachable": (name in reached) if packet.statements else None,
            })
        supporting = []
        for name in entry.supporting:
            record = packet.statements.get(name)
            if packet.statements:
                status = "elaborated" if record is not None and not record.missing else "not in environment"
            else:
                status = "source located" if packet.source_declarations.get(name) else "source not located"
            supporting.append({"name": name, "status": status})
        paper["rows"].append({
            "id": str(row.get("id")),
            "anchor": f"{len(papers)}-{row.get('id')}",
            "paper": entry.census.title,
            "title": entry.title,
            "group": entry.group,
            "claim": review.get("claim") or row.get("summary") or row.get("source_claim"),
            "importance": row.get("importance"),
            "status": row.get("status"),
            "verification": row.get("verification"),
            "sourceAnchor": row.get("source_anchor"),
            "sourceStatement": review.get("source_statement"),
            "clauses": review.get("clause_map") or [],
            "note": review.get("note"),
            "nextAction": row.get("next_action"),
            "canonical": canonical,
            "supporting": supporting,
            "context": context_rows,
            "variants": variants,
            "pinSummary": pin_summary,
        })
    records = {
        name: packet.statements[name].to_json()
        for name in sorted(used_records) if name in packet.statements
    }
    return {
        "schemaVersion": 1,
        "payloadKind": "alignment-review",
        "title": title,
        "statementMeta": dict(packet.statement_meta),
        "graph": {"nodeCount": graph["nodeCount"], "path": graph.get("path")} if graph else None,
        "papers": list(papers.values()),
        "records": records,
    }


def render_alignment_html(payload: Mapping[str, Any]) -> str:
    from importlib import resources
    import html as _html
    import json as _json

    template = resources.files("aiq_lean_tools").joinpath("assets/alignment_viewer.html").read_text(encoding="utf-8")
    text = _json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    text = text.replace("<", r"\u003c").replace(">", r"\u003e").replace("&", r"\u0026")
    return template.replace("__TITLE__", _html.escape(str(payload.get("title", "")))).replace("__PAYLOAD__", text)


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
    statements: bool = False,
    sidecar: str | pathlib.Path | None = None,
    library: str | None = None,
    refresh: bool = False,
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
    statement_map: dict[str, Any] = {}
    statement_meta: dict[str, Any] = {}
    if statements or sidecar is not None:
        seeds: list[str] = []
        for entry in entries:
            seeds.extend(entry.canonical)
            seeds.extend(entry.supporting)
            seeds.extend(str(item["name"]) for item in entry.context)
        for variant in variants:
            seeds.extend(variant.canonical)
            seeds.extend(variant.supporting)
            seeds.extend(str(item["name"]) for item in variant.context)
        statement_map, statement_meta = statement_records(
            base, unique_in_order(seeds), sidecar=sidecar, library=library, refresh=refresh,
        )
    return AlignmentPacket(
        base,
        entries,
        probe_map,
        import_list,
        variants=variants,
        source_declarations=source_declarations,
        statements=statement_map,
        statement_meta=statement_meta,
    )

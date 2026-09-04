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
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .census import CensusDocument, load_census
from .companion import companion_reviews
from .common import Path, md_escape, unique_in_order
from .correspondence import cited_declarations, display_fragments, edges_of, relation_legend
from .source_model import SourceFragment, SourceLibrary, SourceLocator
from .source_pins import SOURCE_PIN_FIELD, source_pin_status
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
        # A presentation form is registered in `lean_declarations` like any other
        # declaration, and it gets its own panel; listing it again as a bare
        # supporting name says the row has a declaration it has not shown.
        named = set(_canonical(review, row)) | {p["name"] for p in _presentation(review)}
        values = [x for x in all_decls if x not in named]
    return [str(x) for x in values or []]


def _presentation(review: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Declarations restated so the signature reads like the printed theorem.

    A bare string is legal and means name-only, and every field but the name may
    be missing: the census is curated by hand, and a half-filled entry must still
    show the declaration rather than take the page down.
    """
    out: list[dict[str, Any]] = []
    for value in review.get("presentation_declarations", []) or []:
        item: Mapping[str, Any] = {"name": value} if isinstance(value, str) else value
        if not isinstance(item, Mapping) or not item.get("name"):
            continue
        out.append({
            "name": str(item["name"]),
            "fronts": [str(x) for x in item.get("fronts") or []],
            "relation": str(item.get("relation") or "unstated"),
            "devices": [str(x) for x in item.get("devices") or []],
            "why": str(item.get("why") or ""),
        })
    return out


def _context(review: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for value in review.get("context_declarations", []) or []:
        if isinstance(value, str):
            out.append({"name": value, "mathematical_role": ""})
        elif isinstance(value, Mapping) and value.get("name"):
            out.append(dict(value))
    return out


def _fronting_sentence(item: Mapping[str, Any]) -> str:
    """One line saying what a presentation declaration fronts, and by what device."""
    fronts = [str(x) for x in item.get("fronts") or []]
    relation = str(item.get("relation") or "unstated")
    devices = [str(x) for x in item.get("devices") or []]
    head = (
        "Fronts " + ", ".join(f"`{n}`" for n in fronts) + f" ({relation})."
        if fronts else f"Presentation form; what it fronts is {relation}."
    )
    return head + (" Devices: " + "; ".join(devices) + "." if devices else "")


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
    def presentation(self) -> list[dict[str, Any]]:
        return _presentation(self.review)

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
    #: Resolves a review's source locators against the repository's documents.
    library: SourceLibrary | None = None
    #: Local-only mode: embed the text of private source fragments as well.
    include_private: bool = False
    #: Proof-dependency payloads by declaration name.  Walking the saved graph
    #: for one target costs seconds; the same declaration shows up in several
    #: rows, and a server holds one cache across every census it renders.
    proof_cache: dict[str, Any] = field(default_factory=dict)

    def fragments(self, review: Mapping[str, Any]) -> list[tuple[dict[str, Any], SourceFragment | None]]:
        """Every source passage a review points at, resolved where possible.

        An unresolvable fragment is kept with a ``None`` body rather than
        dropped: a reviewer must see that the review cites a passage the
        repository can no longer find.
        """
        out: list[tuple[dict[str, Any], SourceFragment | None]] = []
        for spec in display_fragments(review):
            fragment = None
            if self.library is not None and spec.get("locator"):
                try:
                    fragment = self.library.resolve(
                        spec["locator"], id=str(spec.get("id") or ""),
                        role=str(spec.get("role") or "primary"),
                    )
                except Exception:
                    fragment = None
            out.append((spec, fragment))
        return out

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
        disclosed = _disclosed_constants(self.statements, context)
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
        presentation: Sequence[Mapping[str, Any]] = (),
        next_action: object | None = None,
    ) -> list[str]:
        out: list[str] = []
        for spec, fragment in self.fragments(review):
            role = str(spec.get("role") or "primary")
            label = "Source passage" if role == "primary" else f"Inherited source ({role.replace('_', ' ')})"
            if fragment is None:
                out += [f"### {label}", "",
                        f"**Unresolved locator** `{spec.get('locator')}`; the cited passage was "
                        "not found in any configured source document.", ""]
                continue
            out += [f"### {label} — {fragment.locator.label() or fragment.locator.key}", "",
                    f"`{fragment.locator.file}:{fragment.locator.lines[0]}-{fragment.locator.lines[1]}` "
                    f"· content hash `{fragment.sha256[:16]}`"
                    + (f" · **{fragment.visibility}**" if fragment.private else ""), ""]
            if fragment.private and not self.include_private:
                out += ["Private source text is not written into a review packet.", ""]
            else:
                out += ["~~~~latex", fragment.text.strip(), "~~~~", ""]

        source = review.get("source_statement")
        if isinstance(source, Mapping):
            out += ["### Normalized source statement", ""]
            for key in ("setup", "hypotheses", "conclusions", "scope"):
                values = source.get(key, [])
                if values:
                    out += [f"**{key.capitalize()}**", *[f"- {x}" for x in values], ""]

        # The presentation form comes first because it is the statement the
        # paper's reader recognises; the canonical one is then introduced as
        # what it fronts, rather than as a competing spelling.
        if presentation:
            out += ["### Presentation Lean declarations", ""]
            for item in presentation:
                name = str(item["name"])
                out += [f"#### `{name}`", "", _fronting_sentence(item), ""]
                if item.get("why"):
                    out += [str(item["why"]), ""]
                out += self._render_source_declaration(name)
                if self.statements:
                    out += self._render_statement(name, review, context)

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
                    presentation=entry.presentation,
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


def _disclosed_constants(
    statements: Mapping[str, Any], context: Sequence[Mapping[str, Any]]
) -> set[str]:
    """What a review's dictionary discloses: the entries themselves, and the project
    constants named in the *type* of any entry that is a lemma.

    A dictionary usually explains a compact predicate through its characteristic
    lemma (`isTrialResidual_iff`) rather than by naming the predicate; the lemma's
    statement mentions the predicate, so the predicate is disclosed.
    """
    disclosed = {str(item["name"]) for item in context}
    for name in list(disclosed):
        record = statements.get(name)
        if record is not None and not record.missing and record.kind == "theorem":
            disclosed.update(
                dep for dep in record.type_deps
                if dep in statements and not statements[dep].boundary
            )
    return disclosed


def _declaration_payload(
    packet: AlignmentPacket,
    name: str,
    review: Mapping[str, Any],
    context: Sequence[Mapping[str, Any]],
    graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    from leanq.statement import closure_edges, closure_summary

    from .lean_source import declaration_statement_text

    out: dict[str, Any] = {"name": name, "pinStatus": "n/a"}
    rows = packet.source_declarations.get(name, [])
    if rows:
        row = rows[0]
        try:
            rel = row.declaration.path.relative_to(packet.root)
        except ValueError:
            rel = row.declaration.path
        # A reviewer judging a correspondence reads what the declaration says,
        # not how it is proved: docstring and statement, never the proof body.
        out["source"] = {
            "path": rel.as_posix(),
            "line": row.declaration.line,
            "text": row.render(),
            "statement": declaration_statement_text(row.declaration.path, row.declaration.line),
            "module": getattr(row.declaration, "module", ""),
        }
    record = packet.statements.get(name)
    if record is not None and not record.missing:
        pins = review.get(PIN_FIELD) or []
        pin = next(
            (p for p in pins if isinstance(p, Mapping) and p.get("declaration") == name), None
        )
        out.update(
            signature=record.signature or record.type,
            kind=record.kind,
            module=record.module,
            typeDeps=list(record.type_deps),
            docstring=record.docstring,
            hashes={"expr": record.type_expr_hash, "text": record.type_text_sha256},
            pin=dict(pin) if pin else None,
            pinStatus=pin_status(pin, record),
        )
        summary = closure_summary(packet.statements, name)
        disclosed = _disclosed_constants(packet.statements, context)
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
        if name not in packet.proof_cache:
            packet.proof_cache[name] = _proof_payload(graph, name)
        out["proof"] = packet.proof_cache[name]
        if out["proof"] is None:
            # A saved index is a snapshot. After a rename it keeps answering,
            # under the old names, so a silently absent panel is the wrong
            # report: say the graph does not know this declaration.
            out["proofMissing"] = str(graph.get("path") or "the saved dependency graph")
    return out


def _source_payload(
    packet: AlignmentPacket,
    review: Mapping[str, Any],
    row: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """The literature side of one review row, and how its pins stand.

    Each entry carries the fragment as the browser renders it plus the two facts
    that decide whether the review is still about this passage: whether the row
    pinned it, and whether the passage has moved since.
    """
    pins = {
        str(p.get("fragment")): p
        for p in (review.get(SOURCE_PIN_FIELD) or [])
        if isinstance(p, Mapping)
    }
    # A census row written before this model carries one `source_locator` and
    # no review fragments -- and rows with no curated review at all still carry
    # it.  Read it as the implicit primary passage rather than showing nothing.
    container: Mapping[str, Any] = review
    if not display_fragments(review) and isinstance(row, Mapping) and row.get("source_locator"):
        container = {**review, "source_locator": row["source_locator"]}
    out: list[dict[str, Any]] = []
    statuses: list[str] = []
    for spec, fragment in packet.fragments(container):
        fid = str(spec.get("id") or (fragment.id if fragment else ""))
        status = source_pin_status(pins.get(fid), fragment)
        statuses.append(status)
        entry: dict[str, Any] = {
            "id": fid,
            "role": str(spec.get("role") or "primary"),
            "implicit": bool(spec.get("why") is None and spec.get("implicit")),
            "why": str(spec.get("why") or ""),
            "pinStatus": status,
            "pin": dict(pins[fid]) if fid in pins else None,
        }
        if fragment is None:
            entry["unresolved"] = spec.get("locator")
        else:
            entry["fragment"] = fragment.as_json(include_private=packet.include_private)
            # An overlay is an alternate rendition of the same logical passage:
            # the printed paper beside the distributable reconstruction. Its
            # existence is always disclosed -- a reviewer must be able to see
            # that a private provenance source was consulted -- while its text
            # travels only in a local render that asked for it.
            alternates = []
            if packet.library is not None and spec.get("locator"):
                try:
                    alternates = packet.library.alternates(
                        spec["locator"], id=fid,
                        role=str(spec.get("role") or "primary"),
                    )
                except Exception:
                    alternates = []
            if alternates:
                entry["alternates"] = [
                    a.as_json(include_private=packet.include_private) for a in alternates
                ]
        out.append(entry)
    if not statuses:
        return out, None
    for level in ("moved", "unresolved", "unpinned"):
        if level in statuses:
            return out, level
    return out, "current"


def resolve_graph_name(table: Mapping[str, Any], name: str) -> str | None:
    """The graph index's spelling of ``name``.

    Censuses and statement sidecars use whatever name the review author wrote --
    often without the outer namespace -- while the graph index stores the fully
    qualified one.  An exact lookup therefore misses, and the dependency panel
    comes back empty for a declaration that is right there in the graph.
    """
    if not table:
        return None
    if name in table:
        return name
    for prefix in ("TauCeti.", "TauCeti.DavisKahan.", ""):
        if prefix + name in table:
            return prefix + name
    tail = "." + name.split(".")[-1]
    matches = [k for k in table if k.endswith(tail)]
    return matches[0] if len(matches) == 1 else None


#: How far the fronting search walks the dependency graph.  A saved index holds
#: tens of thousands of declarations and a headline theorem reaches thousands of
#: them, so an exhaustive pair of walks per fronted name is not worth its cost.
#: Both relations this looks for -- the presentation form delegating to the
#: declaration it fronts, and the two sharing a proof core -- sit within a couple
#: of edges.  The cap is a search budget and never evidence: a walk that hits it
#: without a verdict reports "unknown", because it has not shown the two unrelated.
FRONTS_SEARCH_DEPTH = 4


def _reachable(
    table: Mapping[str, Any], start: str, depth: int
) -> tuple[dict[str, int], bool]:
    """Project declarations reachable from ``start``, by fewest edges, and whether
    the walk stopped at the cap rather than exhausting what ``start`` reaches."""
    seen = {start: 0}
    frontier = [start]
    for level in range(1, depth + 1):
        nxt: list[str] = []
        for node in frontier:
            decl = table.get(node)
            if decl is None:
                continue
            for dep in decl.deps:
                if dep not in seen and dep in table:
                    seen[dep] = level
                    nxt.append(dep)
        frontier = nxt
        if not frontier:
            return seen, False
    return seen, bool(frontier)


def _is_proof_core(table: Mapping[str, Any], name: str) -> bool:
    """Whether ``name`` could be the proved machinery two statements share.

    Two specializations of one theorem also share every structure they take as a
    hypothesis, and each such structure contributes its field projections one
    edge away.  Those are shared *vocabulary*: naming one as the common core
    would bury the shared engine, which sits deeper, under an accessor.
    """
    decl = table.get(name)
    if decl is None or decl.kind != "theorem" or decl.internal:
        return False
    parent = table.get(name.rsplit(".", 1)[0])
    return parent is None or parent.kind not in {"inductive", "structure"}


def fronts_verified(
    graph: Mapping[str, Any] | None, name: str, fronts: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """How each ``fronts`` claim of a presentation declaration stands in the graph.

    Fronting is a curatorial claim about what a statement *presents*, not about
    how it is proved: the Davis--Kahan presentation form delegates to the
    symmetric-norming engine and never mentions the row's canonical statement,
    which is a different specialization of the same core.  Reporting that as a
    failed dependency check would make the ordinary case look like a defect, so
    a shared proof core is a verdict of its own -- and only a genuine absence of
    both relations is the finding worth showing.
    """
    if graph is None:
        return {front: {"state": "unknown"} for front in fronts}
    table = graph["table"]
    resolved = resolve_graph_name(table, name)
    from_decl, decl_capped = (
        ({}, False) if resolved is None else _reachable(table, resolved, FRONTS_SEARCH_DEPTH)
    )
    out: dict[str, dict[str, Any]] = {}
    for front in fronts:
        target = resolve_graph_name(table, front)
        if resolved is None or target is None:
            out[front] = {"state": "unknown"}
            continue
        if target in from_decl and target != resolved:
            out[front] = {"state": "delegates", "depth": from_decl[target]}
            continue
        from_front, front_capped = _reachable(table, target, FRONTS_SEARCH_DEPTH)
        shared = [n for n in from_decl if n in from_front and _is_proof_core(table, n)]
        if shared:
            via = min(shared, key=lambda n: (from_decl[n] + from_front[n], from_decl[n], n))
            out[front] = {"state": "shared-core", "via": via, "depth": from_decl[via]}
        elif decl_capped or front_capped:
            out[front] = {"state": "unknown"}
        else:
            out[front] = {"state": "independent"}
    return out


def _presentation_payload(
    packet: AlignmentPacket,
    item: Mapping[str, Any],
    review: Mapping[str, Any],
    context: Sequence[Mapping[str, Any]],
    graph: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """A presentation form, shown with the same panels as a canonical statement.

    It is the spelling the paper's reader recognises, so it is worth the same
    evidence -- source text, elaborated signature, closure, dependencies -- and
    not a footnote naming the declaration it fronts.
    """
    name = str(item["name"])
    fronts = [str(x) for x in item.get("fronts") or []]
    return _declaration_payload(packet, name, review, context, graph) | {
        "fronts": fronts,
        "relation": str(item.get("relation") or "unstated"),
        "devices": [str(x) for x in item.get("devices") or []],
        "why": str(item.get("why") or ""),
        "frontsVerified": fronts_verified(graph, name, fronts),
    }


def _proof_payload(graph: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    """Project-local proof dependencies of ``name`` from a saved leanq graph index."""
    from leanq.graph import target_dependency_graph

    table = graph["table"]
    resolved = resolve_graph_name(table, name)
    if resolved is None:
        return None
    name = resolved
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
    # One read per census, not one per row.
    inventories: dict[int, dict[str, dict[str, Any]]] = {}
    for entry in packet.entries:
        if id(entry.census) not in inventories:
            inventories[id(entry.census)] = load_result_inventory(entry.census)
        paper = papers.setdefault(
            entry.census.title, {"title": entry.census.title, "path": str(entry.census.path), "rows": []}
        )
        row, review = entry.row, entry.review
        canonical = [
            _declaration_payload(packet, name, review, entry.context, graph)
            for name in entry.canonical
        ]
        # Anything a clause points at is evidence a reviewer has to read, so it
        # gets a full panel rather than a bare name in a sentence -- the theorem
        # said to carry a representation change, and equally a supporting
        # declaration a clause names as its realization.
        presentation = [
            _presentation_payload(packet, item, review, entry.context, graph)
            for item in entry.presentation
        ]
        shown = set(entry.canonical) | {p["name"] for p in presentation}
        cited = [n for n in cited_declarations(review) if n not in shown]
        evidence = [
            _declaration_payload(packet, name, review, entry.context, graph)
            for name in cited
        ]
        variants = []
        for variant in packet.variants:
            if variant.parent_row is row:
                variant_sources, _ = _source_payload(packet, variant.review, row)
                variants.append({
                    "title": variant.title,
                    "claim": variant.review.get("claim"),
                    "sources": variant_sources,
                    "edges": [edge.as_json() for edge in edges_of(variant.review)],
                    "canonical": [
                        _declaration_payload(packet, name, variant.review, variant.context, graph)
                        for name in variant.canonical
                    ],
                })
        reached: set[str] = set()
        for decl in [*presentation, *canonical, *evidence]:
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
        sources, source_pin_summary = _source_payload(packet, review, row)
        paper["rows"].append({
            "evidence": evidence,
            "sources": sources,
            "sourcePinSummary": source_pin_summary,
            "sourceInterpretation": review.get("source_interpretation"),
            # A standalone review states a verdict on the whole row -- "REPAIR
            # source mismatch", and whether the printed statement is covered
            # literally. It is the most pointed thing on the row and an embedded
            # review has no field for it, so it is carried rather than dropped.
            "verdict": review.get("verdict"),
            "reviewerNote": review.get("reviewer_note"),
            "literalSourceCovered": review.get("literal_source_covered"),
            "fromCompanionReview": bool(review.get("companion")),
            "nonlocalRationale": review.get("nonlocal_rationale"),
            "edges": [edge.as_json() for edge in edges_of(review)],
            "id": str(row.get("id")),
            "anchor": f"{len(papers)}-{row.get('id')}",
            "paper": entry.census.title,
            "title": entry.title,
            "group": entry.group,
            "claim": review.get("claim") or row.get("summary") or row.get("source_claim"),
            "importance": row.get("importance"),
            "status": row.get("status"),
            "verification": row.get("verification"),
            "certification": _certification(row, inventories.get(id(entry.census), {})),
            "sourceAnchor": row.get("source_anchor"),
            "sourceStatement": review.get("source_statement"),
            "clauses": review.get("clause_map") or [],
            "note": review.get("note"),
            "uncurated": bool(review.get("uncurated")),
            "nextAction": row.get("next_action"),
            "presentation": presentation,
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
    relations: dict[str, Any] = {}
    for entry in packet.entries:
        relations.update(relation_legend(entry.census.data.get("relation_definitions")))
    return {
        "schemaVersion": 2,
        "payloadKind": "alignment-review",
        "title": title,
        "statementMeta": dict(packet.statement_meta),
        "graph": {"nodeCount": graph["nodeCount"], "path": graph.get("path")} if graph else None,
        "papers": list(papers.values()),
        "records": records,
        # The literature side: which documents were read, and the TeX macros the
        # page needs in order to render their mathematics.
        "sources": (packet.library.as_json(include_private=packet.include_private)
                    if packet.library else {"documents": [], "macros": {}}),
        "includesPrivate": packet.include_private,
        "relations": relations or relation_legend(),
    }


def render_alignment_html(payload: Mapping[str, Any]) -> str:
    """The review page, with the vendored math renderer inlined.

    ``math=True`` is what makes the page self-contained: the source passages are
    TeX, and a review artifact that needs a CDN to show its own mathematics is
    not one you can hand to a reviewer.
    """
    from .viewer import viewer_html

    return viewer_html("alignment_viewer.html", payload.get("title", ""), payload, math=True)


#: Default field names of the three completion axes in a result inventory.
CERTIFICATION_FIELDS = {
    "disposition": "disposition",
    "verification": "verification",
    "semantic": "semantic_certification",
    "note": "semantic_certification_note",
}


def load_result_inventory(census: CensusDocument) -> dict[str, dict[str, Any]]:
    """The result inventory a census points at, indexed by row id.

    A census row carries the *source-fidelity* bookkeeping for a whole passage.
    Whether the printed result it contains has passed hostile semantic review is
    a different judgement kept in a different file, and a browser that shows
    only the first can advertise `compiled_exact` for a row whose semantic
    correspondence is blocked.  A census says where the second lives:

    ``"result_inventory": {"path": "...json", "collection": "results"}``

    and this reads it.  Absent, or unreadable, the browser simply has one axis
    fewer -- an inventory is optional, and a broken pointer must not take the
    page down.
    """
    spec = census.data.get("result_inventory")
    if not isinstance(spec, Mapping) or not spec.get("path"):
        return {}
    path = census.path.parent / str(spec["path"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = data.get(str(spec.get("collection") or "results"))
    if not isinstance(rows, list):
        return {}
    key = str(spec.get("key") or "id")
    fields = dict(CERTIFICATION_FIELDS)
    if isinstance(spec.get("fields"), Mapping):
        fields.update({str(k): str(v) for k, v in spec["fields"].items()})
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not row.get(key):
            continue
        out[str(row[key])] = {
            axis: str(row.get(field) or "") for axis, field in fields.items()
        } | {"inventory": path.name}
    return out


def _certification(row: Mapping[str, Any], inventory: Mapping[str, Any]) -> dict[str, Any]:
    """The three completion axes of one row, as a reviewer must read them.

    ``status`` and ``verification`` used to be all a row showed, so a row whose
    hostile semantic review was blocked still read as `compiled_exact ·
    proved_in_build` until you opened it and found an `open` clause.  The
    semantic axis is the one a reviewer is entitled to see first.
    """
    entry = inventory.get(str(row.get("id"))) or {}
    out = {
        "disposition": entry.get("disposition") or row.get("status") or "",
        "verification": entry.get("verification") or row.get("verification") or "",
        "semantic": entry.get("semantic") or "",
        "semanticNote": entry.get("note") or "",
        # The census keeps its own passage-level bookkeeping, which is a
        # coarser thing than the printed result's semantic certification and
        # must not be shown as if it were the same judgement.
        "censusCertification": str(row.get("completion_certification") or ""),
        "inventory": entry.get("inventory") or "",
    }
    return {k: v for k, v in out.items() if v}


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
                # `open`, not `claimed_exact`: the clause says in the same breath
                # that no correspondence is registered, and an audit browser must
                # never let an unreviewed row inherit an exactness label merely to
                # satisfy a schema.
                "source_clause": summary,
                "lean_realization": "No curated correspondence is registered for this broader-tier row.",
                "status": "open",
                "note": "Uncurated: this clause is the census summary, not a reviewed correspondence.",
            }
        ],
        "note": "This row is outside the curated headline surface; showing the census fallback.",
        # A row nobody has reviewed and a row a reviewer left open are both
        # "not established", and a triage view that cannot tell them apart
        # reports the whole uncurated tail as if it were disputed.
        "uncurated": True,
    }


def collect_alignment_entries(
    censuses: Sequence[CensusDocument],
    *,
    importance: str = "headline",
    rows: Sequence[str] = (),
) -> list[AlignmentEntry]:
    """Rows to review: every row at or above ``importance``, or exactly ``rows``.

    Naming rows explicitly is how a reviewer builds a packet around a result
    whose source-fidelity importance is not "headline" -- Theorem 8.2 is a
    ``major`` row and Proposition 4.4 a ``supporting`` one, and neither should be
    promoted just to be looked at.
    """
    if importance not in IMPORTANCE_ORDER:
        raise ValueError(f"unknown importance threshold {importance!r}")
    threshold = IMPORTANCE_ORDER[importance]
    wanted = set(rows)
    entries: list[AlignmentEntry] = []
    for census in censuses:
        # Half this repository's papers keep the review in its own document
        # beside the census rather than inside the row. Looking only for the
        # embedded field rendered forty-three reviewed results as unreviewed.
        companion = companion_reviews(census.path, census.root)
        for row in census.items:
            rank = IMPORTANCE_ORDER.get(str(row.get("importance", "technical")), 3)
            if wanted:
                if str(row.get("id")) not in wanted:
                    continue
            elif rank > threshold:
                continue
            review = row.get("semantic_review")
            if not isinstance(review, dict):
                review = companion.get(str(row.get("id") or "")) or _fallback_review(row)
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
        queries.extend(("check", item["name"]) for item in entry.presentation)
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
        names.extend(item["name"] for item in entry.presentation)
        names.extend(entry.canonical)
        names.extend(entry.supporting)
        names.extend(cited_declarations(entry.review))
    for variant in variants:
        names.extend(variant.canonical)
        names.extend(variant.supporting)
        names.extend(cited_declarations(variant.review))
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
    sources: SourceLibrary | None = None,
    include_private: bool = False,
    rows: Sequence[str] = (),
    source_index: LeanSourceIndex | None = None,
    statement_map: Mapping[str, Any] | None = None,
    proof_cache: dict[str, Any] | None = None,
) -> AlignmentPacket:
    censuses = [load_census(path, root=root) for path in census_paths]
    if not censuses:
        raise ValueError("at least one census is required")
    base = censuses[0].root
    entries = collect_alignment_entries(censuses, importance=importance, rows=rows)
    variants = collect_alignment_variants(entries)
    # A caller that already holds a scan -- the server does -- passes it in
    # rather than paying for a second walk of every Lean file.
    if source_index is None:
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
    statements_out: dict[str, Any] = dict(statement_map or {})
    statement_meta: dict[str, Any] = {}
    if statement_map is None and (statements or sidecar is not None):
        seeds: list[str] = []
        for entry in entries:
            seeds.extend(item["name"] for item in entry.presentation)
            seeds.extend(entry.canonical)
            seeds.extend(entry.supporting)
            seeds.extend(str(item["name"]) for item in entry.context)
            # A correspondence lemma is the evidence that a representation
            # change is legitimate, so the page has to be able to show its
            # elaborated statement even when the row does not register it.
            seeds.extend(cited_declarations(entry.review))
        for variant in variants:
            seeds.extend(variant.canonical)
            seeds.extend(variant.supporting)
            seeds.extend(str(item["name"]) for item in variant.context)
            seeds.extend(cited_declarations(variant.review))
        statements_out, statement_meta = statement_records(
            base, unique_in_order(seeds), sidecar=sidecar, library=library, refresh=refresh,
        )
    return AlignmentPacket(
        base,
        entries,
        probe_map,
        import_list,
        variants=variants,
        source_declarations=source_declarations,
        statements=statements_out,
        statement_meta=statement_meta,
        library=sources,
        include_private=include_private,
        proof_cache=proof_cache if proof_cache is not None else {},
    )

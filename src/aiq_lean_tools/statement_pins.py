"""Pin the elaborated statement a semantic review accepted, and notice when it moves.

A review says "this Lean declaration states the paper's result".  That claim is
about the declaration's *type* on the day the reviewer read it.  Nothing in a
green build notices when the type changes afterwards -- a hypothesis added, a
norm class narrowed, a conclusion weakened -- so the review keeps reading as
accepted while describing something that is no longer there.

A pin records two hashes from the ``leanq`` statement sidecar:

- ``type_expr_hash``: the elaborator's structural hash of the type.  It ignores
  binder names and notation, so a cosmetic rename does not move it.
- ``type_text_sha256``: a hash of the printed type.  It moves whenever what a
  reader sees moves.

``check`` compares both against a fresh sidecar.  A moved structural hash is a
drift error: the reviewed statement changed.  A moved text hash alone is a
warning: the term is the same but its rendering is not, which is usually a
Mathlib or toolchain change worth a glance.

The mechanism reads and writes the ``statement_pins`` list of an embedded
``semantic_review`` or of a standalone review row.  Which declarations a row
pins is the row's own claim: the canonical declarations of an embedded review
together with every declaration its clause map names as correspondence or
transport evidence, and every registered declaration of a standalone review row.
"""
from __future__ import annotations

import datetime as _dt
import pathlib
import sys
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .common import Finding, Path
from .semantic_surface import normalize_presentation

try:  # leanq ships in the same distribution; keep the import failure explicit
    from leanq.statement import (
        StatementRecord,
        by_name as _statements_by_name,
        ensure_statement_sidecar,
        load_sidecar_meta,
        load_statement_sidecar,
    )
    from leanq.project import LeanProject, ProjectError, find_project
except ImportError as exc:  # pragma: no cover - packaging guard
    raise ImportError("statement pins need the leanq package from the same distribution") from exc

PIN_FIELD = "statement_pins"


@dataclass(frozen=True)
class PinTarget:
    """One row that can carry pins, with the declarations its review claims."""

    document: str
    row_id: str
    container: dict[str, Any]
    declarations: tuple[str, ...]

    @property
    def location(self) -> str:
        return f"{self.document}:{self.row_id}"

    @property
    def pins(self) -> list[dict[str, Any]]:
        pins = self.container.get(PIN_FIELD)
        return pins if isinstance(pins, list) else []


#: Clause-map fields whose declarations a review claims as *bridging* evidence:
#: theorems asserted to carry a correspondence or a transport between the
#: object a canonical theorem is stated on and the object the source names.
CLAUSE_BRIDGE_FIELDS = ("correspondence_declarations", "transport_declarations")


def claimed_pin_declarations(review: Mapping[str, Any]) -> tuple[str, ...]:
    """The declarations an embedded review claims, in claim order.

    The canonical declarations first, then the presentation declarations -- the
    restatements a reviewer accepted as reading like the printed theorem, which
    drift exactly as a canonical one does -- then every declaration a clause of
    the review names as bridging evidence (``correspondence_declarations`` and
    ``transport_declarations``).  A clause that says "this other theorem carries
    the correspondence" is making a claim about that theorem's type, and a review
    whose bridge silently changed shape has been accepted over a chain that no
    longer composes -- so those are pinned with the canonical ones.  Supporting
    declarations that no clause relies on are not claimed.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(name: object) -> None:
        text = str(name)
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    for name in review.get("canonical_declarations") or review.get("declarations") or []:
        add(name)
    for entry in normalize_presentation(review):
        add(entry["name"])
    for clause in review.get("clause_map") or []:
        if not isinstance(clause, Mapping):
            continue
        for field in CLAUSE_BRIDGE_FIELDS:
            for name in clause.get(field) or []:
                add(name)
    return tuple(out)


def census_pin_targets(census, *, row_ids: Sequence[str] = ()) -> list[PinTarget]:
    """Rows of a census with an embedded review; pins live inside that review.

    A row claims its canonical declarations and every declaration its clause map
    names as bridging evidence; see :func:`claimed_pin_declarations`.
    """
    wanted = set(row_ids)
    out: list[PinTarget] = []
    for row in census.items:
        rid = str(row.get("id", ""))
        if wanted and rid not in wanted:
            continue
        review = row.get("semantic_review")
        if not isinstance(review, dict):
            continue
        decls = claimed_pin_declarations(review)
        if not decls:
            continue
        out.append(PinTarget(str(census.path), rid, review, decls))
    return out


def review_pin_targets(review_doc, *, row_ids: Sequence[str] = ()) -> list[PinTarget]:
    """Rows of a standalone review; pins live on the row itself."""
    wanted = set(row_ids)
    out: list[PinTarget] = []
    for row in review_doc.rows:
        rid = str(row.get("id", ""))
        if wanted and rid not in wanted:
            continue
        decls = tuple(str(x) for x in row.get("lean_declarations", []) or [])
        if not decls:
            continue
        out.append(PinTarget(str(review_doc.path), rid, row, decls))
    return out


def validate_pins(
    container: Mapping[str, Any],
    *,
    claimed: Sequence[str],
    location: str,
) -> list[Finding]:
    """Structural validation of a ``statement_pins`` list.

    A pin must name a declaration the review actually claims, and carry both
    hashes.  Whether the hashes are *current* is a compiler question answered by
    :func:`check_pins`, not here.
    """
    findings: list[Finding] = []
    pins = container.get(PIN_FIELD)
    if pins is None:
        return findings
    if not isinstance(pins, list):
        return [Finding("error", "statement-pins", f"{PIN_FIELD} must be a list", location)]
    claimed_set = set(claimed)
    seen: set[str] = set()
    for index, pin in enumerate(pins):
        loc = f"{location}.{PIN_FIELD}[{index}]"
        if not isinstance(pin, Mapping):
            findings.append(Finding("error", "statement-pin", "pin must be an object", loc))
            continue
        name = pin.get("declaration")
        if not isinstance(name, str) or not name:
            findings.append(Finding("error", "statement-pin", "pin needs a declaration", loc))
            continue
        if name in seen:
            findings.append(Finding("error", "statement-pin", f"duplicate pin for {name}", loc))
        seen.add(name)
        if claimed_set and name not in claimed_set:
            findings.append(
                Finding(
                    "error", "statement-pin",
                    f"pinned declaration {name} is not one the review claims", loc,
                )
            )
        for key in ("type_expr_hash", "type_text_sha256"):
            if not isinstance(pin.get(key), str) or not pin[key]:
                findings.append(Finding("error", "statement-pin", f"pin needs {key}", loc))
    return findings


# ---------------------------------------------------------------------------
# Sidecar access


def _seed_modules(project: LeanProject, seeds: Sequence[str]) -> tuple[list[str], list[str]]:
    """Modules to import for ``seeds``, and the seeds the source scan could not place.

    Every candidate module for an ambiguous short name is kept: the environment
    only has to *contain* the declaration, and the elaborator resolves the full
    name exactly.  A seed with no source module at all -- a Lake package constant
    such as ``TauCeti.LinearPMap.realSpectrum`` cited as review context -- is
    normally in the transitive imports of the seeds that were placed; if it is
    not, its record comes back ``missing`` and the report says so.
    """
    modules: list[str] = []
    unplaced: list[str] = []
    for seed in seeds:
        found = project.candidate_declaration_modules(seed)
        if found:
            modules.extend(found)
        else:
            unplaced.append(seed)
    modules = list(dict.fromkeys(modules))
    # A module with source but no build artifact -- a Challenge library kept out
    # of the default targets, say -- cannot be imported, and one such module
    # would abort the whole import.  Leave it out; a seed that only lived there
    # comes back `missing`, which is the truthful answer about the build.
    unbuilt = set(project.unavailable_import_roots(modules))
    if unbuilt:
        print(
            f"leanq: skipping {len(unbuilt)} unbuilt module(s) a seed is declared in, "
            f"e.g. {sorted(unbuilt)[0]}",
            file=sys.stderr,
        )
    return [m for m in modules if m not in unbuilt], unplaced


def statement_records(
    root: str | pathlib.Path,
    seeds: Sequence[str],
    *,
    sidecar: str | pathlib.Path | None = None,
    library: str | None = None,
    refresh: bool = False,
    verbose: bool = True,
) -> tuple[dict[str, StatementRecord], dict[str, Any]]:
    """Statement records for ``seeds``, from an explicit sidecar or a fresh one.

    An explicit sidecar (a whole-library one built earlier, say) is read as-is
    and never invokes Lean, which is the path a test or a fast gate uses.
    """
    if sidecar is not None:
        path = Path(sidecar)
        records = load_statement_sidecar(path)
        return _statements_by_name(records), load_sidecar_meta(path)
    project = find_project(Path(root))
    seeds = list(dict.fromkeys(seeds))
    modules, unplaced = _seed_modules(project, seeds)
    if library is None:
        if modules:
            library = project.library_for_module(modules[0])
        if library is None:
            libs = project.libraries()
            if len(libs) != 1:
                raise ProjectError(
                    "cannot infer the library for the statement sidecar; pass --lib"
                )
            library = libs[0]
    if not modules:
        modules = project.modules(library)
    if unplaced and verbose:
        print(
            f"leanq: {len(unplaced)} seed(s) have no project source module and are expected "
            f"in the imports of the placed ones, e.g. {unplaced[0]}",
            file=sys.stderr,
        )
    path, records = ensure_statement_sidecar(
        project, library, seeds=seeds, modules=modules, refresh=refresh, verbose=verbose,
    )
    return _statements_by_name(records), load_sidecar_meta(path)


# ---------------------------------------------------------------------------
# Pin and check


def make_pin(record: StatementRecord, *, toolchain: str = "", note: str = "") -> dict[str, Any]:
    pin = {
        "declaration": record.name,
        "type_expr_hash": record.type_expr_hash,
        "type_text_sha256": record.type_text_sha256,
        "toolchain": toolchain,
        "pinned_on": _dt.date.today().isoformat(),
    }
    if note:
        pin["note"] = note
    return pin


def pin_targets(
    targets: Sequence[PinTarget],
    records: Mapping[str, StatementRecord],
    *,
    toolchain: str = "",
    note: str = "",
) -> tuple[int, list[Finding]]:
    """Write or refresh pins on every target.  Returns (pins written, findings)."""
    written = 0
    findings: list[Finding] = []
    for target in targets:
        pins: list[dict[str, Any]] = []
        for name in target.declarations:
            record = records.get(name)
            if record is None or record.missing:
                findings.append(
                    Finding(
                        "error", "statement-pin-missing",
                        f"no statement record for {name}; is the name fully qualified and built?",
                        target.location,
                    )
                )
                continue
            existing = next(
                (p for p in target.pins if isinstance(p, Mapping) and p.get("declaration") == name),
                None,
            )
            pin = make_pin(record, toolchain=toolchain, note=note)
            if existing and not note and existing.get("note"):
                pin["note"] = existing["note"]
            pins.append(pin)
            written += 1
        target.container[PIN_FIELD] = pins
    return written, findings


def check_pins(
    targets: Sequence[PinTarget],
    records: Mapping[str, StatementRecord],
) -> list[Finding]:
    """Compare every pin with the current statement records.

    - missing declaration: error ``statement-pin-gone``;
    - structural hash moved: error ``statement-drift``;
    - only the printed text moved: warning ``statement-text-drift``;
    - a claimed declaration with no pin: warning ``statement-unpinned``.
    """
    findings: list[Finding] = []
    for target in targets:
        pinned = {
            str(p.get("declaration")): p for p in target.pins if isinstance(p, Mapping)
        }
        for name in target.declarations:
            if name not in pinned:
                findings.append(
                    Finding("warning", "statement-unpinned", f"{name} has no statement pin",
                            target.location)
                )
        for name, pin in pinned.items():
            record = records.get(name)
            if record is None or record.missing:
                findings.append(
                    Finding("error", "statement-pin-gone",
                            f"pinned declaration {name} is not in the environment",
                            target.location)
                )
                continue
            if str(pin.get("type_expr_hash")) != record.type_expr_hash:
                findings.append(
                    Finding(
                        "error", "statement-drift",
                        f"{name}: elaborated type changed since it was pinned "
                        f"({pin.get('pinned_on', '?')}); re-review and re-pin",
                        target.location,
                    )
                )
            elif str(pin.get("type_text_sha256")) != record.type_text_sha256:
                findings.append(
                    Finding(
                        "warning", "statement-text-drift",
                        f"{name}: printed type changed but the elaborated term did not "
                        f"(pinned {pin.get('pinned_on', '?')})",
                        target.location,
                    )
                )
    return findings


def pin_status(pin: Mapping[str, Any] | None, record: StatementRecord | None) -> str:
    """One word for a packet: ``unpinned``, ``current``, ``text-drift``, ``drift``, ``gone``."""
    if pin is None:
        return "unpinned"
    if record is None or record.missing:
        return "gone"
    if str(pin.get("type_expr_hash")) != record.type_expr_hash:
        return "drift"
    if str(pin.get("type_text_sha256")) != record.type_text_sha256:
        return "text-drift"
    return "current"

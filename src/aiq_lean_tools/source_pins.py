"""Pin the source passage a semantic review accepted, and notice when it moves.

Statement pins record the elaborated Lean type a review was reading.  They say
nothing about the other half of the claim.  A review is equally a statement
about the *paper* on the day it was read, and the checked-in reconstruction of
that paper is an ordinary file that gets edited: a hypothesis rephrased, a
displayed equation corrected, a scope sentence added.  Nothing noticed.

A source pin records the content hash of the fragment a review cited.  When the
passage changes, the review is flagged for re-reading -- exactly as a moved
elaborated type flags it.

The two are deliberately separate evidence layers, and a viewer shows them
separately:

    source fragment   pinned / current / moved
    Lean statement    pinned / current / moved
    correspondence    a human judgment about the pair

A private fragment can be pinned like any other: the hash is derived from local
text but is not the text, so recording it publishes nothing.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .common import Finding
from .correspondence import declared_fragments
from .source_model import SourceFragment, SourceLibrary, SourceLocator

SOURCE_PIN_FIELD = "source_pins"


@dataclass(frozen=True)
class SourcePinTarget:
    """One review that declares source fragments, and can pin them."""

    document: str
    row_id: str
    container: dict[str, Any]

    @property
    def location(self) -> str:
        return f"{self.document}:{self.row_id}"

    @property
    def fragments(self) -> list[dict[str, Any]]:
        return declared_fragments(self.container)

    @property
    def pins(self) -> list[dict[str, Any]]:
        pins = self.container.get(SOURCE_PIN_FIELD)
        return pins if isinstance(pins, list) else []


def census_source_targets(census, *, row_ids: Sequence[str] = ()) -> list[SourcePinTarget]:
    wanted = set(row_ids)
    out: list[SourcePinTarget] = []
    for row in census.items:
        rid = str(row.get("id", ""))
        if wanted and rid not in wanted:
            continue
        review = row.get("semantic_review")
        if isinstance(review, dict) and declared_fragments(review):
            out.append(SourcePinTarget(str(census.path), rid, review))
    return out


def review_source_targets(review_doc, *, row_ids: Sequence[str] = ()) -> list[SourcePinTarget]:
    wanted = set(row_ids)
    out: list[SourcePinTarget] = []
    for row in review_doc.rows:
        rid = str(row.get("id", ""))
        if wanted and rid not in wanted:
            continue
        if declared_fragments(row):
            out.append(SourcePinTarget(str(review_doc.path), rid, row))
    return out


def validate_source_pins(
    container: Mapping[str, Any], *, location: str
) -> list[Finding]:
    """Structural checks only; whether a hash is *current* is a resolver question."""
    pins = container.get(SOURCE_PIN_FIELD)
    if pins is None:
        return []
    if not isinstance(pins, list):
        return [Finding("error", "source-pins", f"{SOURCE_PIN_FIELD} must be a list", location)]
    declared = {str(f.get("id")) for f in declared_fragments(container) if f.get("id")}
    findings: list[Finding] = []
    seen: set[str] = set()
    for index, pin in enumerate(pins):
        loc = f"{location}.{SOURCE_PIN_FIELD}[{index}]"
        if not isinstance(pin, Mapping):
            findings.append(Finding("error", "source-pin", "pin must be an object", loc))
            continue
        fid = str(pin.get("fragment") or "")
        if not fid:
            findings.append(Finding("error", "source-pin", "pin needs a fragment id", loc))
            continue
        if fid in seen:
            findings.append(Finding("error", "source-pin", f"duplicate pin for {fid}", loc))
        seen.add(fid)
        if declared and fid not in declared:
            findings.append(Finding(
                "error", "source-pin",
                f"pinned fragment {fid} is not one the review declares", loc,
            ))
        if not str(pin.get("sha256") or ""):
            findings.append(Finding("error", "source-pin", "pin needs sha256", loc))
    return findings


def make_source_pin(fragment: SourceFragment, *, note: str = "") -> dict[str, Any]:
    pin = {
        "fragment": fragment.id,
        "document": fragment.document,
        "sha256": fragment.sha256,
        "locator": fragment.locator.as_json(),
        "visibility": fragment.visibility,
        "pinned_on": _dt.date.today().isoformat(),
    }
    if note:
        pin["note"] = note
    return pin


def source_pin_status(pin: Mapping[str, Any] | None, fragment: SourceFragment | None) -> str:
    """One word: ``unpinned``, ``current``, ``moved``, ``unresolved``."""
    if fragment is None:
        return "unresolved"
    if pin is None:
        return "unpinned"
    return "current" if str(pin.get("sha256")) == fragment.sha256 else "moved"


def resolve_target(
    target: SourcePinTarget, library: SourceLibrary
) -> dict[str, SourceFragment]:
    """Resolve every fragment a target declares, keyed by fragment id."""
    out: dict[str, SourceFragment] = {}
    for spec in target.fragments:
        fid = str(spec.get("id") or "")
        if not fid:
            continue
        try:
            locator = SourceLocator.parse(spec.get("locator"))
        except Exception:
            continue
        fragment = library.resolve(locator, id=fid, role=str(spec.get("role") or "primary"))
        if fragment is not None:
            out[fid] = fragment
    return out


def pin_source_targets(
    targets: Sequence[SourcePinTarget], library: SourceLibrary, *, note: str = ""
) -> tuple[int, list[Finding]]:
    written = 0
    findings: list[Finding] = []
    for target in targets:
        resolved = resolve_target(target, library)
        pins: list[dict[str, Any]] = []
        for spec in target.fragments:
            fid = str(spec.get("id") or "")
            fragment = resolved.get(fid)
            if fragment is None:
                findings.append(Finding(
                    "error", "source-pin-unresolved",
                    f"fragment {fid} does not resolve to any source document",
                    target.location,
                ))
                continue
            existing = next(
                (p for p in target.pins if isinstance(p, Mapping) and p.get("fragment") == fid),
                None,
            )
            pin = make_source_pin(fragment, note=note)
            if existing and not note and existing.get("note"):
                pin["note"] = existing["note"]
            pins.append(pin)
            written += 1
        target.container[SOURCE_PIN_FIELD] = pins
    return written, findings


def check_source_targets(
    targets: Sequence[SourcePinTarget], library: SourceLibrary
) -> list[Finding]:
    """Compare every source pin against the source documents as they stand now."""
    findings: list[Finding] = []
    for target in targets:
        resolved = resolve_target(target, library)
        pinned = {
            str(p.get("fragment")): p for p in target.pins if isinstance(p, Mapping)
        }
        for spec in target.fragments:
            fid = str(spec.get("id") or "")
            if fid and fid not in pinned:
                findings.append(Finding(
                    "warning", "source-unpinned",
                    f"source fragment {fid} has no pin", target.location,
                ))
        for fid, pin in pinned.items():
            fragment = resolved.get(fid)
            status = source_pin_status(pin, fragment)
            if status == "unresolved":
                findings.append(Finding(
                    "error", "source-pin-unresolved",
                    f"pinned fragment {fid} no longer resolves; the marker or line range is gone",
                    target.location,
                ))
            elif status == "moved":
                findings.append(Finding(
                    "error", "source-drift",
                    f"{fid}: the source passage changed since it was pinned "
                    f"({pin.get('pinned_on', '?')}); re-read the passage and re-pin",
                    target.location,
                ))
    return findings

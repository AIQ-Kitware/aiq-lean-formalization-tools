"""Which ledgers mention a given declaration, and where.

The viewers were built one document at a time, so a declaration that a census
registers, a semantic review adjudicates, and a coverage inventory counts
appeared three times with nothing joining them. This builds that join: one index
from a Lean declaration name to every row of every ledger that names it.

Names are collected from the fields that actually carry them --
``lean_declarations``, the ``*_declarations`` object lists, and the
``declaration`` key that pins and coverage atoms use -- rather than by pattern
matching every string, which would sweep up prose that merely mentions a name.

Each occurrence remembers the nearest enclosing row, so a hit is something the
UI can navigate to rather than a bare path into a JSON blob.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Iterator

#: Keys whose values are declaration names (a string, or a list of strings).
NAME_FIELDS = ("lean_declarations", "declaration", "theorem_names", "declarations")

#: Keys whose values are lists of objects carrying a ``name``.
NAMED_OBJECT_FIELDS = (
    "canonical_declarations",
    "supporting_declarations",
    "presentation_declarations",
    "context_declarations",
    "canonical",
    "supporting",
    "context",
    "statement_pins",
)

#: A dotted Lean identifier. Deliberately strict: prose sentences and file paths
#: must not qualify.
LEAN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_'₀-₉!?]*(\.[A-Za-z0-9_'₀-₉!?]+)+$")


def _is_name(value: Any) -> bool:
    return isinstance(value, str) and 3 < len(value) < 200 and bool(LEAN_NAME.match(value))


def _row_identity(node: dict) -> tuple[str, str] | None:
    """``(id, title)`` when this dict looks like a navigable row."""
    rid = node.get("id")
    if not isinstance(rid, str) or not rid:
        return None
    for key in ("title", "source_anchor", "claim", "summary"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return rid, value.strip()
    return rid, rid


def _walk(node: Any, row: tuple[str, str] | None, field: str = "") -> Iterator[tuple[str, str, tuple[str, str] | None]]:
    """Yield ``(name, field, row)`` for every declaration name in ``node``."""
    if isinstance(node, dict):
        here = _row_identity(node) or row
        for key, value in node.items():
            if key in NAME_FIELDS:
                if _is_name(value):
                    yield value, key, here
                elif isinstance(value, list):
                    for item in value:
                        if _is_name(item):
                            yield item, key, here
                        elif isinstance(item, dict) and _is_name(item.get("name")):
                            yield item["name"], key, here
            elif key in NAMED_OBJECT_FIELDS and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        for sub in ("name", "declaration"):
                            if _is_name(item.get(sub)):
                                yield item[sub], key, here
                                break
                        # A presentation form names the canonical statement it
                        # fronts, which is a mention of that name as much as of
                        # its own.
                        fronts = item.get("fronts")
                        if isinstance(fronts, list):
                            for front in fronts:
                                if _is_name(front):
                                    yield front, key, here
                    elif _is_name(item):
                        yield item, key, here
            else:
                yield from _walk(value, here, key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, row, field)


class Xref:
    """Declaration name -> the rows that name it, across every ledger."""

    def __init__(self) -> None:
        self.by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._stamps: dict[tuple[str, str], Any] = {}

    def add_document(self, view: str, slug: str, document: str, payload: Any, stamp: Any) -> None:
        key = (view, slug)
        if self._stamps.get(key) == stamp:
            return
        self.drop_document(view, slug)
        self._stamps[key] = stamp
        seen: set[tuple[str, str, str]] = set()
        for name, field, row in _walk(payload, None):
            row_id, row_title = row if row else ("", "")
            dedupe = (name, row_id, field)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            self.by_name[name].append(
                {
                    "view": view,
                    "slug": slug,
                    "document": document,
                    "row": row_id,
                    "row_title": row_title,
                    "field": field,
                }
            )

    def drop_document(self, view: str, slug: str) -> None:
        self._stamps.pop((view, slug), None)
        for name in list(self.by_name):
            kept = [o for o in self.by_name[name] if not (o["view"] == view and o["slug"] == slug)]
            if kept:
                self.by_name[name] = kept
            else:
                del self.by_name[name]

    # -- queries -----------------------------------------------------------

    def occurrences(self, name: str) -> list[dict[str, Any]]:
        return list(self.by_name.get(name, ()))

    def shared(self, minimum: int = 2) -> list[dict[str, Any]]:
        """Declarations that more than one ledger names -- the real join points."""
        out = []
        for name, occ in self.by_name.items():
            docs = {(o["view"], o["slug"]) for o in occ}
            if len(docs) >= minimum:
                out.append({"name": name, "documents": len(docs), "occurrences": len(occ)})
        out.sort(key=lambda r: (-r["documents"], -r["occurrences"], r["name"]))
        return out

    def search(self, needle: str, limit: int = 40) -> list[dict[str, Any]]:
        needle = needle.lower()
        names = [n for n in self.by_name if needle in n.lower()]
        # Prefer a name that starts with the query, then a shorter name, so
        # `sinTheta` offers `sinTheta_complex` before a long derived variant.
        names.sort(key=lambda n: (not n.lower().split(".")[-1].startswith(needle), len(n), n))
        return [
            {"name": n, "occurrences": len(self.by_name[n]), "documents": len({(o["view"], o["slug"]) for o in self.by_name[n]})}
            for n in names[:limit]
        ]

    def stats(self) -> dict[str, Any]:
        return {
            "declarations": len(self.by_name),
            "occurrences": sum(len(v) for v in self.by_name.values()),
            "shared": len(self.shared()),
        }

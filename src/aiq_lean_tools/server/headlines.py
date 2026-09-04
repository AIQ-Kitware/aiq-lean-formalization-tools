"""The headline results of every paper, in one list.

A census answers questions about one paper, and the landing page has to ask a
different one: across everything this repository formalizes, which results are
the ones a reader came for? Each census marks those with ``importance:
headline`` -- four in Davis--Kahan, two in Yu--Wang--Samworth -- so the index is
a filter, not a judgement.

What makes it worth its own module is that the answer is assembled from three
places. The census file holds the claim and the row's verdicts, but the census
*loader* drops ``semantic_review``, which is where the canonical declarations
and the clause-by-clause review live -- so the raw JSON is read here directly.
The Lean source index says where each canonical declaration is written, and the
statement sidecars say what it elaborated to. Neither of the latter two is
guaranteed to exist: a fresh server has not finished its source scan and a tree
nobody has elaborated has no sidecars at all. Missing evidence degrades a field
to absent rather than failing the index, because a landing page that 500s until
a twenty-second scan finishes is worse than one that fills in.

Two caches, both keyed on what they are made of. The parsed census JSON is kept
on the file's stat, and the built index on the censuses together with the source
and statement revisions -- so an edit to a ledger or a Lean file invalidates it,
and a warm server answers the landing page from memory. Keying on the revisions
rather than merely on the censuses is what lets the Lean evidence fill in: it
starts empty on a cold server, and a cache that ignored it would pin that first,
emptiest answer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from ..lean_source import declaration_statement_text

#: Clause verdicts that report the clause as realized in Lean. Everything else
#: -- ``scope_companion``, a gap, an empty status -- is open work, and the
#: landing page counts it as such rather than guessing which flavour of
#: not-yet-exact it is.
SETTLED_CLAUSE_STATUSES = frozenset({"claimed_exact", "exact", "established", "accepted"})

#: Docstrings run to paragraphs; a card shows the opening.
DOCSTRING_CHARS = 240

#: How many canonical declarations a row reports with their source text. A
#: reviewed row curates one to six; a row with no semantic review falls back to
#: its whole registration list, which reaches sixty-six -- reading sixty-six
#: statements off disk to render a card nobody scrolls is the wrong trade.
#: ``canonicalTotal`` reports what was there.
CANONICAL_SHOWN = 6


class HeadlineService:
    """The cross-paper headline index, assembled from the censuses on disk."""

    def __init__(self, root: Path, catalog: Any, declarations: Any) -> None:
        self.root = Path(root)
        self.catalog = catalog
        self.declarations = declarations
        self._raw: dict[Path, tuple[tuple[int, int], list[tuple[int, dict[str, Any]]]]] = {}
        self._built: dict[tuple[str, ...] | None, tuple[tuple, list[dict[str, Any]]]] = {}

    # -- inputs ------------------------------------------------------------

    def _censuses(self) -> list[Any]:
        return [doc for doc in self.catalog.documents() if doc.view == "census"]

    def _items(self, path: Path) -> list[tuple[int, dict[str, Any]]]:
        """Raw census rows with their position in the file, cached on its stat.

        The position is carried because an annotation addresses a row by JSON
        pointer -- ``/items/7/...`` -- and a pointer computed from a filtered
        list would write to a different row than the one on screen.

        Read raw rather than through ``load_census`` because the loader strips
        ``semantic_review``, which is the half of the row this index is for.
        """
        try:
            st = path.stat()
        except OSError:
            return []
        stamp = (st.st_mtime_ns, st.st_size)
        hit = self._raw.get(path)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = [(i, row) for i, row in enumerate(data.get("items") or [])
                 if isinstance(row, dict)]
        self._raw[path] = (stamp, items)
        return items

    def _source_index(self) -> Any:
        # Seconds of tree walk on a cold server, and it may fail outright when
        # there is no Lean tree; an entry without a file location is still useful.
        try:
            return self.declarations.source_index()
        except Exception:
            return None

    def _statements(self) -> dict[str, Any]:
        # Present only once something has elaborated the declarations.
        try:
            return self.declarations.statements()
        except Exception:
            return {}

    # -- the answer --------------------------------------------------------

    def revision(self) -> tuple:
        """A stat stamp of every census read, for a cache key or an ETag.

        Stat only: this is computed per request, so it must not reparse
        anything, and it must move exactly when a census file does.
        """
        out = []
        for doc in self._censuses():
            try:
                st = doc.path.stat()
                out.append((doc.slug, st.st_mtime_ns, st.st_size))
            except OSError:
                out.append((doc.slug, 0, 0))
        return tuple(sorted(out))

    def _stamp(self) -> tuple:
        """Every input the built index is made of, at the revision it is made from."""
        revisions = []
        for name in ("source_revision", "statement_revision"):
            probe = getattr(self.declarations, name, None)
            try:
                revisions.append(probe() if probe is not None else None)
            except Exception:
                revisions.append(None)
        return (self.revision(), tuple(revisions))

    def entries(self, importance: tuple[str, ...] = ("headline",)) -> list[dict[str, Any]]:
        """Every row at one of the given importances, in paper then file order."""
        key = tuple(sorted(importance))
        stamp = self._stamp()
        hit = self._built.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        rows = self._build(set(importance))
        if len(self._built) > 8:
            self._built.clear()
        self._built[key] = (stamp, rows)
        return rows

    def entry(self, slug: str, row_id: str) -> dict[str, Any] | None:
        """One row by paper and id, at any importance.

        Not filtered by importance: a link handed out while a row was a
        headline should keep resolving after the row is demoted.
        """
        for row in self._build(None, only_slug=slug):
            if row["id"] == row_id:
                return row
        return None

    def _build(
        self,
        importance: set[str] | None,
        *,
        only_slug: str | None = None,
    ) -> list[dict[str, Any]]:
        index = self._source_index()
        statements = self._statements()
        out: list[dict[str, Any]] = []
        for doc in self._censuses():
            if only_slug is not None and doc.slug != only_slug:
                continue
            for _, item in self._items(doc.path):
                if importance is not None and item.get("importance") not in importance:
                    continue
                out.append(self._entry(doc, item, index, statements))
        return out

    def _entry(
        self,
        doc: Any,
        item: dict[str, Any],
        index: Any,
        statements: dict[str, Any],
    ) -> dict[str, Any]:
        review = item.get("semantic_review")
        review = review if isinstance(review, dict) else {}
        clauses = [c for c in review.get("clause_map") or [] if isinstance(c, dict)]
        # Most censuses carry no semantic review at all, so the row's own
        # declaration list is the only statement of what answers it.
        canonical = review.get("canonical_declarations") or item.get("lean_declarations") or []
        return {
            "paper": doc.title,
            # A census titles itself with the paper's full citation, which is
            # right on the page about that paper and useless as a heading over a
            # list of six. The slug is the name a human gave the file.
            "paperShort": short_paper_name(doc.slug),
            "slug": doc.slug,
            "view": "census",
            "id": str(item.get("id") or ""),
            "title": item.get("title") or "",
            "claim": review.get("claim") or "",
            "group": review.get("group") or "",
            "importance": item.get("importance") or "",
            "status": item.get("status") or "",
            "verification": item.get("verification") or "",
            "certification": item.get("completion_certification") or "",
            "sourceAnchor": item.get("source_anchor") or "",
            "section": str(item.get("section") or ""),
            "summary": item.get("summary") or "",
            "nextAction": item.get("next_action") or "",
            "clauseCount": len(clauses),
            "clauseOpen": sum(1 for c in clauses if c.get("status") not in SETTLED_CLAUSE_STATUSES),
            "canonicalTotal": len(_names(canonical)),
            "canonical": [
                self._declaration(name, index, statements)
                for name in _names(canonical)[:CANONICAL_SHOWN]
            ],
            "supporting": _names(review.get("supporting_declarations") or []),
        }

    def context(self, name: str) -> list[dict[str, Any]]:
        """Every census row this declaration answers for, with its prose.

        The audit page asks the question the ledgers exist to answer -- what
        printed result is this Lean statement for, and clause by clause, which
        sentence of it does which part? A clause carries the JSON pointer that
        addresses it, so the same panel that shows the correspondence can
        record a reviewer's verdict on it.
        """
        out: list[dict[str, Any]] = []
        for doc in self._censuses():
            for pos, item in self._items(doc.path):
                review = item.get("semantic_review")
                review = review if isinstance(review, dict) else {}
                role = _role_of(name, item, review)
                if role is None:
                    continue
                clauses = []
                for i, clause in enumerate(review.get("clause_map") or []):
                    if not isinstance(clause, dict):
                        continue
                    clauses.append({
                        "index": i,
                        "sourceClause": clause.get("source_clause") or "",
                        "leanRealization": clause.get("lean_realization") or "",
                        "status": clause.get("status") or "",
                        "kind": clause.get("kind") or "",
                        "relation": clause.get("relation") or "",
                        "sourceExcerpt": clause.get("source_excerpt") or "",
                        "pointer": f"/items/{pos}/semantic_review/clause_map/{i}/status",
                    })
                out.append({
                    "paper": doc.title,
                    "view": "census",
                    "slug": doc.slug,
                    "id": str(item.get("id") or ""),
                    "title": item.get("title") or "",
                    "role": role,
                    "claim": review.get("claim") or item.get("summary") or "",
                    "importance": item.get("importance") or "",
                    "status": item.get("status") or "",
                    "verification": item.get("verification") or "",
                    "sourceAnchor": item.get("source_anchor") or "",
                    "sourceStatement": review.get("source_statement") or None,
                    "clauses": clauses,
                    "notePointer": f"/items/{pos}/notes",
                })
        # A curated row is the one a reviewer wants first; a row that merely
        # lists the name among dozens is context.
        out.sort(key=lambda r: _ROLE_ORDER.index(r["role"]))
        return out

    def _declaration(
        self,
        name: str,
        index: Any,
        statements: dict[str, Any],
    ) -> dict[str, Any]:
        record = statements.get(name)
        rows = index.resolve(name) if index is not None else []
        decl = rows[0] if rows else None
        path, line, statement = "", 0, ""
        if decl is not None:
            path = (
                decl.path.relative_to(self.root).as_posix()
                if decl.path.is_relative_to(self.root)
                else str(decl.path)
            )
            line = decl.line
            statement = declaration_statement_text(decl.path, decl.line) or ""
        return {
            "name": name,
            # The elaborated record is the better answer where it exists; the
            # source scan knows these two for a declaration nobody has built.
            "kind": getattr(record, "kind", "") or (decl.kind if decl is not None else ""),
            "module": getattr(record, "module", "") or (decl.module if decl is not None else ""),
            "docstring": (getattr(record, "docstring", "") or "")[:DOCSTRING_CHARS],
            "path": path,
            "line": line,
            "statement": statement,
        }


#: Strongest claim first: a row that names this declaration as its canonical
#: realization says more about it than one that lists it among its evidence.
_ROLE_ORDER = ("canonical", "supporting", "context", "listed")


def _role_of(name: str, item: dict[str, Any], review: dict[str, Any]) -> str | None:
    """How a census row names this declaration, or ``None`` if it does not."""
    if name in _names(review.get("canonical_declarations") or []):
        return "canonical"
    if name in _names(review.get("supporting_declarations") or []):
        return "supporting"
    for entry in review.get("context_declarations") or []:
        if isinstance(entry, dict) and entry.get("name") == name:
            return "context"
    if name in _names(item.get("lean_declarations") or []):
        return "listed"
    return None


#: What a ledger file calls itself, minus the bookkeeping. A display aid for a
#: sidebar and a heading -- a census titles itself with the paper's whole
#: citation, which is unreadable in either. `-statement-map` is deliberately not
#: here: it would collapse to the same label as the census beside it.
_CENSUS_SUFFIXES = (
    "-full-source-census", "-source-census", "-census",
    "-result-semantic-review", "-semantic-review",
    "-formalization-result-inventory", "-result-inventory",
)


def short_paper_name(slug: str) -> str:
    """The name a human gave a ledger file, without the bookkeeping suffix."""
    for suffix in _CENSUS_SUFFIXES:
        if slug.endswith(suffix):
            return slug[: -len(suffix)]
    return slug


def _names(values: Iterable[Any]) -> list[str]:
    return [v for v in values if isinstance(v, str) and v]

"""What an identifier in a rendered Lean statement means.

A statement on screen is spelled the way Lean resolved it in the file it was
written in -- ``SymmetricNormingFunction``, not ``TauCeti.SymmetricNormingFunction``
-- because that file has the namespace open.  Every index this repository keeps
is keyed the other way, by fully qualified name.  So a reviewer hovering the
most interesting word in a source-facing theorem gets nothing from a literal
lookup, which is the common case rather than the corner case: in a paper-facing
statement almost every name is relative.

This module is that resolution -- verbatim, then through the reading module's
own namespaces, then by suffix over the whole index -- and the compact answer
that goes on a hover card.  An ambiguous suffix is reported as ambiguous rather
than silently picking a module, because picking one by convention is how a
reviewer ends up reading the wrong theorem's docstring.

Read-only and pure apart from reading the Lean file a resolved name lives in.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from ..lean_source import (
    PROOF_PLACEHOLDER,
    LeanSourceIndex,
    declaration_statement_text,
    strip_comments,
)

#: A hover card is read at a glance, not scrolled.
DOCSTRING_LIMIT = 600
SOURCE_LIMIT = 4000


@dataclass(frozen=True)
class Symbol:
    """One resolved identifier, and the other names the query could have meant."""

    name: str
    query: str
    kind: str
    module: str
    docstring: str
    signature: str
    path: str
    line: int
    source: str
    candidates: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "query": self.query,
            "kind": self.kind,
            "module": self.module,
            "docstring": self.docstring,
            "signature": self.signature,
            "path": self.path,
            "line": self.line,
            "source": self.source,
            "candidates": list(self.candidates),
        }


def resolve_symbol(
    *,
    name: str,
    source_index: LeanSourceIndex,
    statements: dict[str, Any],
    root: str | pathlib.Path,
    module: str | None = None,
    limit: int = 6,
) -> Symbol | None:
    """Resolve ``name`` as it was read in ``module``, or return ``None``.

    ``None`` is a real answer, not a failure: a reviewer hovers field accesses
    (``P.gap``), local binders, and notation as often as they hover declarations,
    and none of those have a global name to report.
    """
    query = name.strip()
    if not query:
        return None
    resolved, candidates = _resolve(query, source_index, statements, module, limit)
    if resolved is None:
        return None
    return _describe(resolved, query, candidates, source_index, statements, pathlib.Path(root))


# -- resolution ---------------------------------------------------------------


def _known(name: str, source_index: LeanSourceIndex, statements: dict[str, Any]) -> bool:
    return _record(statements, name) is not None or name in source_index.by_name


def _record(statements: dict[str, Any], name: str) -> Any | None:
    """The elaborated record for ``name``, or ``None``.

    A record flagged ``missing`` is a note that elaboration was attempted and
    found nothing; treating it as a hit reports an empty card as an answer.
    """
    record = statements.get(name)
    if record is None or getattr(record, "missing", False):
        return None
    return record


def _all_names(source_index: LeanSourceIndex, statements: dict[str, Any]) -> Iterable[str]:
    known = {name for name, record in statements.items() if not getattr(record, "missing", False)}
    known.update(source_index.by_name)
    return known


def _module_prefixes(module: str) -> list[str]:
    """``A.B.C`` -> ``["A.B.C", "A.B", "A"]``.

    Longest first: a name declared in the module being read shadows the same
    short name elsewhere in the package, exactly as Lean resolves it.
    """
    parts = [part for part in module.split(".") if part]
    return [".".join(parts[:n]) for n in range(len(parts), 0, -1)]


def _resolve(
    query: str,
    source_index: LeanSourceIndex,
    statements: dict[str, Any],
    module: str | None,
    limit: int,
) -> tuple[str | None, tuple[str, ...]]:
    if _known(query, source_index, statements):
        return query, ()
    for prefix in _module_prefixes(module or ""):
        candidate = f"{prefix}.{query}"
        if _known(candidate, source_index, statements):
            return candidate, ()
    suffix = "." + query
    # Shortest wins: a name with fewer components is nearer the root of the
    # namespace tree and is what an open-namespace spelling most often means.
    # The rest are handed back so the card can say the choice was not forced.
    matches = sorted(
        (name for name in _all_names(source_index, statements) if name.endswith(suffix)),
        key=lambda name: (name.count("."), name),
    )
    if matches:
        return matches[0], tuple(matches[1 : 1 + max(0, limit)])
    return None, ()


# -- the answer ---------------------------------------------------------------

#: A leading ``/-- ... -/`` block, which belongs in the card's prose and not in
#: the line offered as the declaration's signature.
_LEADING_DOCSTRING_RE = re.compile(r"\A\s*/--(?P<body>.*?)-/\s*", re.DOTALL)


def _split_docstring(text: str) -> tuple[str, str]:
    """``(docstring, statement)`` for a declaration as written."""
    match = _LEADING_DOCSTRING_RE.match(text)
    if match is None:
        return "", text.strip()
    prose = "\n".join(line.strip() for line in match.group("body").splitlines()).strip()
    return prose, text[match.end():].strip()


def _text(record: Any, field: str) -> str:
    return str(getattr(record, field, "") or "")


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    # Marked, so a clipped statement is never mistaken for a complete one.
    return text[:limit].rstrip() + " …"


def _describe(
    resolved: str,
    query: str,
    candidates: tuple[str, ...],
    source_index: LeanSourceIndex,
    statements: dict[str, Any],
    root: pathlib.Path,
) -> Symbol:
    record = _record(statements, resolved)
    rows = source_index.by_name.get(resolved) or []
    if not rows and record is not None:
        # The environment's qualified spelling is not always the key the source
        # scan filed a declaration under. Matching the short name *within the
        # module the record names* recovers the location without the hazard of a
        # bare short-name lookup, which resolves `ContinuousLinearMap.id` to
        # whichever project declaration happens to be called `id`.
        short = resolved.rsplit(".", 1)[-1]
        module = _text(record, "module")
        rows = [row for row in source_index.by_short_name.get(short, ())
                if row.module == module]
    row = rows[0] if rows else None

    path, line, written = "", 0, ""
    if row is not None:
        line = row.line
        path = row.path.as_posix()
        if row.path.is_relative_to(root):
            path = row.path.relative_to(root).as_posix()
        written = declaration_statement_text(row.path, row.line) or ""
    prose, statement = _split_docstring(written)

    return Symbol(
        name=resolved,
        query=query,
        kind=_text(record, "kind") or (row.kind if row is not None else ""),
        # The source scan was read from disk; a statement sidecar is a snapshot
        # and can name a module that has since been renamed away.
        module=(row.module if row is not None else "") or _text(record, "module"),
        # The elaborated docstring and the written one are the same prose; the
        # written one is the only source when nothing has been elaborated yet.
        docstring=_clip(_text(record, "docstring") or prose, DOCSTRING_LIMIT),
        signature=_text(record, "signature") or _text(record, "type") or statement,
        path=path,
        line=line,
        source=_clip(written, SOURCE_LIMIT),
        candidates=candidates,
    )


# -- what a statement mentions ------------------------------------------------

#: An identifier atom: a non-digit word character, then word characters and the
#: name-legal suffixes.  ``\w`` already covers the unicode this project's names
#: are full of -- ``𝕜``, ``ℝ``, ``Θ``, and the ``₀-₉`` subscripts, which are not
#: ``\d`` and so may not start one.  Dots join atoms so a qualified name stays
#: one token.
_ATOM = r"[^\W\d][\w'!?]*"
_IDENTIFIER_RE = re.compile(rf"{_ATOM}(?:\.{_ATOM})*")

#: Words Lean's own grammar puts in a statement, plus the handful of tactic
#: names that survive next to one.  Kept short on purpose: a word listed here can
#: never be offered for lookup, so a wrong entry hides a real declaration, while
#: a missing one only leaves an unresolvable token in the list.
LEAN_WORDS = frozenset(
    """
    theorem lemma def abbrev instance structure class inductive axiom example alias opaque
    namespace section end open import variable universe attribute deriving extends where
    omit private protected noncomputable partial unsafe scoped local nonrec
    by fun let rec in do if then else match with have show this from calc at
    forall exists Type Sort Prop
    intro intros apply exact refine rw rwa simp simpa constructor rfl trivial
    """.split()
)


def statement_identifiers(text: str) -> list[str]:
    """Every identifier-shaped token in ``text``, in first-appearance order.

    Comments and docstrings are dropped first: the prose around a theorem names
    the same things the statement does, so scanning it makes the panel claim the
    statement mentions vocabulary it does not use.  Single-character tokens go
    too -- they are the ambient binders (``A``, ``S``, ``𝕜``), never declarations.
    """
    # The elided-proof marker is our own text, not the author's.
    body = strip_comments(text).replace(PROOF_PLACEHOLDER, " ")
    out: dict[str, None] = {}
    for match in _IDENTIFIER_RE.finditer(body):
        token = match.group()
        if len(token) > 1 and token not in LEAN_WORDS:
            out.setdefault(token, None)
    return list(out)

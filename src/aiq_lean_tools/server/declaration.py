"""Everything known about one declaration, gathered for an audit.

Selecting a theorem in a census used to be the end of the trail: you got its
name. To judge whether it says what the paper says you then had to find the
file, read the statement, find whether it had been elaborated, and find what its
proof rests on -- in four different places.

This assembles that in one answer:

* the **source as written**, statement and proof body, from the Lean file;
* the **elaborated signature**, docstring and type hashes, read from whatever
  ``leanq`` statement sidecars already exist -- no Lean is invoked, so a
  declaration nobody has elaborated yet says so instead of blocking;
* the **statement closure**: which project constants the statement itself
  depends on, which is what decides whether a theorem is stated in the paper's
  vocabulary or in a private one;
* the **proof dependencies and axioms**, when a saved graph index is present;
* every **ledger row that names it**, so the audit and the bookkeeping meet.

Everything here is read-only and derived. Nothing is cached beyond a file's
stat, so an edit to a Lean file shows up on the next request.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

#: Where statement sidecars are written by `leanq`/`aiq-lean alignment`.
SIDECAR_DIR = ".leanq"
#: Default saved graph index, as `viz-proof-structure.sh` writes it.
GRAPH_PATH = "build/leanq/project-semantic-graph.json"

#: Tokens that open a new top-level declaration, used to find where one ends.
_DECL_STARTS = (
    "theorem ", "lemma ", "def ", "abbrev ", "instance ", "structure ", "class ",
    "inductive ", "example ", "noncomputable def ", "private theorem ", "private def ",
    "protected theorem ", "protected def ", "@[", "namespace ", "end ", "section ",
    "variable ", "open ", "import ", "/-- ",
)


class DeclarationService:
    """Read-only assembly of what the repository knows about a declaration."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._index: Any = None
        self._statements: dict[str, Any] | None = None
        self._statement_stamp: tuple | None = None
        self._graph: Any = None
        self._graph_stamp: tuple | None = None
        self._newest: tuple[float, int, str] | None = None
        # Proof closures only. Walking the dependency graph for one target costs
        # about three seconds; everything else in a detail() is fast and is left
        # uncached so a source edit shows immediately.
        self._proofs: dict[str, Any] = {}
        self._proofs_stamp: tuple | None = None

    # -- lazily built inputs ----------------------------------------------

    def source_index(self):
        if self._index is None:
            from ..lean_source import scan_lean_project

            self._index = scan_lean_project(self.root)
        return self._index

    def refresh_source_index(self) -> None:
        self._index = None

    def _sidecar_files(self) -> list[Path]:
        d = self.root / SIDECAR_DIR
        return sorted(d.glob("*.statements-*.jsonl")) if d.is_dir() else []

    def statements(self) -> dict[str, Any]:
        """Every elaborated record any sidecar holds, newest file winning."""
        files = self._sidecar_files()
        stamp = tuple((f.name, f.stat().st_mtime_ns) for f in files)
        if self._statements is not None and stamp == self._statement_stamp:
            return self._statements
        from leanq.statement import load_statement_sidecar

        merged: dict[str, Any] = {}
        for path in sorted(files, key=lambda p: p.stat().st_mtime):
            try:
                for record in load_statement_sidecar(path):
                    if not record.missing:
                        merged[record.name] = record
            except Exception:
                continue
        self._statements, self._statement_stamp = merged, stamp
        return merged

    def graph(self):
        path = self.root / GRAPH_PATH
        if not path.is_file():
            return None
        stamp = (path.stat().st_mtime_ns, path.stat().st_size)
        if self._graph is not None and stamp == self._graph_stamp:
            return self._graph
        # A rebuilt graph is exactly the event that can clear staleness.
        self._newest = None
        try:
            # `leanq graph-index` writes nodes/edges; the proof panel wants it
            # indexed by declaration name. load_graph_table does that -- reading
            # the raw JSON instead makes every proof panel silently empty.
            from ..alignment import load_graph_table

            self._graph = load_graph_table(path)
            self._graph_stamp = stamp
        except Exception:
            self._graph = None
        return self._graph

    def graph_status(self) -> dict[str, Any]:
        """Whether the saved dependency graph still describes the sources.

        A graph index is a snapshot. After a rename it keeps answering, with the
        old names, and a dependency view built on it looks healthy while being
        wrong -- the current index still holds 106 declarations whose names were
        changed earlier today. So freshness is reported, never assumed.
        """
        path = self.root / GRAPH_PATH
        if not path.is_file():
            return {"present": False, "path": GRAPH_PATH}
        graph_ns = path.stat().st_mtime_ns
        newest, newest_file = self._newest_source()
        table = (self.graph() or {}).get("table") or {}
        return {
            "present": True,
            "path": GRAPH_PATH,
            "declarations": len(table),
            "stale": bool(newest and newest > graph_ns),
            "newerSource": newest_file if newest > graph_ns else "",
            "rebuild": f"leanq graph-index --out {GRAPH_PATH}",
        }

    def _newest_source(self, ttl: float = 180.0) -> tuple[int, str]:
        """Timestamp of the most recently edited Lean file, cached.

        Walking the source tree costs two and a half seconds, and this ran on
        every declaration request -- it was the entire cost of opening an audit
        page. A short TTL was not enough either: it expired between clicks, so
        every few requests paid the walk again.

        Staleness is a slow-moving property, so the window is minutes, and the
        cache is dropped immediately when the graph file itself changes -- which
        is the event that can make a stale index current.
        """
        now = time.monotonic()
        if self._newest is not None and now - self._newest[0] < ttl:
            return self._newest[1], self._newest[2]
        newest, newest_file = 0, ""
        for lean in self.root.glob("*/**/*.lean"):
            if any(part in {".lake", "build", ".leanq"} for part in lean.parts):
                continue
            try:
                ns = lean.stat().st_mtime_ns
            except OSError:
                continue
            if ns > newest:
                newest, newest_file = ns, lean.name
        self._newest = (now, newest, newest_file)
        return newest, newest_file

    def _proof_for(self, graph: Any, resolved: str) -> Any:
        if self._proofs_stamp != self._graph_stamp:
            self._proofs.clear()
            self._proofs_stamp = self._graph_stamp
        if resolved in self._proofs:
            return self._proofs[resolved]
        try:
            from ..alignment import _proof_payload

            value = _proof_payload(graph, resolved)
        except Exception:
            value = None
        if len(self._proofs) > 256:
            self._proofs.clear()
        self._proofs[resolved] = value
        return value

    def resolve_graph_name(self, name: str) -> str | None:
        """The graph's spelling of ``name``.

        Censuses and statement sidecars use the short name; the graph index
        stores the fully qualified one, so an exact lookup silently misses every
        declaration and every proof panel comes back empty.
        """
        table = (self.graph() or {}).get("table") or {}
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

    # -- the answer --------------------------------------------------------

    def detail(self, name: str, *, with_proof: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {"name": name}

        # Source as written.
        try:
            from ..lean_source import declaration_source_texts

            texts = declaration_source_texts(self.source_index(), name)
        except Exception:
            texts = []
        if texts:
            text = texts[0]
            decl = text.declaration
            try:
                rel = decl.path.relative_to(self.root).as_posix()
            except ValueError:
                rel = str(decl.path)
            out["source"] = {
                "path": rel,
                "line": decl.line,
                "module": getattr(decl, "module", ""),
                "header": text.render(),
                "full": _full_declaration(decl.path, decl.line) if with_proof else None,
            }
            out["alternates"] = [
                {
                    "path": t.declaration.path.relative_to(self.root).as_posix()
                    if t.declaration.path.is_relative_to(self.root) else str(t.declaration.path),
                    "line": t.declaration.line,
                }
                for t in texts[1:]
            ]

        # Elaborated statement.
        record = self.statements().get(name)
        if record is not None:
            out["elaborated"] = {
                "signature": record.signature or record.type,
                "type": record.type,
                "kind": record.kind,
                "module": record.module,
                "library": record.library,
                "isProp": record.is_prop,
                "docstring": record.docstring,
                "hashes": {"expr": record.type_expr_hash, "text": getattr(record, "type_text_sha256", "")},
                "typeDeps": list(record.type_deps),
            }
            try:
                from leanq.statement import closure_summary

                out["closure"] = closure_summary(self.statements(), name)
            except Exception:
                out["closure"] = None
        else:
            out["elaborated"] = None
            out["elaboratedHint"] = (
                "No statement sidecar holds this declaration. Build one with "
                "`aiq-lean alignment html <census> --statements`, which elaborates the "
                "declarations a census registers."
            )

        # Proof dependencies.
        graph = self.graph()
        status = self.graph_status()
        out["graphStatus"] = status
        if graph is not None:
            resolved = self.resolve_graph_name(name)
            out["graphName"] = resolved
            if resolved is None:
                out["proof"] = None
                out["proofHint"] = (
                    "This declaration is not in the saved dependency graph."
                    + (" The graph is older than the sources, so it may predate this name; rebuild with "
                       f"`{status['rebuild']}`." if status.get("stale") else "")
                )
            else:
                out["proof"] = self._proof_for(graph, resolved)
        else:
            out["proof"] = None
            out["proofHint"] = (
                f"No saved proof graph at {GRAPH_PATH}. Build one with "
                f"`{status.get('rebuild')}` (about ten minutes)."
            )
        return out


def _full_declaration(path: Path, line: int, limit: int = 500) -> str | None:
    """The whole declaration, statement and proof, as written.

    ``SourceDeclarationText.render`` deliberately stops before the proof body --
    it answers "what does this say". Auditing a proof needs the body too.

    The declaration's own line is never treated as a terminator: scanning
    forward from the docstring instead of from the declaration made every
    theorem end at its own first line, returning the docstring alone.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    decl = max(0, line - 1)
    if decl >= len(lines):
        return None

    # Walk back over attributes, `omit ... in`, and a docstring block.
    start = decl
    while start > 0:
        prev = lines[start - 1].rstrip()
        stripped = prev.lstrip()
        if stripped.startswith("@[") or stripped.startswith("omit ") or stripped.startswith("private ") or stripped.startswith("protected "):
            start -= 1
        elif prev.endswith("-/"):
            start -= 1
            while start > 0 and not lines[start].lstrip().startswith(("/--", "/-")):
                start -= 1
        elif stripped.startswith("/--") and stripped.endswith("-/"):
            start -= 1
        else:
            break

    out = lines[start : decl + 1]
    for text in lines[decl + 1 : decl + limit]:
        if text and not text[0].isspace() and any(text.startswith(t) for t in _DECL_STARTS):
            break
        out.append(text)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) or None

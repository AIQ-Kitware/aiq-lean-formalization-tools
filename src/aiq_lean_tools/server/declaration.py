"""Everything known about one declaration, gathered for an audit.

Selecting a theorem in a census used to be the end of the trail: you got its
name. To judge whether it says what the paper says you then had to find the
file, read the statement, find whether it had been elaborated, and find what its
proof rests on -- in four different places.

This assembles that in one answer:

* the **source as written** -- the docstring and the statement, without the
  proof body -- from the Lean file;
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

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ..lean_source import declaration_statement_text

#: Where statement sidecars are written by `leanq`/`aiq-lean alignment`.
SIDECAR_DIR = ".leanq"
#: Default saved graph index, as `viz-proof-structure.sh` writes it.
GRAPH_PATH = "build/leanq/project-semantic-graph.json"

class DeclarationService:
    """Read-only assembly of what the repository knows about a declaration."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._index: Any = None
        self._index_stamp: tuple | None = None
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
        """The Lean source scan, rebuilt when a source file has changed.

        The scan costs seconds, so it is cached -- but it used to be cached
        *forever*, and nothing refreshed it.  A long-running server therefore
        kept serving whatever a theorem said when the process started.  The
        newest-source stamp already computed for graph staleness is the cheap
        revision to key it on.
        """
        stamp = self.source_revision()
        if self._index is not None and stamp == self._index_stamp:
            return self._index
        from ..lean_source import scan_lean_project

        self._index = scan_lean_project(self.root)
        self._index_stamp = stamp
        return self._index

    def refresh_source_index(self) -> None:
        self._index = None
        self._index_stamp = None
        self._newest = None

    def source_revision(self) -> tuple[int, str]:
        """What the Lean sources look like now, for a cache key."""
        return self._newest_source()

    def rescan_sources(self) -> tuple[int, str]:
        """Recompute the source revision now, ignoring the staleness TTL.

        The server calls this from its watcher on a slow cadence, in a thread,
        so a request never pays the tree walk and a Lean edit is still noticed
        within seconds instead of within the TTL.
        """
        self._newest = None
        return self._newest_source()

    def _sidecar_files(self) -> list[Path]:
        d = self.root / SIDECAR_DIR
        return sorted(d.glob("*.statements-*.jsonl")) if d.is_dir() else []

    def statement_revision(self) -> str:
        """A short digest of every statement sidecar on disk.

        A count of records is *not* a revision: regenerating a sidecar after a
        theorem's type changes leaves the count identical, and anything keyed on
        the count went on serving the previous elaborated statement.  This is
        keyed on the same (name, mtime) stamp the sidecar cache itself uses, and
        digested so it stays cheap to put in a page key or an ETag.
        """
        files = self._sidecar_files()
        stamp = repr(self._sidecar_stamp(files)).encode()
        return hashlib.sha256(stamp).hexdigest()[:16]

    def graph_revision(self) -> tuple:
        """What the saved dependency graph looks like now, for a cache key.

        Its path is constant, so keying on the path never invalidates anything;
        mtime and size do.
        """
        path = self.root / GRAPH_PATH
        try:
            st = path.stat()
        except OSError:
            return ()
        return (st.st_mtime_ns, st.st_size)

    @staticmethod
    def _sidecar_stamp(files: list[Path]) -> tuple:
        out = []
        for f in files:
            try:
                st = f.stat()
            except OSError:
                out.append((f.name, 0))
                continue
            out.append((f.name, st.st_mtime_ns))
        return tuple(out)

    def statements(self) -> dict[str, Any]:
        """Every elaborated record any sidecar holds, newest file winning."""
        files = self._sidecar_files()
        stamp = self._sidecar_stamp(files)
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
        stamp = self.graph_revision()
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
        """The graph's spelling of ``name``; see :func:`alignment.resolve_graph_name`."""
        from ..alignment import resolve_graph_name

        return resolve_graph_name((self.graph() or {}).get("table") or {}, name)

    # -- the answer --------------------------------------------------------

    def detail(self, name: str) -> dict[str, Any]:
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
                "statement": declaration_statement_text(decl.path, decl.line),
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

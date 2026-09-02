"""The source-to-Lean comparison view, served from the workspace browser.

``aiq-lean alignment html`` writes this page as a file.  Served, it should be
the same page -- so this builds the same payload through the same functions, and
differs only in what it reuses: the server already holds a Lean source scan,
every statement sidecar on disk, and the saved dependency graph, and rebuilding
those per request costs half a minute each.

The source library is built once and re-read per request only when a source
document has changed on disk, because that is the event a source pin exists to
notice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..alignment import alignment_payload, build_alignment_packet
from ..source_model import SourceLibrary, load_private_config


class AlignmentService:
    """Alignment payloads for the censuses in a repository, cached on inputs."""

    def __init__(self, root: Path, declarations, *, private: str | None = None,
                 include_private: bool = False) -> None:
        self.root = Path(root)
        self.declarations = declarations
        self.include_private = include_private
        self._private = private
        self._proofs: dict[str, Any] = {}
        self._proofs_stamp: Any = None
        self._library: SourceLibrary | None = None
        self._library_stamp: tuple | None = None
        self._cache: dict[tuple, tuple[tuple, dict[str, Any], str]] = {}

    # -- inputs ------------------------------------------------------------

    def library(self) -> SourceLibrary:
        if self._library is None:
            self._library = SourceLibrary.discover(
                self.root, private=load_private_config(self._private)
            )
            self._library_stamp = self._documents_stamp(self._library)
            return self._library
        stamp = self._documents_stamp(self._library)
        if stamp != self._library_stamp:
            self._library = None
            return self.library()
        return self._library

    def documents_stamp(self) -> tuple:
        """What the source documents look like right now, for a page cache key."""
        return self._documents_stamp(self.library())

    @staticmethod
    def _documents_stamp(library: SourceLibrary) -> tuple:
        out = []
        for doc in library.documents.values():
            try:
                st = doc.path.stat()
            except OSError:
                out.append((doc.id, 0, 0))
                continue
            out.append((doc.id, st.st_mtime_ns, st.st_size))
        return tuple(out)

    # -- the payload --------------------------------------------------------

    def payload(self, census_path: Path, *, importance: str = "headline",
                rows: tuple[str, ...] = ()) -> tuple[dict[str, Any], str]:
        library = self.library()
        try:
            census_stamp = census_path.stat().st_mtime_ns
        except OSError as ex:
            raise KeyError(str(census_path)) from ex
        graph = self.declarations.graph()
        key = (str(census_path), importance, rows)
        stamp = (
            census_stamp,
            self._documents_stamp(library),
            len(self.declarations.statements()),
            (graph or {}).get("nodeCount"),
        )
        hit = self._cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1], hit[2]
        if self._proofs_stamp != (graph or {}).get("path"):
            self._proofs.clear()
            self._proofs_stamp = (graph or {}).get("path")
        packet = build_alignment_packet(
            [census_path],
            root=self.root,
            importance=importance,
            rows=rows,
            sources=library,
            include_private=self.include_private,
            source_index=self.declarations.source_index(),
            statement_map=self.declarations.statements(),
            proof_cache=self._proofs,
        )
        title = f"{packet.entries[0].census.title} — source-to-Lean alignment" if packet.entries \
            else "Source-to-Lean alignment"
        data = alignment_payload(packet, graph=graph, title=title)
        if len(self._cache) > 8:
            self._cache.clear()
        self._cache[key] = (stamp, data, title)
        return data, title

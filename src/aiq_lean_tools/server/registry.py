"""What documents exist under a root, and which viewer answers for each.

A repository holds many ledgers whose names follow no single rule --
``dev/davis-kahan-1970-full-source-census.json``,
``dev/helm-2025-result-semantic-review.json``,
``prose/distilled_literature/source_manifest.json``. Guessing the kind from the
filename gets this wrong the first time someone names a file differently, so the
kind is read off the schema instead: a census has ``items``, a semantic review
has ``rows`` and ``review_kind``, a result inventory has ``results``, a
foundation map has ``nodes``, a literature manifest has ``works``.

The marker is checked before the loader runs. Offering each file to every loader
in turn looks more general but is wrong here -- ``load_census`` accepts most of
these schemas, so whichever loader was tried first would claim all 97 documents.
A file whose marker matches but whose loader rejects it is simply not in the
catalog, and neither is one no marker matches.

Payloads are cached on ``(path, mtime, size)``. Editing a file on disk therefore
invalidates its payload with no cache-busting protocol.
"""

from __future__ import annotations

import json as _json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Where ledgers live. Kept broad; the loaders decide what is real.
SCAN_GLOBS = ("dev/**/*.json", "prose/**/*.json", "docs/**/*.json")

# Directories that never hold a reviewable ledger.
SKIP_PARTS = {".git", ".lake", "build", ".leanq", "node_modules", "__pycache__", "submodules"}


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return value[:100] or "document"


@dataclass(frozen=True)
class ViewSpec:
    """One viewer: what its documents look like, and how to load one."""

    name: str
    label: str
    asset: str
    load: Callable[[Path, Path], Any]
    #: The top-level key that identifies this schema. Checked before the loader
    #: runs, because several loaders are permissive enough to accept a
    #: neighbouring schema and the first one offered would otherwise win.
    marker: str
    #: A second key that must also be present, where one key is not decisive.
    marker2: str | None = None

    def claims(self, data: Any) -> bool:
        if not isinstance(data, dict) or self.marker not in data:
            return False
        return self.marker2 is None or self.marker2 in data


@dataclass
class Document:
    view: str
    slug: str
    path: Path
    title: str

    def as_json(self, root: Path) -> dict[str, Any]:
        try:
            rel = self.path.relative_to(root).as_posix()
        except ValueError:
            rel = str(self.path)
        return {"view": self.view, "slug": self.slug, "path": rel, "title": self.title}


def _view_specs() -> list[ViewSpec]:
    from .. import census as _census
    from .. import coverage as _coverage
    from .. import foundations as _foundations
    from .. import literature as _literature
    from .. import semantic_review as _review

    def load_foundations(path: Path, root: Path):
        fmap = _foundations.FoundationMap.load(path)
        return _foundations.check_foundation_map(fmap, root=root, lean_probe=False)

    return [
        ViewSpec("census", "Census", "census_viewer.html",
                 lambda p, r: _census.load_census(p, root=r), marker="items"),
        ViewSpec("review", "Semantic review", "review_viewer.html",
                 lambda p, r: _review.load_semantic_review(p, root=r), marker="rows", marker2="review_kind"),
        ViewSpec("coverage", "Coverage", "coverage_viewer.html",
                 lambda p, r: _coverage.load_coverage_bundle(p, root=r), marker="results"),
        ViewSpec("foundations", "Foundations", "foundation_viewer.html",
                 load_foundations, marker="nodes"),
        ViewSpec("literature", "Literature", "literature_viewer.html",
                 lambda p, r: _literature.load_literature(p, root=r), marker="works"),
    ]


@dataclass
class Catalog:
    root: Path
    specs: list[ViewSpec] = field(default_factory=_view_specs)
    _docs: dict[tuple[str, str], Document] = field(default_factory=dict)
    _payloads: dict[tuple[str, str], tuple[tuple[int, int], Any, str]] = field(default_factory=dict)
    _scanned: bool = False

    # -- discovery ---------------------------------------------------------

    def candidate_files(self) -> list[Path]:
        seen: set[Path] = set()
        for pattern in SCAN_GLOBS:
            for p in sorted(self.root.glob(pattern)):
                if not p.is_file():
                    continue
                if SKIP_PARTS & set(p.relative_to(self.root).parts):
                    continue
                seen.add(p)
        return sorted(seen)

    def scan(self, *, force: bool = False) -> list[Document]:
        if self._scanned and not force:
            return list(self._docs.values())
        self._docs.clear()
        for path in self.candidate_files():
            try:
                data = _json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for spec in self.specs:
                if not spec.claims(data):
                    continue
                try:
                    doc = spec.load(path, self.root)
                except Exception:
                    continue  # right shape, but the loader rejected it
                title = _title_of(doc, path)
                d = Document(spec.name, _slug(path.stem), path, title)
                self._docs[(spec.name, d.slug)] = d
                break
        self._scanned = True
        return list(self._docs.values())

    def documents(self) -> list[Document]:
        return self.scan()

    def get(self, view: str, slug: str) -> Document | None:
        self.scan()
        return self._docs.get((view, slug))

    def spec(self, view: str) -> ViewSpec | None:
        return next((s for s in self.specs if s.name == view), None)

    # -- payloads ----------------------------------------------------------

    def payload(self, view: str, slug: str) -> tuple[Any, str]:
        """``(payload, title)`` for a document, cached on the file's stat."""
        doc = self.get(view, slug)
        if doc is None:
            raise KeyError(f"{view}/{slug}")
        spec = self.spec(view)
        assert spec is not None
        st = doc.path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        hit = self._payloads.get((view, slug))
        if hit and hit[0] == stamp:
            return hit[1], hit[2]
        loaded = spec.load(doc.path, self.root)
        payload = loaded.payload()
        title = _title_of(loaded, doc.path)
        self._payloads[(view, slug)] = (stamp, payload, title)
        doc.title = title
        return payload, title

    def watched_files(self) -> list[Path]:
        return [d.path for d in self.documents()]


def _title_of(doc: Any, path: Path) -> str:
    for attr in ("title",):
        value = getattr(doc, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return path.stem

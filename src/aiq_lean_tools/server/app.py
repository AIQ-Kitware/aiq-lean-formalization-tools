"""The FastAPI application behind ``aiq-lean serve``.

One process holds every ledger in a repository and every viewer that reads one,
so the views can finally refer to each other: a declaration named in an
alignment row is a link to the census row that registers it, and a search runs
across all of them at once instead of one page at a time.

The static pages do not go away, and this is deliberately not a second
implementation of them. ``/view/{view}/{slug}`` renders exactly what
``aiq-lean <view> html -o`` writes, through the same ``viewer_html`` call, so a
served page and a written file cannot disagree.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from ..viewer import read_asset, viewer_html
from .alignment import AlignmentService
from .edits import EditRefused, apply_edit, read_journal
from .registry import Catalog
from .warmup import Readiness
from .declaration import DeclarationService
from .xref import Xref

# Imported at module scope, not inside create_app: this module uses
# ``from __future__ import annotations``, so FastAPI resolves a route's
# annotations against these module globals. Bound inside a function they are
# invisible to that lookup, and a `request: Request` parameter silently becomes
# a required query parameter instead of the request body.
try:
    from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
    from fastapi.middleware.gzip import GZipMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse

    HAVE_FASTAPI = True
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    HAVE_FASTAPI = False


def _require_fastapi() -> None:
    missing = None
    if not HAVE_FASTAPI:
        missing = "fastapi"
    else:
        try:
            import uvicorn  # noqa: F401
        except ModuleNotFoundError:
            missing = "uvicorn"
    if missing:
        raise SystemExit(
            "aiq-lean serve needs the optional server dependencies:\n\n"
            "    python3 -m pip install -e 'submodules/aiq-lean-formalization-tools[serve]'\n\n"
            f"(missing: {missing})"
        )


def create_app(root: Path, *, title: str = "Formalization workspace",
               private_sources: str | None = None, include_private: bool = False):
    _require_fastapi()

    root = Path(root).expanduser().resolve()
    catalog = Catalog(root)
    app = FastAPI(title=title)
    app.state.root = root
    app.state.catalog = catalog
    app.state.sockets: set[Any] = set()
    xref = Xref()
    app.state.xref = xref
    decls = DeclarationService(root)
    app.state.declarations = decls
    alignment = AlignmentService(root, decls, private=private_sources,
                                 include_private=include_private)
    app.state.alignment = alignment

    # Pages are large -- the workspace view is 1.6 MB of JSON in a script tag --
    # and highly compressible.
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    ready = Readiness()
    app.state.readiness = ready
    ready.declare("catalog", "Finding ledgers", "the ledger list")
    ready.declare("payloads", "Reading ledgers", "ledger views and text search")
    ready.declare("xref", "Cross-referencing declarations", "declaration links and shared declarations")
    ready.declare("sources", "Scanning Lean sources", "source snippets on the audit page")
    ready.declare("statements", "Loading elaborated statements", "elaborated signatures and closures")
    ready.declare("graph", "Loading the dependency graph", "proof dependencies and axioms")
    ready.declare("workspace", "Building the workspace summary", "the workspace view")
    ready.declare("literature", "Reading literature source documents",
                  "rendered source passages on the alignment view")

    # The workspace view rebuilds from every ledger in the repository, which
    # takes half a minute; it was doing that on each request. Cached against the
    # ledger stats, so an edit still invalidates it.
    workspace_cache: dict[bool, tuple[Any, dict, str]] = {}

    def ledger_fingerprint() -> Any:
        out = []
        for doc in catalog.documents():
            try:
                st = doc.path.stat()
            except OSError:
                continue
            out.append((doc.path.name, st.st_mtime_ns, st.st_size))
        return tuple(out)

    def workspace_payload(source_audit: bool) -> tuple[dict, str]:
        from ..workspace import FormalizationWorkspace

        stamp = ledger_fingerprint()
        hit = workspace_cache.get(source_audit)
        if hit and hit[0] == stamp:
            return hit[1], hit[2]
        ws = FormalizationWorkspace.discover(root)
        # Reuse the scan the declaration service already holds: building this
        # from scratch rescanned every Lean file for an identical index.
        data = ws.payload(include_source_audit=source_audit, source_index=decls.source_index())
        title = ws.payload_title(data)
        workspace_cache[source_audit] = (stamp, data, title)
        return data, title

    # Rendered viewer pages, keyed by what they are made of.
    page_cache: dict[tuple, str] = {}

    def refresh_xref() -> Xref:
        """Keep the index in step with the files, cheaply.

        Documents are re-indexed only when their stat changes, so this is a
        no-op on every request but the first after an edit.
        """
        for doc in catalog.documents():
            try:
                st = doc.path.stat()
                payload, title = catalog.payload(doc.view, doc.slug)
            except Exception:
                continue
            xref.add_document(doc.view, doc.slug, title, payload, (st.st_mtime_ns, st.st_size))
        return xref

    # -- catalog and payloads ---------------------------------------------

    @app.get("/api/catalog")
    def api_catalog(refresh: bool = False) -> JSONResponse:
        if refresh:
            catalog.scan(force=True)
        docs = [d.as_json(root) for d in catalog.documents()]
        views = [{"name": s.name, "label": s.label} for s in catalog.specs]
        views.append({"name": "workspace", "label": "Workspace"})
        views.append({"name": "alignment", "label": "Alignment"})
        # Every census can be read as an alignment surface, so the sidebar can
        # offer one beside each. This used to advertise an "Alignment" view with
        # no documents behind it and no route to serve one.
        docs = docs + [
            {"view": "alignment", "slug": d["slug"], "path": d["path"],
             "title": d["title"]}
            for d in docs if d["view"] == "census"
        ]
        return JSONResponse(
            {"root": str(root), "title": title, "views": views, "documents": docs}
        )

    @app.get("/api/payload/alignment/{slug}")
    def api_alignment(slug: str, importance: str = "headline") -> JSONResponse:
        doc = catalog.get("census", slug)
        if doc is None:
            raise HTTPException(404, f"no census named {slug}")
        return JSONResponse(alignment.payload(doc.path, importance=importance)[0])

    @app.get("/api/payload/{view}/{slug}")
    def api_payload(view: str, slug: str) -> JSONResponse:
        try:
            payload, _ = catalog.payload(view, slug)
        except KeyError:
            raise HTTPException(404, f"no {view} document named {slug}")
        return JSONResponse(payload)

    @app.get("/api/payload/workspace")
    def api_workspace(source_audit: bool = False) -> JSONResponse:
        return JSONResponse(workspace_payload(source_audit)[0])

    # -- rendered viewers, identical to the static files ------------------

    def _cached_page(key: tuple, build, request: Request) -> HTMLResponse:
        """Serve a rendered page, letting the browser skip the bytes it has.

        The pages are large -- a census is nearly a megabyte -- so revisiting a
        view should cost a conditional request, not a re-download and re-parse.
        The tag is derived from what the page is made of, so it changes exactly
        when the page does.
        """
        page = page_cache.get(key)
        if page is None:
            page = build()
            if len(page_cache) > 24:
                page_cache.clear()
            page_cache[key] = page
        etag = '"' + hashlib.sha256(repr(key).encode()).hexdigest()[:24] + '"'
        if request.headers.get("if-none-match") == etag:
            from fastapi.responses import Response

            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
        return HTMLResponse(page, headers={"ETag": etag, "Cache-Control": "no-cache"})

    @app.get("/view/alignment/{slug}", response_class=HTMLResponse)
    def alignment_page(request: Request, slug: str, importance: str = "headline",
                       row: list[str] | None = None, theme: str | None = None) -> HTMLResponse:
        doc = catalog.get("census", slug)
        if doc is None:
            raise HTTPException(404, f"no census named {slug}")
        rows = tuple(row or ())
        data, doc_title = alignment.payload(doc.path, importance=importance, rows=rows)
        # The source documents are part of what this page is made of, so they
        # belong in the tag. Without them, editing the reconstruction left the
        # cached page showing the passage the review was accepted against --
        # precisely the drift a source pin exists to notice.
        key = ("alignment", slug, importance, rows, doc.path.stat().st_mtime_ns,
               alignment.documents_stamp(), len(decls.statements()), theme)
        return _cached_page(
            key,
            lambda: _with_bridge(
                viewer_html("alignment_viewer.html", doc_title, data, math=True), theme),
            request,
        )

    @app.get("/view/{view}/{slug}", response_class=HTMLResponse)
    def view_page(request: Request, view: str, slug: str, theme: str | None = None) -> HTMLResponse:
        spec = catalog.spec(view)
        if spec is None:
            raise HTTPException(404, f"unknown view {view}")
        try:
            payload, doc_title = catalog.payload(view, slug)
        except KeyError:
            raise HTTPException(404, f"no {view} document named {slug}")
        doc = catalog.get(view, slug)
        st = doc.path.stat() if doc else None
        key = (view, slug, st.st_mtime_ns if st else 0, theme)
        return _cached_page(key, lambda: _with_bridge(viewer_html(spec.asset, doc_title, payload), theme), request)

    @app.get("/view/workspace", response_class=HTMLResponse)
    def workspace_page(request: Request, source_audit: bool = False, theme: str | None = None) -> HTMLResponse:
        data, title = workspace_payload(source_audit)
        key = ("workspace", source_audit, ledger_fingerprint(), theme)
        return _cached_page(key, lambda: _with_bridge(viewer_html("workspace_viewer.html", title, data), theme), request)

    # -- search across every ledger at once -------------------------------

    @app.get("/api/search")
    def api_search(q: str, limit: int = 60) -> JSONResponse:
        needle = q.strip().lower()
        if len(needle) < 2:
            return JSONResponse({"query": q, "hits": []})
        hits: list[dict[str, Any]] = []
        for doc in catalog.documents():
            try:
                payload, _ = catalog.payload(doc.view, doc.slug)
            except Exception:
                continue
            for path, text in _walk_strings(payload):
                if needle in text.lower():
                    hits.append(
                        {
                            "view": doc.view,
                            "slug": doc.slug,
                            "document": doc.title,
                            "field": path,
                            "excerpt": _excerpt(text, needle),
                        }
                    )
                    if len(hits) >= limit:
                        return JSONResponse({"query": q, "hits": hits, "truncated": True})
        return JSONResponse({"query": q, "hits": hits, "truncated": False})

    # -- cross references --------------------------------------------------

    @app.get("/api/xref")
    def api_xref(q: str | None = None, shared: bool = False, limit: int = 40) -> JSONResponse:
        idx = refresh_xref()
        if q:
            return JSONResponse({"query": q, "names": idx.search(q, limit=limit)})
        if shared:
            return JSONResponse({"shared": idx.shared()[:limit], "stats": idx.stats()})
        return JSONResponse(idx.stats())

    @app.get("/api/xref/{name:path}")
    def api_xref_name(name: str) -> JSONResponse:
        idx = refresh_xref()
        return JSONResponse({"name": name, "occurrences": idx.occurrences(name)})

    @app.get("/api/rows/{view}/{slug}")
    def api_rows(view: str, slug: str) -> JSONResponse:
        """The navigable rows of one document, for a jump-to list."""
        if view == "alignment":
            doc = catalog.get("census", slug)
            if doc is None:
                raise HTTPException(404, f"no census named {slug}")
            return JSONResponse({"rows": _rows_of(alignment.payload(doc.path)[0])})
        try:
            payload, _ = catalog.payload(view, slug)
        except KeyError:
            raise HTTPException(404, f"no {view} document named {slug}")
        return JSONResponse({"rows": _rows_of(payload)})

    @app.get("/api/declaration/{name:path}")
    def api_declaration(name: str, proof: bool = True) -> JSONResponse:
        """Source, elaboration, closure, proof dependencies and every ledger row."""
        detail = decls.detail(name, with_proof=proof)
        detail["occurrences"] = refresh_xref().occurrences(name)
        if not detail.get("source") and not detail.get("elaborated") and not detail["occurrences"]:
            raise HTTPException(404, f"nothing known about {name}")
        return JSONResponse(detail)

    @app.get("/api/graph/status")
    def api_graph_status() -> JSONResponse:
        return JSONResponse(decls.graph_status())

    @app.get("/api/graph/{name:path}")
    def api_graph(name: str, depth: int = 1) -> JSONResponse:
        """Immediate proof dependencies, for the dependency view."""
        detail = decls.detail(name, with_proof=True)
        return JSONResponse(
            {
                "name": name,
                "graphName": detail.get("graphName"),
                "status": detail.get("graphStatus"),
                "proof": detail.get("proof"),
                "hint": detail.get("proofHint"),
                "closure": detail.get("closure"),
            }
        )

    # -- annotation --------------------------------------------------------

    @app.post("/api/annotate")
    async def api_annotate(request: Request) -> JSONResponse:
        body = await request.json()
        view, slug = body.get("view"), body.get("slug")
        doc = catalog.get(str(view), str(slug))
        if doc is None:
            raise HTTPException(404, f"no {view} document named {slug}")
        try:
            result = apply_edit(
                doc.path,
                str(body.get("pointer", "")),
                body.get("value"),
                author=str(body.get("author") or "browser"),
                note=str(body.get("note") or ""),
                root=root,
            )
        except EditRefused as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=403)
        except (KeyError, IndexError, ValueError) as ex:
            return JSONResponse({"ok": False, "error": str(ex)}, status_code=400)
        await _broadcast(app, {"kind": "edited", "view": doc.view, "slug": doc.slug})
        return JSONResponse(result.as_json(root))

    @app.get("/api/journal")
    def api_journal(limit: int = 200) -> JSONResponse:
        return JSONResponse({"entries": read_journal(root, limit=limit)})

    @app.get("/api/ready")
    def api_ready() -> JSONResponse:
        return JSONResponse(ready.as_json())

    @app.get("/api/writable")
    def api_writable() -> JSONResponse:
        from .edits import WRITABLE

        return JSONResponse({"patterns": list(WRITABLE)})

    # -- shell and live reload --------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def shell() -> HTMLResponse:
        return HTMLResponse(read_asset("shell.html").replace("__TITLE__", title))

    @app.websocket("/live")
    async def live(ws: WebSocket) -> None:
        await ws.accept()
        app.state.sockets.add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            app.state.sockets.discard(ws)

    def _warm() -> None:
        """Pay the expensive costs once, at startup, in dependency order.

        Deliberately sequential. Running the three independent chains in threads
        was measured and made every stage roughly three times slower for exactly
        the same wall clock -- this work is CPU-bound Python, so threads only
        interleave on the GIL. The wins came from doing less instead: pruning
        the discovery walk, and sharing one source scan.
        """
        def read_all():
            n = 0
            for doc in catalog.documents():
                try:
                    catalog.payload(doc.view, doc.slug)
                    n += 1
                except Exception:
                    pass
            return n

        ready.run("catalog", catalog.documents, describe=lambda d: f"{len(d)} ledger(s)")
        ready.run("payloads", read_all, describe=lambda n: f"{n} ledger(s) read")
        ready.run("xref", refresh_xref, describe=lambda x: f"{x.stats()['declarations']} declarations")
        ready.run("sources", decls.source_index, describe=lambda _: "Lean sources indexed")
        ready.run("statements", decls.statements, describe=lambda m: f"{len(m)} elaborated statement(s)")
        ready.run("workspace", lambda: workspace_payload(False),
                  describe=lambda r: f"{len(r[0].get('census_rows', []))} census document(s)")
        ready.run("literature", alignment.library,
                  describe=lambda lib: f"{len(lib.documents)} source document(s)")
        ready.run("graph", decls.graph_status,
                  describe=lambda g: (f"{g['declarations']} declarations"
                                      + (" (stale)" if g.get("stale") else "")) if g and g.get("present")
                                     else "no saved graph")

    @app.on_event("startup")
    async def _watch() -> None:
        app.state.watch_task = asyncio.create_task(_watch_files(app, catalog))
        # In a worker thread: these are blocking, and the server must answer
        # while they run so the UI can show what is still coming.
        app.state.warm_task = asyncio.create_task(asyncio.to_thread(_warm))

    @app.on_event("shutdown")
    async def _stop_watch() -> None:
        task = getattr(app.state, "watch_task", None)
        if task:
            task.cancel()

    return app


def _rows_of(payload: Any) -> list[dict[str, Any]]:
    """Navigable rows of a payload, wherever this schema happens to keep them."""
    from .xref import _row_identity

    for container in (
        payload.get("data", {}).get("items") if isinstance(payload.get("data"), dict) else None,
        payload.get("data", {}).get("rows") if isinstance(payload.get("data"), dict) else None,
        payload.get("items"),
        payload.get("rows"),
        payload.get("results"),
    ):
        if isinstance(container, list) and container:
            out = []
            for node in container:
                if not isinstance(node, dict):
                    continue
                ident = _row_identity(node)
                if ident:
                    out.append({"id": ident[0], "title": ident[1]})
            if out:
                return out
    # An alignment payload keeps its rows one level down, per paper.
    papers = payload.get("papers")
    if isinstance(papers, list):
        out = []
        for paper in papers:
            for node in (paper or {}).get("rows", []) or []:
                if isinstance(node, dict) and node.get("id"):
                    out.append({"id": node["id"], "title": node.get("title") or node["id"]})
        return out
    return []


#: Injected into every served viewer page. The viewers are self-contained
#: documents that know nothing about the shell around them, and rewriting seven
#: of them to add navigation would fork each from its static twin. Instead the
#: shell reaches in: declaration names become clickable, and a click is posted
#: up to the parent frame, which owns routing.
_BRIDGE = r"""
<script>
(function(){
  if (window.top === window.self) return;   // opened directly: stay static
  const NAME = /^[A-Za-z_][A-Za-z0-9_'\u2080-\u2089!?]*(\.[A-Za-z0-9_'\u2080-\u2089!?]+)+$/;
  const link = el => {
    const t = (el.textContent || '').trim();
    if (!NAME.test(t) || t.length > 200 || el.dataset.xr) return;
    el.dataset.xr = '1';
    el.style.cursor = 'pointer';
    el.style.textDecoration = 'underline dotted';
    el.style.textUnderlineOffset = '2px';
    el.title = 'Show every ledger that names ' + t;
    el.addEventListener('click', ev => {
      ev.preventDefault(); ev.stopPropagation();
      parent.postMessage({aiq: 'declaration', name: t}, '*');
    });
  };
  const scan = () => document.querySelectorAll('code, .decl h3 code, td code').forEach(link);
  scan();
  new MutationObserver(scan).observe(document.body, {childList: true, subtree: true});
  document.addEventListener('keydown', ev => {
    if (ev.key === '/' && !/input|textarea/i.test((ev.target.tagName || ''))) {
      ev.preventDefault(); parent.postMessage({aiq: 'focus-search'}, '*');
    }
  });
  // The shell asks for a row by its census id. Viewers anchor rows
  // differently -- an exact id, a prefixed anchor, or nothing at all -- so try
  // the cheap selectors first and fall back to finding the text.
  addEventListener('message', ev => {
    const m = ev.data || {};
    if (m.aiq === 'theme') {
      if (m.value === 'system') document.documentElement.removeAttribute('data-theme');
      else document.documentElement.setAttribute('data-theme', m.value);
      return;
    }
    if (m.aiq !== 'scroll-to' || !m.id) return;
    const esc = (window.CSS && CSS.escape) ? CSS.escape(m.id) : m.id.replace(/[^\w-]/g, '\\$&');
    let el = document.getElementById(m.id)
          || document.querySelector('[id$="-' + esc + '"]')
          || document.querySelector('[data-id="' + esc + '"]');
    if (!el) {
      for (const c of document.querySelectorAll('code, td, h2, h3')) {
        if ((c.textContent || '').trim() === m.id) { el = c; break; }
      }
    }
    if (!el) return;
    const box = el.closest('section, tr, .row, .decl, article') || el;
    box.scrollIntoView({behavior: 'smooth', block: 'center'});
    const prev = box.style.outline;
    box.style.outline = '2px solid #2f6f4f';
    box.style.outlineOffset = '3px';
    setTimeout(() => { box.style.outline = prev; }, 2200);
  });

  parent.postMessage({aiq: 'ready', title: document.title}, '*');
})();
</script>
"""


def _with_bridge(page: str, theme: str | None = None) -> str:
    """Attach the shell bridge, and stamp the shell's theme for the first paint.

    An iframe is a separate document and never inherits the shell's
    ``data-theme``, so a viewer opened inside a dark shell painted itself light
    -- dark text on the dark ground showing through. The stamp avoids the flash;
    the bridge's message handler keeps it in step when the theme is toggled.
    """
    if theme in ("dark", "light") and "<html" in page:
        i = page.find("<html")
        page = page[:i] + f'<html data-theme="{theme}"' + page[page.find(">", i):]
    return page + _BRIDGE


async def _watch_files(app, catalog: Catalog, interval: float = 1.0) -> None:
    """Poll the catalog's files and push a reload when one changes on disk."""
    stamps: dict[Path, tuple[int, int]] = {}
    while True:
        try:
            changed: list[str] = []
            for path in catalog.watched_files():
                try:
                    st = path.stat()
                except FileNotFoundError:
                    continue
                stamp = (st.st_mtime_ns, st.st_size)
                if stamps.get(path) not in (None, stamp):
                    changed.append(path.name)
                stamps[path] = stamp
            if changed:
                await _broadcast(app, {"kind": "changed", "files": changed})
        except asyncio.CancelledError:  # pragma: no cover
            raise
        except Exception:
            pass
        await asyncio.sleep(interval)


async def _broadcast(app, message: dict[str, Any]) -> None:
    dead = []
    for ws in list(app.state.sockets):
        try:
            await ws.send_text(json.dumps(message))
        except Exception:
            dead.append(ws)
    for ws in dead:
        app.state.sockets.discard(ws)


def _walk_strings(node: Any, path: str = "", depth: int = 0):
    if depth > 12:
        return
    if isinstance(node, str):
        if len(node) >= 2:
            yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_strings(v, f"{path}.{k}" if path else str(k), depth + 1)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_strings(v, f"{path}[{i}]", depth + 1)


def _excerpt(text: str, needle: str, width: int = 130) -> str:
    i = text.lower().find(needle)
    start = max(0, i - width // 3)
    out = text[start : start + width]
    return ("…" if start else "") + out + ("…" if start + width < len(text) else "")


def serve(root: Path, *, host: str = "127.0.0.1", port: int = 8800,
          title: str = "Formalization workspace", private_sources: str | None = None,
          include_private: bool = False) -> int:
    _require_fastapi()
    import uvicorn

    app = create_app(root, title=title, private_sources=private_sources,
                     include_private=include_private)
    if include_private:
        print("LOCAL MODE: private source excerpts will be rendered in this session")
    print(f"serving {root} at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0

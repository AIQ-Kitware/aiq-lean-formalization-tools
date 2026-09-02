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
import json
from pathlib import Path
from typing import Any

from ..viewer import read_asset, viewer_html
from .edits import EditRefused, apply_edit, read_journal
from .registry import Catalog

# Imported at module scope, not inside create_app: this module uses
# ``from __future__ import annotations``, so FastAPI resolves a route's
# annotations against these module globals. Bound inside a function they are
# invisible to that lookup, and a `request: Request` parameter silently becomes
# a required query parameter instead of the request body.
try:
    from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
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


def create_app(root: Path, *, title: str = "Formalization workspace"):
    _require_fastapi()

    root = Path(root).expanduser().resolve()
    catalog = Catalog(root)
    app = FastAPI(title=title)
    app.state.root = root
    app.state.catalog = catalog
    app.state.sockets: set[Any] = set()

    # -- catalog and payloads ---------------------------------------------

    @app.get("/api/catalog")
    def api_catalog(refresh: bool = False) -> JSONResponse:
        if refresh:
            catalog.scan(force=True)
        docs = [d.as_json(root) for d in catalog.documents()]
        views = [{"name": s.name, "label": s.label} for s in catalog.specs]
        views.append({"name": "workspace", "label": "Workspace"})
        views.append({"name": "alignment", "label": "Alignment"})
        return JSONResponse(
            {"root": str(root), "title": title, "views": views, "documents": docs}
        )

    @app.get("/api/payload/{view}/{slug}")
    def api_payload(view: str, slug: str) -> JSONResponse:
        try:
            payload, _ = catalog.payload(view, slug)
        except KeyError:
            raise HTTPException(404, f"no {view} document named {slug}")
        return JSONResponse(payload)

    @app.get("/api/payload/workspace")
    def api_workspace(source_audit: bool = False) -> JSONResponse:
        from ..workspace import FormalizationWorkspace

        ws = FormalizationWorkspace.discover(root)
        return JSONResponse(ws.payload(include_source_audit=source_audit))

    # -- rendered viewers, identical to the static files ------------------

    @app.get("/view/{view}/{slug}", response_class=HTMLResponse)
    def view_page(view: str, slug: str) -> HTMLResponse:
        spec = catalog.spec(view)
        if spec is None:
            raise HTTPException(404, f"unknown view {view}")
        try:
            payload, doc_title = catalog.payload(view, slug)
        except KeyError:
            raise HTTPException(404, f"no {view} document named {slug}")
        return HTMLResponse(viewer_html(spec.asset, doc_title, payload))

    @app.get("/view/workspace", response_class=HTMLResponse)
    def workspace_page(source_audit: bool = False) -> HTMLResponse:
        from ..workspace import FormalizationWorkspace

        ws = FormalizationWorkspace.discover(root)
        data = ws.payload(include_source_audit=source_audit)
        return HTMLResponse(viewer_html("workspace_viewer.html", ws.payload_title(data), data))

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

    @app.on_event("startup")
    async def _watch() -> None:
        app.state.watch_task = asyncio.create_task(_watch_files(app, catalog))

    @app.on_event("shutdown")
    async def _stop_watch() -> None:
        task = getattr(app.state, "watch_task", None)
        if task:
            task.cancel()

    return app


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


def serve(root: Path, *, host: str = "127.0.0.1", port: int = 8800, title: str = "Formalization workspace") -> int:
    _require_fastapi()
    import uvicorn

    app = create_app(root, title=title)
    print(f"serving {root} at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0

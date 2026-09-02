"""One way to put a payload into a viewer template.

Every viewer in this package is the same shape: build a JSON payload, drop it
into an HTML template beside a title. That was written out longhand in seven
modules, each with slightly different escaping, and a payload that existed only
as a local inside ``render_html`` -- so nothing but the static page could reach
it.

Splitting the payload from the rendering gives the payload a name. ``aiq-lean
serve`` returns the same object over HTTP that the static page embeds, so a
served view and a written file cannot drift.

The escape set is the strict one: ``<``, ``>`` and ``&`` never survive into the
inline ``<script>`` as themselves, so no payload string can close the tag it
sits in.
"""

from __future__ import annotations

import base64 as _base64
import functools as _functools
import html as _html
import json as _json
import re as _re
from importlib import resources
from typing import Any


def encode_payload(payload: Any) -> str:
    """JSON for embedding in an inline ``<script>`` element."""
    text = _json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def read_asset(asset: str) -> str:
    return resources.files("aiq_lean_tools").joinpath(f"assets/{asset}").read_text(encoding="utf-8")


def _with_theme(page: str) -> str:
    """Put the shared theme in front of the viewer's own stylesheet.

    Six of the seven viewers declared no colours at all, so a browser in dark
    mode rendered them white. Injecting before their own <style> means a viewer
    that already styles something keeps its rules and this only fills the gap.
    """
    css = read_asset("theme.css")
    block = f'<style id="aiq-theme">\n{css}\n</style>'
    marker = "<style"
    i = page.find(marker)
    if i == -1:
        return page + block
    return page[:i] + block + page[i:]


def _vendor_bytes(name: str) -> bytes:
    return resources.files("aiq_lean_tools").joinpath(f"assets/vendor/{name}").read_bytes()


_FONT_SRC = _re.compile(r"src:url\(fonts/(KaTeX_[A-Za-z0-9-]+)\.woff2\)[^;}]*")


@_functools.lru_cache(maxsize=1)
def katex_bundle() -> str:
    """KaTeX as one self-contained block: no CDN, no sibling files, no network.

    A review packet is opened from a file share, an archive, or a checkout with
    no network, and a page whose mathematics silently fails to render is worse
    than one that never promised to.  So the fonts are inlined: the stylesheet's
    ``@font-face`` rules are rewritten to a single ``woff2`` data URI each, and
    the ``woff``/``ttf`` fallbacks -- which would resolve to files that are not
    there -- are dropped.

    The cost is about 700 KB on a page that already carries a megabyte of JSON,
    and it is paid only by viewers that ask for mathematics.
    """
    css = _vendor_bytes("katex/katex.min.css").decode("utf-8")

    def inline(match: _re.Match[str]) -> str:
        data = _base64.b64encode(_vendor_bytes(f"katex/fonts/{match.group(1)}.woff2")).decode()
        return f"src:url(data:font/woff2;base64,{data}) format(\"woff2\")"

    css = _FONT_SRC.sub(inline, css)
    js = _vendor_bytes("katex/katex.min.js").decode("utf-8")
    version = _vendor_bytes("katex/VERSION").decode("utf-8").strip()
    return (
        f"<!-- KaTeX {version}, MIT licensed, vendored; see assets/vendor/katex/LICENSE -->\n"
        f"<style id=\"katex-css\">\n{css}\n</style>\n"
        f"<script id=\"katex-js\">{js}</script>\n"
    )


def viewer_html(asset: str, title: str, payload: Any, *, math: bool = False) -> str:
    """Render ``asset`` with ``payload`` embedded and ``title`` substituted.

    ``math`` inlines the vendored KaTeX bundle, for viewers that render
    literature mathematics rather than only Lean and prose.
    """
    page = (
        read_asset(asset)
        .replace("__TITLE__", _html.escape(str(title)))
        .replace("__PAYLOAD__", encode_payload(payload))
    )
    page = _with_theme(page)
    if math:
        marker = "</head>"
        i = page.find(marker)
        bundle = katex_bundle()
        page = page[:i] + bundle + page[i:] if i != -1 else bundle + page
    return page

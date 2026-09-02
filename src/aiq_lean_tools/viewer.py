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

import html as _html
import json as _json
from importlib import resources
from typing import Any


def encode_payload(payload: Any) -> str:
    """JSON for embedding in an inline ``<script>`` element."""
    text = _json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def read_asset(asset: str) -> str:
    return resources.files("aiq_lean_tools").joinpath(f"assets/{asset}").read_text(encoding="utf-8")


def viewer_html(asset: str, title: str, payload: Any) -> str:
    """Render ``asset`` with ``payload`` embedded and ``title`` substituted."""
    return (
        read_asset(asset)
        .replace("__TITLE__", _html.escape(str(title)))
        .replace("__PAYLOAD__", encode_payload(payload))
    )

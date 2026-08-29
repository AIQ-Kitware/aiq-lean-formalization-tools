"""Self-contained interactive HTML renderers for leanq graph payloads."""

from __future__ import annotations

import html
import json
from importlib.resources import files
from pathlib import Path


_GRAPH_TEMPLATE = "assets/viewer.html"
_HEADLINE_TEMPLATE = "assets/headline_viewer.html"
_PROJECT_EXPLORER_TEMPLATE = "assets/project_explorer.html"
_COMPARISON_TEMPLATE = "assets/comparison_viewer.html"


def _safe_json_for_script(payload: dict) -> str:
    """JSON text safe to embed in a ``<script type=application/json>`` node."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # HTML parsers recognize </script> even inside non-JavaScript script data.
    # Escaping angle brackets preserves the JSON value while preventing that
    # sentinel and keeps arbitrary declaration/documentation text inert.
    return text.replace("<", r"\u003c").replace(">", r"\u003e").replace("&", r"\u0026")


def _display_title(payload: dict, title: str | None = None) -> str:
    if title:
        return title
    presentation = payload.get("presentation") or {}
    if presentation.get("title"):
        return str(presentation["title"])
    if payload.get("payloadKind") == "headline-consumption":
        return "Headline theorem consumption"
    if payload.get("payloadKind") == "headline-dependencies":
        return "Headline theorem dependency foundations"
    if payload.get("payloadKind") == "project-explorer":
        return "Whole formalization dependency explorer"
    targets = payload.get("targets") or payload.get("bootstrapTargets") or []
    result = "Lean proof dependencies"
    if targets:
        result += f": {str(targets[0]).rsplit('.', 1)[-1]}"
    return result


def render_graph_html(payload: dict, *, title: str | None = None) -> str:
    """Render one saved payload as a standalone, offline HTML document.

    Rendering is pure Python/resource loading.  It never invokes Lean, so an HTML
    template can be iterated against a stable JSON artifact.
    """
    if payload.get("payloadKind") in {"project-explorer", "headline-dependencies"}:
        template_name = _PROJECT_EXPLORER_TEMPLATE
    elif payload.get("payloadKind") == "headline-consumption":
        template_name = _HEADLINE_TEMPLATE
    else:
        template_name = _GRAPH_TEMPLATE
    template = files("leanq").joinpath(template_name).read_text(encoding="utf-8")
    return template.replace(
        "__LEANQ_TITLE__", html.escape(_display_title(payload, title))
    ).replace("__LEANQ_DATA__", _safe_json_for_script(payload))



def render_comparison_html(payload: dict, *, title: str | None = None) -> str:
    """Render the generic package-first census ancestry/comparison publisher."""
    template = files("leanq").joinpath(_COMPARISON_TEMPLATE).read_text(encoding="utf-8")
    display_title = title or "Proof dependency comparison"
    return template.replace(
        "__LEANQ_TITLE__", html.escape(display_title)
    ).replace("__LEANQ_DATA__", _safe_json_for_script(payload))


def write_comparison_html(path: Path, payload: dict, *, title: str | None = None) -> Path:
    """Write the generic package-first comparison publisher."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_comparison_html(payload, title=title), encoding="utf-8")
    return path

def write_graph_html(path: Path, payload: dict, *, title: str | None = None) -> Path:
    """Write a standalone graph viewer, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_graph_html(payload, title=title), encoding="utf-8")
    return path

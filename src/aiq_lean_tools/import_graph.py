"""Source-level Lean import graph and conservative local import closures."""
from __future__ import annotations

import collections
import html
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .common import Path, find_workspace_root
from .lean_source import strip_comments

IMPORT_LINE_RE = re.compile(r"(?m)^\s*(?:public\s+)?import\s+(.+?)\s*$")
MODULE_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_'.₀-₉⁰-⁹′]*$")


def module_suffix(module: str) -> pathlib.PurePosixPath:
    return pathlib.PurePosixPath(*module.split(".")).with_suffix(".lean")


def imports_of(path: Path) -> tuple[str, ...]:
    clean = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    out: list[str] = []
    for match in IMPORT_LINE_RE.finditer(clean):
        for token in match.group(1).split():
            token = token.strip()
            if MODULE_TOKEN_RE.match(token):
                out.append(token)
    return tuple(dict.fromkeys(out))


@dataclass(frozen=True)
class ImportResolution:
    module: str
    path: Path | None
    candidates: tuple[Path, ...] = ()


@dataclass
class SourceImportGraph:
    root: Path
    files: tuple[Path, ...]
    imports: dict[Path, tuple[str, ...]]
    suffix_index: dict[str, tuple[Path, ...]]

    @classmethod
    def discover(
        cls,
        root: str | pathlib.Path | None = None,
        *,
        exclude_dirs: Iterable[str] = (".git", ".lake", "build", "vendor", "external"),
    ) -> "SourceImportGraph":
        base = find_workspace_root(root)
        excluded = set(exclude_dirs)
        files = tuple(sorted(
            path for path in base.rglob("*.lean")
            if not excluded.intersection(path.relative_to(base).parts)
        ))
        suffixes: dict[str, list[Path]] = collections.defaultdict(list)
        for path in files:
            parts = path.relative_to(base).parts
            for index in range(len(parts)):
                suffixes[pathlib.PurePosixPath(*parts[index:]).as_posix()].append(path)
        suffix_index = {
            suffix: tuple(sorted(paths, key=lambda p: (len(p.relative_to(base).parts), p.as_posix())))
            for suffix, paths in suffixes.items()
        }
        return cls(base, files, {path: imports_of(path) for path in files}, suffix_index)

    def resolve(self, module: str) -> ImportResolution:
        suffix = module_suffix(module).as_posix()
        matches = self.suffix_index.get(suffix, ())
        return ImportResolution(module, matches[0] if matches else None, tuple(matches))

    def canonical_module(self, path: Path) -> str:
        """Return a deterministic source-path module label.

        This label is for source reports only.  When a library has ``srcDir``, the
        imported module name may be a suffix of this path label; ``resolve`` handles
        that explicitly instead of claiming the path label is Lean's semantic module.
        """
        return ".".join(path.relative_to(self.root).with_suffix("").parts)

    def local_edges(self) -> list[dict]:
        rows: list[dict] = []
        for source in self.files:
            source_rel = source.relative_to(self.root).as_posix()
            for imported in self.imports[source]:
                resolution = self.resolve(imported)
                if resolution.path is None:
                    continue
                rows.append({
                    "source_path": source_rel,
                    "source_module": self.canonical_module(source),
                    "imported_module": imported,
                    "target_path": resolution.path.relative_to(self.root).as_posix(),
                    "ambiguous_resolution": len(resolution.candidates) > 1,
                    "resolution_candidates": [p.relative_to(self.root).as_posix() for p in resolution.candidates],
                })
        return rows

    def closure(self, root_module: str) -> dict:
        resolution = self.resolve(root_module)
        if resolution.path is None:
            raise KeyError(f"cannot resolve local Lean target {root_module}")
        queue = collections.deque([resolution.path])
        seen: dict[Path, str] = {resolution.path: root_module}
        unresolved: list[dict] = []
        ambiguities: list[dict] = []
        while queue:
            source = queue.popleft()
            for imported in self.imports[source]:
                dep = self.resolve(imported)
                if dep.path is None:
                    unresolved.append({
                        "source": source.relative_to(self.root).as_posix(),
                        "module": imported,
                    })
                    continue
                if len(dep.candidates) > 1:
                    ambiguities.append({
                        "source": source.relative_to(self.root).as_posix(),
                        "module": imported,
                        "selected": dep.path.relative_to(self.root).as_posix(),
                        "candidates": [p.relative_to(self.root).as_posix() for p in dep.candidates],
                    })
                if dep.path not in seen:
                    seen[dep.path] = imported
                    queue.append(dep.path)
        rows = []
        for path, imported_as in sorted(seen.items(), key=lambda kv: kv[0].as_posix()):
            rel = path.relative_to(self.root)
            family = rel.parts[0] if len(rel.parts) > 1 else Path(rel.parts[0]).stem
            rows.append({
                "path": rel.as_posix(),
                "imported_as": imported_as,
                "family": family,
            })
        return {
            "root_module": root_module,
            "local_module_count": len(rows),
            "families": dict(sorted(collections.Counter(row["family"] for row in rows).items())),
            "modules": rows,
            "unresolved_imports": unresolved,
            "resolution_ambiguities": ambiguities,
        }

    def compare_closures(self, targets: Sequence[tuple[str, str]]) -> dict:
        closures: dict[str, dict] = {}
        path_sets: dict[str, set[str]] = {}
        for label, module in targets:
            data = self.closure(module)
            data["label"] = label
            closures[label] = data
            path_sets[label] = {row["path"] for row in data["modules"]}
        overlaps: list[dict] = []
        labels = [label for label, _ in targets]
        for i, left in enumerate(labels):
            for right in labels[i + 1:]:
                shared = sorted(path_sets[left] & path_sets[right])
                overlaps.append({"left": left, "right": right, "count": len(shared), "paths": shared})
        return {"targets": list(closures.values()), "overlaps": overlaps}

    def render_closure_html(self, targets: Sequence[tuple[str, str]]) -> str:
        payload = self.compare_closures(targets)
        data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
        cards = []
        for target in payload["targets"]:
            fam = ", ".join(f"{html.escape(k)}={v}" for k, v in target["families"].items())
            cards.append(
                f"<section class='card'><h2>{html.escape(target['label'])}</h2>"
                f"<p><code>{html.escape(target['root_module'])}</code></p>"
                f"<p><strong>{target['local_module_count']}</strong> local source modules</p>"
                f"<p class='muted'>{fam}</p></section>"
            )
        return f"""<!doctype html>
<meta charset='utf-8'>
<title>Lean source import closures</title>
<style>
body{{font-family:system-ui,sans-serif;margin:2rem;line-height:1.45;color:#222}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem}} .card{{border:1px solid #ccc;border-radius:10px;padding:1rem}} .muted{{color:#666}} table{{border-collapse:collapse;width:100%;margin-top:1rem}} th,td{{border-bottom:1px solid #ddd;text-align:left;padding:.45rem}} code{{word-break:break-word}}
</style>
<h1>Lean source import closures</h1>
<p>This is a source-import upper bound, not a declaration-level proof-dependency claim.</p>
<div class='cards'>{''.join(cards)}</div>
<h2>Pairwise overlap</h2>
<table><thead><tr><th>Left</th><th>Right</th><th>Shared local modules</th></tr></thead><tbody>
{''.join(f"<tr><td>{html.escape(row['left'])}</td><td>{html.escape(row['right'])}</td><td>{row['count']}</td></tr>" for row in payload['overlaps'])}
</tbody></table>
<script type='application/json' id='import-closure-data'>{data}</script>
"""

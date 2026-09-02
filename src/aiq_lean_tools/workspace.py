from __future__ import annotations

import collections
import fnmatch
import html
import json
import os
import pathlib

import yaml
from dataclasses import dataclass
from importlib import resources
from typing import Any, Iterable, Sequence

from .audits import source_audit_summary
from .census import CensusDocument, load_census
from .common import Finding, Path, find_workspace_root
from .coverage import CoverageBundle, load_coverage_bundle
from .foundations import FoundationMap, check_foundation_map
from .lean_source import LeanSourceIndex, SourceScope, scan_lean_project
from .literature import LiteratureDocument, load_literature
from .manifest import FormalizationManifest, load_manifest
from .semantic_review import SemanticReviewDocument, load_semantic_review

DEFAULT_CENSUS_GLOBS = ("**/*full-source-census.json", "**/*source-census.json")
DEFAULT_REVIEW_GLOBS = ("**/*result-semantic-review.json", "**/*semantic-review.json")
DEFAULT_COVERAGE_GLOBS = ("**/*formalization-result-inventory.json", "**/*result-inventory.json")
DEFAULT_LITERATURE_GLOBS = ("**/*literature*.json", "**/*literature*.yaml", "**/*literature*.yml", "**/distilled_literature/source_manifest.json")
DEFAULT_FOUNDATION_GLOBS = ("**/*foundation*.json", "**/*foundation*.yaml", "**/*foundation*.yml")
SKIP_PARTS = {".git", ".lake", "build", ".venv", "venv", "node_modules"}


@dataclass
class FormalizationWorkspace:
    root: Path
    census_paths: list[Path]
    review_paths: list[Path]
    coverage_paths: list[Path]
    literature_paths: list[Path]
    foundation_paths: list[Path]
    manifest_path: Path | None = None

    @classmethod
    def discover(
        cls,
        root: str | pathlib.Path | None = None,
        *,
        census_globs: Sequence[str] = DEFAULT_CENSUS_GLOBS,
        review_globs: Sequence[str] = DEFAULT_REVIEW_GLOBS,
        coverage_globs: Sequence[str] = DEFAULT_COVERAGE_GLOBS,
        literature_globs: Sequence[str] = DEFAULT_LITERATURE_GLOBS,
        foundation_globs: Sequence[str] = DEFAULT_FOUNDATION_GLOBS,
    ) -> "FormalizationWorkspace":
        base = find_workspace_root(root)
        census_paths = _glob_unique(base, census_globs)
        review_paths = _glob_unique(base, review_globs)
        coverage_paths = _glob_unique(base, coverage_globs)
        literature_paths = [path for path in _glob_unique(base, literature_globs) if _looks_like_literature(path)]
        foundation_paths = [path for path in _glob_unique(base, foundation_globs) if _looks_like_foundation_map(path)]
        # A dedicated semantic review can match a generic source-census glob if a
        # user chose a broad custom pattern.  Classification by top-level keys is
        # cheap and keeps the workspace robust.
        classified_census: list[Path] = []
        classified_review: list[Path] = list(review_paths)
        review_set = set(review_paths)
        for path in census_paths:
            if path in review_set:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                classified_census.append(path)
                continue
            if isinstance(data, dict) and "rows" in data and "relation_definitions" in data:
                classified_review.append(path)
            else:
                classified_census.append(path)
        manifest = base / "formalization.yaml"
        return cls(
            base, classified_census, sorted(set(classified_review)), coverage_paths,
            literature_paths, foundation_paths, manifest if manifest.is_file() else None,
        )

    def censuses(self) -> list[CensusDocument]:
        return [load_census(path, root=self.root) for path in self.census_paths]

    def reviews(self) -> list[SemanticReviewDocument]:
        return [load_semantic_review(path, root=self.root) for path in self.review_paths]

    def coverage_bundles(self) -> list[CoverageBundle]:
        return [load_coverage_bundle(path, root=self.root) for path in self.coverage_paths]

    def literature_documents(self) -> list[LiteratureDocument]:
        return [load_literature(path, root=self.root) for path in self.literature_paths]

    def foundation_maps(self) -> list[FoundationMap]:
        return [FoundationMap.load(path) for path in self.foundation_paths]

    def manifest(self) -> FormalizationManifest | None:
        return load_manifest(self.manifest_path) if self.manifest_path else None

    def validate(self, *, source_locations: bool = True, static_declarations: bool = False) -> list[Finding]:
        findings: list[Finding] = []
        source_index: LeanSourceIndex | None = None
        if static_declarations:
            source_index = scan_lean_project(self.root)
        for doc in self.censuses():
            for finding in doc.validate(check_source_locations=source_locations):
                findings.append(Finding(finding.level, finding.code, finding.message, f"{doc.path.relative_to(self.root)}:{finding.location}" if finding.location else str(doc.path.relative_to(self.root))))
            if source_index is not None:
                for finding in doc.static_declaration_findings(source_index):
                    findings.append(Finding(finding.level, finding.code, finding.message, f"{doc.path.relative_to(self.root)}:{finding.location}" if finding.location else str(doc.path.relative_to(self.root))))
        for doc in self.reviews():
            for finding in doc.validate():
                findings.append(Finding(finding.level, finding.code, finding.message, f"{doc.path.relative_to(self.root)}:{finding.location}" if finding.location else str(doc.path.relative_to(self.root))))
        for bundle in self.coverage_bundles():
            rel = bundle.results.path.relative_to(self.root)
            for finding in bundle.validate(static_declarations=static_declarations, source_index=source_index):
                findings.append(Finding(finding.level, finding.code, finding.message, f"{rel}:{finding.location}" if finding.location else str(rel)))
        for doc in self.literature_documents():
            rel = doc.path.relative_to(self.root)
            for finding in doc.validate():
                findings.append(Finding(finding.level, finding.code, finding.message, f"{rel}:{finding.location}" if finding.location else str(rel)))
        foundation_maps = self.foundation_maps()
        if foundation_maps and source_index is None:
            source_index = scan_lean_project(self.root)
        for fmap in foundation_maps:
            rel = fmap.path.relative_to(self.root) if fmap.path else Path("foundation-map")
            report = check_foundation_map(fmap, root=self.root, index=source_index)
            for finding in report.findings:
                findings.append(Finding(finding.level, finding.code, finding.message, f"{rel}:{finding.location}" if finding.location else str(rel)))
        manifest = self.manifest()
        if manifest:
            for finding in manifest.validate():
                findings.append(Finding(finding.level, finding.code, finding.message, f"formalization.yaml:{finding.location}" if finding.location else "formalization.yaml"))
        return findings

    def overview(
        self,
        *,
        include_source_audit: bool = False,
        source_index: LeanSourceIndex | None = None,
    ) -> dict[str, Any]:
        """Totals across every ledger.

        ``source_index`` lets a caller that has already scanned the project hand
        that scan in. A long-lived server has one, and rescanning cost it ten
        seconds per workspace build for an identical result.
        """
        censuses = self.censuses()
        reviews = self.reviews()
        coverage = self.coverage_bundles()
        literature = self.literature_documents()
        foundation_maps = self.foundation_maps()
        foundation_index = source_index or (scan_lean_project(self.root) if foundation_maps else None)
        status = collections.Counter()
        verification = collections.Counter()
        importance = collections.Counter()
        alignment = collections.Counter()
        cited_by_doc: dict[str, set[str]] = {}
        declaration_docs: dict[str, set[str]] = collections.defaultdict(set)
        for doc in censuses:
            summary = doc.summary()
            status.update(summary["status"])
            verification.update(summary["verification"])
            importance.update(summary["importance"])
            alignment.update(summary["semantic_alignment"])
            label = doc.path.relative_to(self.root).as_posix()
            refs = set(doc.declaration_refs)
            cited_by_doc[label] = refs
            for ref in refs:
                declaration_docs[ref].add(label)
        cross_reuse = [
            {"declaration": name, "documents": sorted(docs), "document_count": len(docs)}
            for name, docs in declaration_docs.items() if len(docs) > 1
        ]
        cross_reuse.sort(key=lambda row: (-row["document_count"], row["declaration"]))
        data: dict[str, Any] = {
            "root": str(self.root),
            "manifest": self.manifest().summary() if self.manifest() else None,
            "census_count": len(censuses),
            "semantic_review_count": len(reviews),
            "coverage_inventory_count": len(coverage),
            "literature_inventory_count": len(literature),
            "foundation_map_count": len(foundation_maps),
            "result_rows": sum(len(doc.items) for doc in censuses),
            "review_rows": sum(len(doc.rows) for doc in reviews),
            "coverage_results": sum(len(bundle.results.results) for bundle in coverage),
            "source_fidelity_atoms": sum(len(bundle.atoms.atoms) for bundle in coverage if bundle.atoms),
            "literature_works": sum(len(doc.works) for doc in literature),
            "foundation_nodes": sum(len(fmap.nodes) for fmap in foundation_maps),
            "status": dict(status),
            "verification": dict(verification),
            "importance": dict(importance),
            "semantic_alignment": dict(alignment),
            "unique_cited_declarations": len(declaration_docs),
            "cross_document_reuse": cross_reuse,
            "censuses": [
                {**doc.summary(), "path": doc.path.relative_to(self.root).as_posix()}
                for doc in censuses
            ],
            "reviews": [
                {**doc.summary(), "path": doc.path.relative_to(self.root).as_posix()}
                for doc in reviews
            ],
            "coverage": [
                {**bundle.summary(), "path": bundle.results.path.relative_to(self.root).as_posix()}
                for bundle in coverage
            ],
            "literature": [
                {**doc.summary(), "path": doc.path.relative_to(self.root).as_posix()}
                for doc in literature
            ],
            "foundations": [
                {**check_foundation_map(fmap, root=self.root, index=foundation_index).summary(), "path": fmap.path.relative_to(self.root).as_posix() if fmap.path else ""}
                for fmap in foundation_maps
            ],
        }
        graph_path = self.root / "build" / "leanq" / "project-semantic-graph.json"
        if graph_path.is_file():
            try:
                graph = json.loads(graph_path.read_text(encoding="utf-8"))
                data["leanq_graph"] = {
                    "path": graph_path.relative_to(self.root).as_posix(),
                    "node_count": graph.get("nodeCount", len(graph.get("nodes", []))),
                    "edge_count": graph.get("edgeCount", len(graph.get("edges", []))),
                    "libraries": graph.get("libraries", []),
                }
            except Exception as ex:
                data["leanq_graph"] = {"path": graph_path.relative_to(self.root).as_posix(), "error": str(ex)}
        if include_source_audit:
            data["source_audit"] = source_audit_summary(scan_lean_project(self.root))
        return data

    def payload(
        self,
        *,
        include_source_audit: bool = False,
        source_index: LeanSourceIndex | None = None,
    ) -> dict:
        data = self.overview(include_source_audit=include_source_audit, source_index=source_index)
        # Carried inline so the static page can drill down with no server behind it.
        data["census_rows"] = [
            {
                "document": doc.path.relative_to(self.root).as_posix(),
                "title": doc.title,
                "rows": doc.items,
            }
            for doc in self.censuses()
        ]
        data["coverage_rows"] = [
            {
                "document": bundle.results.path.relative_to(self.root).as_posix(),
                "results": bundle.results.results,
                "atoms": bundle.atoms.atoms if bundle.atoms else [],
            }
            for bundle in self.coverage_bundles()
        ]
        return data

    def payload_title(self, data: dict) -> str:
        title = data.get("manifest", {}).get("project_name") if isinstance(data.get("manifest"), dict) else None
        return str(title or self.root.name)

    def render_html(self, *, include_source_audit: bool = False, source_index: LeanSourceIndex | None = None) -> str:
        from .viewer import viewer_html

        data = self.payload(include_source_audit=include_source_audit, source_index=source_index)
        return viewer_html("workspace_viewer.html", self.payload_title(data), data)


def _discovery_skip_parts(root: Path) -> set[str]:
    """Directories whose contents are not this workspace's ledgers.

    Reuses the project's ``source_scope.exclude_dirs`` so a reference checkout,
    a vendored donor, or a submitted copy of the project does not contribute its
    example or duplicated census documents to this workspace's totals.
    """
    return SKIP_PARTS | set(SourceScope.load(root).exclude_dirs)


def _pattern_tail(pattern: str) -> tuple[str, ...]:
    """The components a ``**/``-prefixed glob must match at the end of a path."""
    return tuple(pattern[3:].split("/")) if pattern.startswith("**/") else tuple(pattern.split("/"))


def _tail_matches(rel_parts: Sequence[str], tail: Sequence[str]) -> bool:
    if len(tail) > len(rel_parts):
        return False
    window = rel_parts[len(rel_parts) - len(tail):]
    return all(fnmatch.fnmatch(part, pat) for part, pat in zip(window, tail))


def _glob_unique(root: Path, patterns: Sequence[str]) -> list[Path]:
    """Ledger files under ``root``, skipping directories that hold none.

    ``root.glob("**/...")`` walks everything and discards afterwards, so a
    repository with a populated ``.lake`` paid for 164k irrelevant paths on
    every discovery -- fifteen seconds of the workspace view. Pruning during the
    walk asks the filesystem for a fraction of that.

    Matching is component-wise so ``**/`` keeps its glob meaning: a ``*`` never
    crosses a path separator, which a single fnmatch over the whole relative
    path would allow.
    """
    skip = _discovery_skip_parts(root)
    tails = [_pattern_tail(pattern) for pattern in patterns]
    found: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        rel_dir = Path(dirpath).relative_to(root)
        base_parts = () if rel_dir == Path(".") else rel_dir.parts
        for filename in filenames:
            parts = (*base_parts, filename)
            if any(_tail_matches(parts, tail) for tail in tails):
                found.add((Path(dirpath) / filename).resolve())
    return sorted(found)


def _load_mapping_for_discovery(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _looks_like_foundation_map(path: Path) -> bool:
    data = _load_mapping_for_discovery(path)
    if not data or not isinstance(data.get("nodes"), list) or not data["nodes"]:
        return False
    return all(
        isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("declaration"), str)
        for row in data["nodes"]
    )


def _looks_like_literature(path: Path) -> bool:
    data = _load_mapping_for_discovery(path)
    works = data.get("works") if data else None
    if not isinstance(works, dict) or not works:
        return False
    return all(isinstance(row, dict) and "title" in row and "authors" in row for row in works.values())

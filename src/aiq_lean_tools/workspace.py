from __future__ import annotations

import collections
import html
import json
import pathlib
from dataclasses import dataclass
from importlib import resources
from typing import Any, Iterable, Sequence

from .audits import source_audit_summary
from .census import CensusDocument, load_census
from .common import Finding, Path, find_workspace_root
from .coverage import CoverageBundle, load_coverage_bundle
from .lean_source import LeanSourceIndex, scan_lean_project
from .manifest import FormalizationManifest, load_manifest
from .semantic_review import SemanticReviewDocument, load_semantic_review

DEFAULT_CENSUS_GLOBS = ("**/*full-source-census.json", "**/*source-census.json")
DEFAULT_REVIEW_GLOBS = ("**/*result-semantic-review.json", "**/*semantic-review.json")
DEFAULT_COVERAGE_GLOBS = ("**/*formalization-result-inventory.json", "**/*result-inventory.json")
SKIP_PARTS = {".git", ".lake", "build", ".venv", "venv", "node_modules"}


@dataclass
class FormalizationWorkspace:
    root: Path
    census_paths: list[Path]
    review_paths: list[Path]
    coverage_paths: list[Path]
    manifest_path: Path | None = None

    @classmethod
    def discover(
        cls,
        root: str | pathlib.Path | None = None,
        *,
        census_globs: Sequence[str] = DEFAULT_CENSUS_GLOBS,
        review_globs: Sequence[str] = DEFAULT_REVIEW_GLOBS,
        coverage_globs: Sequence[str] = DEFAULT_COVERAGE_GLOBS,
    ) -> "FormalizationWorkspace":
        base = find_workspace_root(root)
        census_paths = _glob_unique(base, census_globs)
        review_paths = _glob_unique(base, review_globs)
        coverage_paths = _glob_unique(base, coverage_globs)
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
        return cls(base, classified_census, sorted(set(classified_review)), coverage_paths, manifest if manifest.is_file() else None)

    def censuses(self) -> list[CensusDocument]:
        return [load_census(path, root=self.root) for path in self.census_paths]

    def reviews(self) -> list[SemanticReviewDocument]:
        return [load_semantic_review(path, root=self.root) for path in self.review_paths]

    def coverage_bundles(self) -> list[CoverageBundle]:
        return [load_coverage_bundle(path, root=self.root) for path in self.coverage_paths]

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
        manifest = self.manifest()
        if manifest:
            for finding in manifest.validate():
                findings.append(Finding(finding.level, finding.code, finding.message, f"formalization.yaml:{finding.location}" if finding.location else "formalization.yaml"))
        return findings

    def overview(self, *, include_source_audit: bool = False) -> dict[str, Any]:
        censuses = self.censuses()
        reviews = self.reviews()
        coverage = self.coverage_bundles()
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
            "result_rows": sum(len(doc.items) for doc in censuses),
            "review_rows": sum(len(doc.rows) for doc in reviews),
            "coverage_results": sum(len(bundle.results.results) for bundle in coverage),
            "source_fidelity_atoms": sum(len(bundle.atoms.atoms) for bundle in coverage if bundle.atoms),
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

    def render_html(self, *, include_source_audit: bool = False) -> str:
        data = self.overview(include_source_audit=include_source_audit)
        # Include compact row data for drill-down without needing a web server.
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
        template = resources.files("aiq_lean_tools").joinpath("assets/workspace_viewer.html").read_text(encoding="utf-8")
        title = data.get("manifest", {}).get("project_name") if isinstance(data.get("manifest"), dict) else None
        title = title or self.root.name
        encoded = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
        return template.replace("__TITLE__", html.escape(str(title))).replace("__PAYLOAD__", encoded)


def _glob_unique(root: Path, patterns: Sequence[str]) -> list[Path]:
    found: set[Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file() and not SKIP_PARTS.intersection(path.relative_to(root).parts):
                found.add(path.resolve())
    return sorted(found)

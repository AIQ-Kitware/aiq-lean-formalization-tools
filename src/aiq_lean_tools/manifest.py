from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Any, Mapping

import yaml

from .common import Finding, Path
from .errors import ValidationError


class ManifestValidationError(ValidationError):
    pass


@dataclass
class FormalizationManifest:
    path: Path
    data: dict[str, Any]

    @property
    def project_name(self) -> str:
        project = self.data.get("project", {})
        return str(project.get("name", self.path.parent.name)) if isinstance(project, Mapping) else self.path.parent.name

    def validate(self) -> list[Finding]:
        out: list[Finding] = []
        if not isinstance(self.data.get("version"), str):
            out.append(Finding("error", "version", "formalization manifest requires a string version"))
        project = self.data.get("project")
        if not isinstance(project, Mapping):
            out.append(Finding("error", "project", "manifest requires a project object"))
        elif not str(project.get("name", "")).strip():
            out.append(Finding("error", "project-name", "project.name cannot be empty"))
        sources = self.data.get("sources", [])
        if sources is not None and not isinstance(sources, list):
            out.append(Finding("error", "sources", "sources must be a list"))
        elif isinstance(sources, list):
            for index, source in enumerate(sources):
                if not isinstance(source, Mapping):
                    out.append(Finding("error", "source-type", "source must be an object", f"sources[{index}]"))
                    continue
                if not str(source.get("title", "")).strip():
                    out.append(Finding("error", "source-title", "source title cannot be empty", f"sources[{index}]"))
                authors = source.get("authors")
                if authors is not None and (not isinstance(authors, list) or not all(isinstance(x, str) for x in authors)):
                    out.append(Finding("error", "source-authors", "source authors must be a list of strings", f"sources[{index}]"))
        status = self.data.get("status")
        if status is not None and not isinstance(status, Mapping):
            out.append(Finding("error", "status", "status must be an object"))
        return out

    def summary(self) -> dict[str, Any]:
        project = self.data.get("project", {}) if isinstance(self.data.get("project"), Mapping) else {}
        sources = self.data.get("sources", []) if isinstance(self.data.get("sources"), list) else []
        status = self.data.get("status", {}) if isinstance(self.data.get("status"), Mapping) else {}
        automation = self.data.get("automation", {}) if isinstance(self.data.get("automation"), Mapping) else {}
        return {
            "path": str(self.path),
            "version": self.data.get("version"),
            "project_name": project.get("name"),
            "authors": project.get("authors", []),
            "source_count": len(sources),
            "source_titles": [source.get("title") for source in sources if isinstance(source, Mapping)],
            "sorry_count": status.get("sorry_count"),
            "axioms": status.get("axioms", []),
            "automation_methods": len(automation.get("methods", [])) if isinstance(automation.get("methods"), list) else 0,
        }


def load_manifest(path: str | pathlib.Path) -> FormalizationManifest:
    p = Path(path).expanduser().resolve()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError as ex:
        raise ManifestValidationError(f"manifest does not exist: {p}") from ex
    except yaml.YAMLError as ex:
        raise ManifestValidationError(f"invalid YAML in {p}: {ex}") from ex
    if not isinstance(data, dict):
        raise ManifestValidationError(f"manifest root must be a mapping: {p}")
    return FormalizationManifest(p, data)

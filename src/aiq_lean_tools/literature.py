"""Machine-checked literature/source inventory for formalization projects.

The document tracks bibliographic works separately from theorem-level censuses.
Enum vocabularies, group ordering, reconstruction statuses, and per-work source
markers are data in the manifest rather than package constants.
"""
from __future__ import annotations

import collections
import html
import importlib.resources as resources
import json
import pathlib
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Sequence
from urllib.parse import urlparse

import yaml

from .common import Finding, Path, atomic_write_json, atomic_write_text, clone_json, dotted_delete, dotted_set, infer_artifact_root
from .errors import ValidationError

CORE_WORK_FIELDS = (
    "title", "authors", "year", "kind", "group", "priority", "role",
    "formalization_status", "distilled_status", "bibliographic_status",
    "primary_url", "target_note", "repo_evidence", "existing_assets", "scope",
    "missing_work",
)
DEFAULT_KINDS = ("paper", "book", "monograph", "thesis", "report", "web")


def _string_list(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValidationError(f"{name} must be a list of strings")
    out = tuple(str(x).strip() for x in value if isinstance(x, str) and x.strip())
    if len(out) != len(value):
        raise ValidationError(f"{name} must contain only non-empty strings")
    return out


@dataclass
class LiteratureDocument:
    path: Path
    root: Path
    data: dict[str, Any]

    @property
    def title(self) -> str:
        return str(self.data.get("title") or "Formalization literature index")

    @property
    def works(self) -> dict[str, dict[str, Any]]:
        raw = self.data.get("works", {})
        return raw if isinstance(raw, dict) else {}

    @property
    def role_legend(self) -> Mapping[str, str]:
        raw = self.data.get("role_legend", {})
        return raw if isinstance(raw, Mapping) else {}

    @property
    def status_legend(self) -> Mapping[str, str]:
        raw = self.data.get("status_legend", {})
        return raw if isinstance(raw, Mapping) else {}

    @property
    def bibliographic_status_legend(self) -> Mapping[str, str]:
        raw = self.data.get("bibliographic_status_legend", {})
        return raw if isinstance(raw, Mapping) else {}

    def _policy(self) -> Mapping[str, Any]:
        raw = self.data.get("policy", {})
        return raw if isinstance(raw, Mapping) else {}

    def group_order(self) -> tuple[str, ...]:
        configured = self.data.get("group_order", self._policy().get("groups", []))
        values = _string_list(configured, "group_order") if configured else ()
        if values:
            return values
        return tuple(dict.fromkeys(str(work.get("group", "")) for work in self.works.values() if work.get("group")))

    def priority_order(self) -> tuple[str, ...]:
        configured = self.data.get("priority_order", self._policy().get("priorities", []))
        values = _string_list(configured, "priority_order") if configured else ()
        if values:
            return values
        return tuple(dict.fromkeys(str(work.get("priority", "")) for work in self.works.values() if work.get("priority")))

    def validate(self) -> list[Finding]:
        findings: list[Finding] = []
        if not isinstance(self.data.get("version"), (str, int, float)):
            findings.append(Finding("error", "literature-version", "literature document requires version"))
        if not isinstance(self.data.get("works"), Mapping) or not self.works:
            findings.append(Finding("error", "literature-works", "works must be a non-empty mapping"))
            return findings
        policy = self._policy()
        required_fields = _string_list(policy.get("required_work_fields", CORE_WORK_FIELDS), "policy.required_work_fields")
        kinds = set(_string_list(policy.get("kinds", DEFAULT_KINDS), "policy.kinds"))
        groups = set(self.group_order())
        priorities = set(self.priority_order())
        roles = set(self.role_legend)
        statuses = set(self.status_legend)
        bib_statuses = set(self.bibliographic_status_legend)
        target_notes: dict[str, str] = {}
        note_extension = str(policy.get("note_extension", ".tex")) if policy.get("note_extension", ".tex") is not None else ""

        reconstruction = self.data.get("reconstruction", {}) or {}
        if not isinstance(reconstruction, Mapping):
            findings.append(Finding("error", "literature-reconstruction", "reconstruction must be a mapping"))
            reconstruction = {}
        complete_statuses = set(_string_list(reconstruction.get("complete_statuses", ["complete"]), "reconstruction.complete_statuses"))
        # Historical manifests used ``complete`` as a terminal state even when
        # the legend only documented in-progress categories.  Treat configured
        # completion states as valid vocabulary in addition to legend keys.
        statuses.update(complete_statuses)
        note_root = self.path.parent / str(reconstruction.get("note_root", "."))
        common_markers = _string_list(reconstruction.get("required_markers", []), "reconstruction.required_markers")
        per_work = reconstruction.get("per_work_markers", {}) or {}
        if not isinstance(per_work, Mapping):
            findings.append(Finding("error", "literature-markers", "reconstruction.per_work_markers must be a mapping"))
            per_work = {}

        for key, work in self.works.items():
            loc = f"works.{key}"
            if not isinstance(key, str) or not key.strip():
                findings.append(Finding("error", "literature-key", "work key must be a non-empty string", loc))
                continue
            if not isinstance(work, Mapping):
                findings.append(Finding("error", "literature-work", "work entry must be a mapping", loc))
                continue
            missing = [field for field in required_fields if field not in work]
            if missing:
                findings.append(Finding("error", "literature-fields", f"missing required fields: {', '.join(missing)}", loc))
                continue
            if not isinstance(work.get("title"), str) or not work["title"].strip():
                findings.append(Finding("error", "literature-title", "title must be non-empty", loc))
            authors = work.get("authors")
            if not isinstance(authors, list) or not authors or not all(isinstance(x, str) and x.strip() for x in authors):
                findings.append(Finding("error", "literature-authors", "authors must be a non-empty list of strings", loc))
            year = work.get("year")
            if not isinstance(year, int) or not (1000 <= year <= 2200):
                findings.append(Finding("error", "literature-year", f"invalid year {year!r}", loc))
            if kinds and work.get("kind") not in kinds:
                findings.append(Finding("error", "literature-kind", f"unknown kind {work.get('kind')!r}", loc))
            if groups and work.get("group") not in groups:
                findings.append(Finding("error", "literature-group", f"unknown group {work.get('group')!r}", loc))
            if priorities and work.get("priority") not in priorities:
                findings.append(Finding("error", "literature-priority", f"unknown priority {work.get('priority')!r}", loc))
            if roles and work.get("role") not in roles:
                findings.append(Finding("error", "literature-role", f"unknown role {work.get('role')!r}", loc))
            if statuses and work.get("distilled_status") not in statuses:
                findings.append(Finding("error", "literature-status", f"unknown distilled_status {work.get('distilled_status')!r}", loc))
            if bib_statuses and work.get("bibliographic_status") not in bib_statuses:
                findings.append(Finding("error", "literature-bibliographic-status", f"unknown bibliographic_status {work.get('bibliographic_status')!r}", loc))
            url = work.get("primary_url")
            if url:
                if not isinstance(url, str):
                    findings.append(Finding("error", "literature-url", "primary_url must be a string or empty", loc))
                else:
                    parsed = urlparse(url)
                    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                        findings.append(Finding("error", "literature-url", f"invalid primary_url {url!r}", loc))
            note = work.get("target_note")
            if not isinstance(note, str) or not note.strip():
                findings.append(Finding("error", "literature-note", "target_note must be a non-empty basename", loc))
            elif "/" in note or "\\" in note:
                findings.append(Finding("error", "literature-note", "target_note must be a basename", loc))
            elif note_extension and not note.endswith(note_extension):
                findings.append(Finding("error", "literature-note", f"target_note must end with {note_extension}", loc))
            elif note in target_notes:
                findings.append(Finding("error", "literature-note-duplicate", f"target_note duplicates {target_notes[note]}: {note}", loc))
            else:
                target_notes[note] = key
            for field in ("repo_evidence", "existing_assets"):
                paths = work.get(field)
                if not isinstance(paths, list) or not all(isinstance(x, str) for x in paths):
                    findings.append(Finding("error", "literature-path-list", f"{field} must be a list of paths", loc))
                    continue
                for rel in paths:
                    if not (self.root / rel).exists():
                        findings.append(Finding("error", "literature-path", f"{field} path does not exist: {rel}", loc))
            for field in ("formalization_status", "scope", "missing_work"):
                if not isinstance(work.get(field), str) or not work[field].strip():
                    findings.append(Finding("error", "literature-text", f"{field} must be non-empty", loc))
            if work.get("distilled_status") in complete_statuses and isinstance(note, str):
                note_path = note_root / note
                if not note_path.is_file():
                    findings.append(Finding("error", "literature-complete-note", f"complete literature note is missing: {note_path}", loc))
                else:
                    text = note_path.read_text(encoding="utf-8", errors="replace")
                    markers = list(common_markers)
                    extra = per_work.get(key, [])
                    try:
                        markers.extend(_string_list(extra, f"reconstruction.per_work_markers.{key}"))
                    except ValidationError as ex:
                        findings.append(Finding("error", "literature-markers", str(ex), loc))
                    for marker in markers:
                        if marker not in text:
                            findings.append(Finding("error", "literature-note-marker", f"complete note missing marker {marker!r}", loc))
        return findings

    def summary(self) -> dict[str, Any]:
        works = list(self.works.values())
        counter = lambda field: dict(sorted(collections.Counter(str(w.get(field, "")) for w in works if w.get(field)).items()))
        return {
            "title": self.title,
            "works": len(works),
            "kinds": counter("kind"),
            "groups": counter("group"),
            "priorities": counter("priority"),
            "roles": counter("role"),
            "distilled_status": counter("distilled_status"),
            "bibliographic_status": counter("bibliographic_status"),
        }

    def row(self, key: str) -> dict[str, Any]:
        try:
            return self.works[key]
        except KeyError as ex:
            raise ValidationError(f"literature work does not exist: {key}") from ex

    def patch_work(self, key: str, sets: Sequence[tuple[str, Any]], deletes: Sequence[str]) -> None:
        row = self.row(key)
        for field, value in sets:
            dotted_set(row, field, value)
        for field in deletes:
            dotted_delete(row, field)

    def append_work(self, key: str, row: Mapping[str, Any]) -> None:
        if key in self.works:
            raise ValidationError(f"literature work already exists: {key}")
        self.works[key] = clone_json(dict(row))

    def write(self) -> None:
        if self.path.suffix.lower() in {".yaml", ".yml"}:
            atomic_write_text(self.path, yaml.safe_dump(self.data, sort_keys=False, allow_unicode=True))
        else:
            atomic_write_json(self.path, self.data)

    def render_markdown(self) -> str:
        summary = self.summary()
        lines = [f"# {self.title}", ""]
        purpose = self.data.get("purpose")
        if isinstance(purpose, str) and purpose.strip():
            lines += [purpose.strip(), ""]
        lines += ["## Inventory summary", "", f"- Works: **{summary['works']}**", ""]
        for label in ("distilled_status", "bibliographic_status", "priorities"):
            values = summary[label]
            if values:
                lines.append(f"- {label.replace('_', ' ')}: " + ", ".join(f"`{k}`={v}" for k, v in values.items()))
        lines.append("")
        grouped: dict[str, list[tuple[str, dict[str, Any]]]] = collections.defaultdict(list)
        for key, work in self.works.items():
            grouped[str(work.get("group", "Ungrouped"))].append((key, work))
        order = list(self.group_order())
        for group in grouped:
            if group not in order:
                order.append(group)
        for group in order:
            items = grouped.get(group, [])
            if not items:
                continue
            items.sort(key=lambda item: (item[1].get("year", 0), item[0]))
            lines += [f"## {group}", "", "| Key | Year | Work | Role | Formalization status | Literature asset | Priority |", "| --- | ---: | --- | --- | --- | --- | --- |"]
            for key, work in items:
                esc = lambda x: str(x).replace("|", "\\|").replace("\n", " ")
                lines.append(
                    f"| `{esc(key)}` | {esc(work.get('year', ''))} | {esc(work.get('title', ''))} | "
                    f"{esc(work.get('role', ''))} | {esc(work.get('formalization_status', ''))} | "
                    f"`{esc(work.get('distilled_status', ''))}` | {esc(work.get('priority', ''))} |"
                )
            lines.append("")
        pending = [(key, work) for key, work in self.works.items() if work.get("missing_work")]
        if pending:
            lines += ["## Recorded follow-up", ""]
            for key, work in pending:
                lines.append(f"- **`{key}` — {work.get('title', '')}**: {work.get('missing_work', '')}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def render_latex(self) -> str:
        def esc(text: Any) -> str:
            value = str(text)
            replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
            return "".join(replacements.get(ch, ch) for ch in value)
        lines = [
            r"\documentclass[10pt]{article}", r"\usepackage[margin=0.7in]{geometry}",
            r"\usepackage[T1]{fontenc}", r"\usepackage{longtable}", r"\usepackage{booktabs}",
            r"\usepackage[hidelinks]{hyperref}", r"\setlength{\parindent}{0pt}",
            r"\title{" + esc(self.title) + "}", r"\author{}", r"\date{Generated from the machine-readable literature manifest}",
            r"\begin{document}", r"\maketitle",
        ]
        purpose = self.data.get("purpose")
        if isinstance(purpose, str) and purpose.strip():
            lines += [r"\section*{Purpose}", esc(purpose)]
        grouped: dict[str, list[tuple[str, dict[str, Any]]]] = collections.defaultdict(list)
        for key, work in self.works.items():
            grouped[str(work.get("group", "Ungrouped"))].append((key, work))
        order = list(self.group_order())
        for group in grouped:
            if group not in order:
                order.append(group)
        for group in order:
            items = grouped.get(group, [])
            if not items:
                continue
            lines += [r"\section*{" + esc(group) + "}", r"\begin{longtable}{@{}p{0.18\textwidth}p{0.06\textwidth}p{0.34\textwidth}p{0.15\textwidth}p{0.13\textwidth}p{0.06\textwidth}@{}}", r"\toprule", r"Key & Year & Work & Role & Asset & Priority \\", r"\midrule"]
            for key, work in sorted(items, key=lambda item: (item[1].get("year", 0), item[0])):
                lines.append(
                    r"\texttt{" + esc(key) + "} & " + esc(work.get("year", "")) + " & " + esc(work.get("title", "")) + " & " + esc(work.get("role", "")) + " & " + esc(work.get("distilled_status", "")) + " & " + esc(work.get("priority", "")) + r" \\"
                )
            lines += [r"\bottomrule", r"\end{longtable}"]
        lines += [r"\end{document}", ""]
        return "\n".join(lines)

    def render_html(self) -> str:
        payload = {"summary": self.summary(), "data": self.data}
        template = resources.files("aiq_lean_tools").joinpath("assets/literature_viewer.html").read_text(encoding="utf-8")
        encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
        return template.replace("__TITLE__", html.escape(self.title)).replace("__PAYLOAD__", encoded)


def load_literature(path: str | pathlib.Path, *, root: str | pathlib.Path | None = None) -> LiteratureDocument:
    p = Path(path).expanduser().resolve()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as ex:
        raise ValidationError(f"literature manifest does not exist: {p}") from ex
    try:
        data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as ex:
        raise ValidationError(f"invalid literature manifest {p}: {ex}") from ex
    if not isinstance(data, dict):
        raise ValidationError("literature manifest root must be a mapping")
    base = Path(root).expanduser().resolve() if root else infer_artifact_root(p)
    return LiteratureDocument(p, base, data)

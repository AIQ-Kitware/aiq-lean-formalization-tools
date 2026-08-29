"""Python-only Lean source inventory.

This layer deliberately stops short of pretending source parsing is semantic.
It is useful for import/admission/docstring/name audits and for finding modules
that should be imported by a compiler-backed probe.  ``leanq`` remains the
elaborator-backed declaration/dependency authority.
"""
from __future__ import annotations

import collections
import pathlib
import re
from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping, Sequence

from .common import Path, find_workspace_root

DECL_KINDS = (
    "theorem", "lemma", "def", "abbrev", "structure", "class", "inductive",
    "axiom", "instance", "opaque", "alias",
)
DECL_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)"
    r"(?:(?:@\[[^\n]*\]\s*)?)*"
    r"(?:(?P<private>private)\s+)?"
    r"(?:(?:protected|noncomputable|unsafe|partial|scoped|local|public)\s+)*"
    r"(?P<kind>" + "|".join(DECL_KINDS) + r")\s+"
    r"(?P<name>`[^`]+`|[A-Za-z_][A-Za-z0-9_'.₀-₉⁰-⁹′!?]*)"
)
NAMESPACE_RE = re.compile(r"(?m)^\s*namespace\s+([A-Za-z0-9_'.₀-₉⁰-⁹′]+)\s*(?:--.*)?$")
SECTION_RE = re.compile(r"(?m)^\s*(?:(?:noncomputable|private|public)\s+)*section(?:\s+([A-Za-z0-9_'.₀-₉⁰-⁹′]+))?\s*(?:--.*)?$")
END_RE = re.compile(r"(?m)^\s*end(?:\s+([A-Za-z0-9_'.₀-₉⁰-⁹′]+))?\s*(?:--.*)?$")
IMPORT_RE = re.compile(r"(?m)^\s*import\s+([A-Za-z0-9_'.₀-₉⁰-⁹′]+)\s*(?:--.*)?$")
ADMISSION_RE = re.compile(r"(?<![A-Za-z0-9_.'])(?:sorry|admit)(?![A-Za-z0-9_.'])")
DOCSTRING_END_RE = re.compile(r"-/\s*$")


@dataclass(frozen=True)
class SourceDecl:
    name: str
    short_name: str
    kind: str
    module: str
    path: Path
    line: int
    private: bool
    documented: bool

    def to_json(self, root: Path | None = None) -> dict:
        path = self.path
        if root is not None:
            try:
                path = path.relative_to(root)
            except ValueError:
                pass
        return {
            "name": self.name,
            "short_name": self.short_name,
            "kind": self.kind,
            "module": self.module,
            "path": path.as_posix(),
            "line": self.line,
            "private": self.private,
            "documented": self.documented,
        }


@dataclass
class LeanSourceIndex:
    root: Path
    declarations: list[SourceDecl]
    imports: dict[str, set[str]]
    modules: dict[str, Path]
    admitted_modules: set[str]

    @property
    def by_name(self) -> dict[str, list[SourceDecl]]:
        out: dict[str, list[SourceDecl]] = collections.defaultdict(list)
        for decl in self.declarations:
            out[decl.name].append(decl)
        return dict(out)

    @property
    def by_short_name(self) -> dict[str, list[SourceDecl]]:
        out: dict[str, list[SourceDecl]] = collections.defaultdict(list)
        for decl in self.declarations:
            out[decl.short_name].append(decl)
        return dict(out)

    def resolve(self, name: str) -> list[SourceDecl]:
        exact = self.by_name.get(name, [])
        if exact:
            return exact
        return self.by_short_name.get(name.rsplit(".", 1)[-1], [])

    def import_closure(self, roots: Iterable[str]) -> set[str]:
        seen: set[str] = set()
        stack = list(roots)
        while stack:
            module = stack.pop()
            if module in seen:
                continue
            seen.add(module)
            stack.extend(dep for dep in self.imports.get(module, ()) if dep in self.modules)
        return seen

    def admission_closure(self) -> dict[str, set[str]]:
        reverse: dict[str, set[str]] = collections.defaultdict(set)
        for module, deps in self.imports.items():
            for dep in deps:
                if dep in self.modules:
                    reverse[dep].add(module)
        tainted: dict[str, set[str]] = collections.defaultdict(set)
        for source in self.admitted_modules:
            queue = [source]
            seen = {source}
            while queue:
                current = queue.pop()
                tainted[current].add(source)
                for consumer in reverse[current]:
                    if consumer not in seen:
                        seen.add(consumer)
                        queue.append(consumer)
        return dict(tainted)

    def duplicate_public_names(self) -> dict[str, list[SourceDecl]]:
        return {
            name: rows
            for name, rows in self.by_name.items()
            if len({row.module for row in rows if not row.private}) > 1
        }

    def private_shadows_imported_public(self) -> list[dict]:
        """Find private declarations reusing a public name from their import closure.

        This is intentionally a source-level duplication signal.  It does not
        claim the declarations are semantically equivalent; it identifies the
        exact pattern that merits review.
        """
        public_by_module: dict[str, set[str]] = collections.defaultdict(set)
        private_by_module: dict[str, list[SourceDecl]] = collections.defaultdict(list)
        for decl in self.declarations:
            if decl.private:
                private_by_module[decl.module].append(decl)
            else:
                public_by_module[decl.module].add(decl.short_name)
        findings: list[dict] = []
        for module, private_rows in private_by_module.items():
            closure = self.import_closure(self.imports.get(module, ()))
            available: dict[str, list[str]] = collections.defaultdict(list)
            for imported in closure:
                for name in public_by_module.get(imported, ()):
                    available[name].append(imported)
            for decl in private_rows:
                providers = sorted(available.get(decl.short_name, ()))
                if providers:
                    findings.append({
                        "module": module,
                        "name": decl.short_name,
                        "declaration": decl,
                        "providers": providers,
                    })
        return sorted(findings, key=lambda row: (row["module"], row["name"]))


def strip_comments(text: str) -> str:
    """Strip nested Lean comments while preserving offsets and strings."""
    out: list[str] = []
    i = 0
    depth = 0
    in_string = False
    escaped = False
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if depth:
            if ch == "/" and nxt == "-":
                depth += 1
                out.extend("  ")
                i += 2
                continue
            if ch == "-" and nxt == "/":
                depth -= 1
                out.extend("  ")
                i += 2
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "-":
            depth = 1
            out.extend("  ")
            i += 2
            continue
        if ch == "-" and nxt == "-":
            out.extend("  ")
            i += 2
            while i < len(text) and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _module_name(root: Path, path: Path) -> str:
    return ".".join(path.relative_to(root).with_suffix("").parts)


def _namespace_events(clean: str) -> list[tuple[int, str, str | None]]:
    events: list[tuple[int, str, str | None]] = []
    for m in NAMESPACE_RE.finditer(clean):
        events.append((m.start(), "namespace", m.group(1)))
    for m in SECTION_RE.finditer(clean):
        events.append((m.start(), "section", m.group(1)))
    for m in END_RE.finditer(clean):
        events.append((m.start(), "end", m.group(1)))
    return sorted(events)


def _namespace_at(events: Sequence[tuple[int, str, str | None]], offset: int) -> list[str]:
    stack: list[tuple[str, str | None]] = []
    for pos, kind, name in events:
        if pos >= offset:
            break
        if kind in {"namespace", "section"}:
            stack.append((kind, name))
            continue
        if not stack:
            continue
        if name is None:
            stack.pop()
            continue
        for idx in range(len(stack) - 1, -1, -1):
            if stack[idx][1] == name:
                del stack[idx:]
                break
    parts: list[str] = []
    for kind, name in stack:
        if kind == "namespace" and name:
            parts.extend(name.split("."))
    return parts


def _has_docstring(original: str, decl_offset: int) -> bool:
    prefix = original[:decl_offset]
    lines = prefix.splitlines()
    i = len(lines) - 1
    while i >= 0 and not lines[i].strip():
        i -= 1
    if i < 0 or not DOCSTRING_END_RE.search(lines[i]):
        return False
    depth = 0
    while i >= 0:
        line = lines[i]
        depth += line.count("-/") - line.count("/-")
        if depth <= 0:
            return line.lstrip().startswith("/--")
        i -= 1
    return False


def _scan_file(root: Path, path: Path) -> tuple[list[SourceDecl], set[str], bool]:
    original = path.read_text(encoding="utf-8", errors="replace")
    clean = strip_comments(original)
    module = _module_name(root, path)
    events = _namespace_events(clean)
    rows: list[SourceDecl] = []
    for match in DECL_RE.finditer(clean):
        raw_name = match.group("name").strip("`")
        ns = _namespace_at(events, match.start())
        if raw_name.startswith("_root_."):
            full = raw_name[len("_root_."):]
        elif "." in raw_name and raw_name.split(".", 1)[0] in ns:
            full = raw_name
        else:
            full = ".".join([*ns, raw_name]) if ns else raw_name
        rows.append(SourceDecl(
            name=full,
            short_name=raw_name.rsplit(".", 1)[-1],
            kind=match.group("kind"),
            module=module,
            path=path,
            line=_line_of(clean, match.start()),
            private=bool(match.group("private")),
            documented=_has_docstring(original, match.start()),
        ))
    imports = set(IMPORT_RE.findall(clean))
    admitted = bool(ADMISSION_RE.search(clean))
    return rows, imports, admitted


def lean_files(root: Path, *, exclude_dirs: Iterable[str] = (".git", ".lake", "build", "vendor", "external")) -> Iterator[Path]:
    excluded = set(exclude_dirs)
    for path in root.rglob("*.lean"):
        rel = path.relative_to(root)
        if excluded.intersection(rel.parts):
            continue
        yield path


def scan_lean_project(root: str | pathlib.Path | None = None, *, exclude_dirs: Iterable[str] = (".git", ".lake", "build", "vendor", "external")) -> LeanSourceIndex:
    base = find_workspace_root(root)
    declarations: list[SourceDecl] = []
    imports: dict[str, set[str]] = {}
    modules: dict[str, Path] = {}
    admitted: set[str] = set()
    for path in sorted(lean_files(base, exclude_dirs=exclude_dirs)):
        rows, deps, has_admission = _scan_file(base, path)
        module = _module_name(base, path)
        modules[module] = path
        imports[module] = deps
        declarations.extend(rows)
        if has_admission:
            admitted.add(module)
    return LeanSourceIndex(base, declarations, imports, modules, admitted)

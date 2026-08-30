"""Python-only Lean source inventory.

This layer deliberately stops short of pretending source parsing is semantic.
It is useful for import/admission/docstring/name audits and for finding modules
that should be imported by a compiler-backed probe.  ``leanq`` remains the
elaborator-backed declaration/dependency authority.
"""
from __future__ import annotations

import collections
import functools
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Sequence

import yaml

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
# `instance : Foo Bar := ...` is legal and nameless, so a name-keyed scan drops it
# entirely -- and a docstring gate that cannot see a declaration reports it as
# documented.  Matched separately because it cannot share the named-declaration
# regex: the whole difference is that no name follows the keyword.
ANONYMOUS_INSTANCE_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)"
    r"(?:(?:@\[[^\n]*\]\s*)?)*"
    r"(?:(?P<private>private)\s+)?"
    r"(?:(?:protected|noncomputable|unsafe|partial|scoped|local|public)\s+)*"
    r"(?P<kind>instance)\s*(?=[:(\[{])"
)
ANONYMOUS_NAME = "<anonymous>"
NAMESPACE_RE = re.compile(r"(?m)^\s*namespace\s+([A-Za-z0-9_'.₀-₉⁰-⁹′]+)\s*(?:--.*)?$")
SECTION_RE = re.compile(r"(?m)^\s*(?:(?:noncomputable|private|public)\s+)*section(?:\s+([A-Za-z0-9_'.₀-₉⁰-⁹′]+))?\s*(?:--.*)?$")
END_RE = re.compile(r"(?m)^\s*end(?:\s+([A-Za-z0-9_'.₀-₉⁰-⁹′]+))?\s*(?:--.*)?$")
# Lean 4 module-system imports may carry `public`, `private`, or `meta`
# modifiers.  Missing them silently drops real edges from every import-derived
# view (layer policy, admission closure, module plans, coverage), which reads as
# a clean architecture rather than as an unparsed line.
IMPORT_RE = re.compile(
    r"(?m)^\s*(?:(?:public|private|meta)\s+)*import\s+"
    r"([A-Za-z0-9_'.₀-₉⁰-⁹′]+)\s*(?:--.*)?$"
)
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
    anonymous: bool = False

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
            "anonymous": self.anonymous,
        }


@dataclass
class LeanSourceIndex:
    root: Path
    declarations: list[SourceDecl]
    imports: dict[str, set[str]]
    modules: dict[str, Path]
    admitted_modules: set[str]

    # `resolve` is called once per cited declaration, and a census can cite
    # thousands.  Rebuilding these tables per lookup made name resolution the
    # second-largest cost in a whole-repository scan.
    @functools.cached_property
    def named_declarations(self) -> list[SourceDecl]:
        return [decl for decl in self.declarations if not decl.anonymous]

    @functools.cached_property
    def by_name(self) -> dict[str, list[SourceDecl]]:
        out: dict[str, list[SourceDecl]] = collections.defaultdict(list)
        for decl in self.named_declarations:
            out[decl.name].append(decl)
        return dict(out)

    @functools.cached_property
    def by_short_name(self) -> dict[str, list[SourceDecl]]:
        out: dict[str, list[SourceDecl]] = collections.defaultdict(list)
        for decl in self.named_declarations:
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
        for decl in self.named_declarations:
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


_COMMENT_TOKEN_RE = re.compile(r'"|/-|-/|--')
_NON_NEWLINE_RE = re.compile(r"[^\n]")


def _blank(text: str) -> str:
    """Replace every character except a newline with a space."""
    return _NON_NEWLINE_RE.sub(" ", text)


def strip_comments(text: str) -> str:
    """Strip nested Lean comments while preserving offsets and strings.

    Offsets are preserved because every declaration/namespace offset computed on
    the stripped text is reported against the original file.  The scanner jumps
    between comment/string tokens rather than walking characters: this function
    runs over every Lean file in a repository for most audits, and the
    character loop it replaced dominated whole-tree scans.
    """
    out: list[str] = []
    pos = 0
    depth = 0
    size = len(text)
    while pos < size:
        match = _COMMENT_TOKEN_RE.search(text, pos)
        if match is None:
            rest = text[pos:]
            out.append(_blank(rest) if depth else rest)
            break
        start = match.start()
        token = match.group()
        chunk = text[pos:start]
        out.append(_blank(chunk) if depth else chunk)
        if depth:
            if token == "/-":
                depth += 1
            elif token == "-/":
                depth -= 1
            out.append(" " * len(token))
            pos = start + len(token)
            continue
        if token == '"':
            cursor = start + 1
            while cursor < size:
                char = text[cursor]
                if char == "\\":
                    cursor += 2
                    continue
                cursor += 1
                if char == '"':
                    break
            out.append(text[start:cursor])
            pos = cursor
            continue
        if token == "/-":
            depth = 1
            out.append("  ")
            pos = start + 2
            continue
        if token == "--":
            eol = text.find("\n", start)
            if eol == -1:
                eol = size
            out.append(" " * (eol - start))
            pos = eol
            continue
        # A bare `-/` outside any comment is ordinary source text.
        out.append(token)
        pos = start + len(token)
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
    for match in ANONYMOUS_INSTANCE_RE.finditer(clean):
        rows.append(SourceDecl(
            name=ANONYMOUS_NAME,
            short_name=ANONYMOUS_NAME,
            kind="instance",
            module=module,
            path=path,
            line=_line_of(clean, match.start()),
            private=bool(match.group("private")),
            documented=_has_docstring(original, match.start()),
            anonymous=True,
        ))
    rows.sort(key=lambda decl: decl.line)
    imports = set(IMPORT_RE.findall(clean))
    admitted = bool(ADMISSION_RE.search(clean))
    return rows, imports, admitted


DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = (".git", ".lake", "build", "vendor", "external")


@dataclass(frozen=True)
class SourceScope:
    """Which Lean files in a checkout are *this project's* source.

    A formalization checkout is rarely only its own libraries.  It also carries
    vendored donors, retired trees, submitted copies of itself, and reference
    checkouts of other repositories.  Scanning those as project source does not
    merely cost time: every duplicate-name, docstring, namespace, and import
    audit reports the extra trees as findings, so the answer is wrong rather
    than slow.

    The project owns this decision, so it is read from ``formalization.yaml``:

    .. code-block:: yaml

        source_scope:
          roots: ["MyLib", "MyPaper"]        # optional; default: whole checkout
          exclude_dirs: [".lake", "retired"] # directory *names*, at any depth
    """

    roots: tuple[str, ...] = ()
    exclude_dirs: tuple[str, ...] = DEFAULT_EXCLUDE_DIRS

    @classmethod
    def from_data(cls, data: Mapping[str, Any] | None) -> "SourceScope":
        if not isinstance(data, Mapping):
            return cls()
        roots = data.get("roots", [])
        excludes = data.get("exclude_dirs", list(DEFAULT_EXCLUDE_DIRS))
        if isinstance(roots, str):
            roots = [roots]
        if isinstance(excludes, str):
            excludes = [excludes]
        if not isinstance(roots, list) or not all(isinstance(x, str) for x in roots):
            raise ValueError("source_scope.roots must be a list of directory paths")
        if not isinstance(excludes, list) or not all(isinstance(x, str) for x in excludes):
            raise ValueError("source_scope.exclude_dirs must be a list of directory names")
        return cls(tuple(r.strip("/") for r in roots if r.strip("/")), tuple(excludes))

    @classmethod
    def load(cls, root: str | pathlib.Path) -> "SourceScope":
        manifest = Path(root).expanduser().resolve() / "formalization.yaml"
        if not manifest.is_file():
            return cls()
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            return cls()
        return cls.from_data(data.get("source_scope"))

    def includes(self, relative: pathlib.PurePath) -> bool:
        if set(self.exclude_dirs).intersection(relative.parts):
            return False
        if not self.roots:
            return True
        posix = relative.as_posix()
        return any(posix == r or posix.startswith(r + "/") for r in self.roots)


def _resolve_scope(
    root: Path,
    scope: SourceScope | None,
    exclude_dirs: Iterable[str] | None,
) -> SourceScope:
    if scope is not None:
        return scope
    if exclude_dirs is not None:
        return SourceScope(exclude_dirs=tuple(exclude_dirs))
    return SourceScope.load(root)


def lean_files(
    root: Path,
    *,
    exclude_dirs: Iterable[str] | None = None,
    scope: SourceScope | None = None,
) -> Iterator[Path]:
    resolved = _resolve_scope(root, scope, exclude_dirs)
    for path in root.rglob("*.lean"):
        if resolved.includes(path.relative_to(root)):
            yield path


def scan_lean_project(
    root: str | pathlib.Path | None = None,
    *,
    exclude_dirs: Iterable[str] | None = None,
    scope: SourceScope | None = None,
) -> LeanSourceIndex:
    base = find_workspace_root(root)
    resolved = _resolve_scope(base, scope, exclude_dirs)
    declarations: list[SourceDecl] = []
    imports: dict[str, set[str]] = {}
    modules: dict[str, Path] = {}
    admitted: set[str] = set()
    for path in sorted(lean_files(base, scope=resolved)):
        rows, deps, has_admission = _scan_file(base, path)
        module = _module_name(base, path)
        modules[module] = path
        imports[module] = deps
        declarations.extend(rows)
        if has_admission:
            admitted.add(module)
    return LeanSourceIndex(base, declarations, imports, modules, admitted)


@dataclass(frozen=True)
class SourceDeclarationText:
    """Human-written Lean declaration header plus relevant ambient binders."""

    declaration: SourceDecl
    header: str
    ambient: str

    def to_json(self, root: Path | None = None) -> dict:
        data = self.declaration.to_json(root)
        data.update({"header": self.header, "ambient": self.ambient})
        return data

    def render(self) -> str:
        parts = []
        if self.ambient.strip():
            parts.append(self.ambient.rstrip())
        parts.append(self.header.rstrip())
        return "\n".join(parts).rstrip()


def _source_header(lines: list[str], start: int) -> str:
    """Extract a declaration header and stop before its top-level body."""
    out: list[str] = []
    paren = bracket = brace = 0
    for line in lines[start:]:
        cut: int | None = None
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "(":
                paren += 1
            elif ch == ")":
                paren = max(0, paren - 1)
            elif ch == "[":
                bracket += 1
            elif ch == "]":
                bracket = max(0, bracket - 1)
            elif ch == "{":
                brace += 1
            elif ch == "}":
                brace = max(0, brace - 1)
            if paren == bracket == brace == 0:
                if line.startswith(":=", i):
                    cut = i
                    break
                if line.startswith("where", i) and (i == 0 or line[i - 1].isspace()):
                    after = i + len("where")
                    if after == len(line) or line[after].isspace():
                        cut = i
                        break
            i += 1
        if cut is not None:
            prefix = line[:cut].rstrip()
            if prefix:
                out.append(prefix)
            break
        out.append(line.rstrip())
    return "\n".join(out).rstrip()


def _variable_blocks_before(lines: list[str], decl_line: int) -> list[str]:
    blocks: list[str] = []
    i = 0
    while i < decl_line:
        if re.match(r"^\s*variable\b", lines[i]):
            block = [lines[i].rstrip()]
            i += 1
            while i < decl_line:
                stripped = lines[i].strip()
                if not stripped:
                    break
                if re.match(
                    r"^(?:variable|theorem|lemma|def|abbrev|structure|class|inductive|axiom|instance|opaque|alias|section|end|namespace|/-|/--)",
                    stripped,
                ):
                    break
                if lines[i][:1].isspace():
                    block.append(lines[i].rstrip())
                    i += 1
                    continue
                break
            blocks.append("\n".join(block))
            continue
        i += 1
    return blocks


def _binder_names(block: str) -> set[str]:
    names: set[str] = set()
    for group in re.findall(r"[\{\(]\s*([^:}\)]+?)\s*:(?!=)", block):
        for name in group.split():
            if re.match(r"^[A-Za-z_𝕜ℝℂ][\w₀-₉𝕜ℝℂ]*$", name):
                names.add(name)
    return names


def _ambient_variables(lines: list[str], decl_line: int, declaration: str) -> str:
    used = set(re.findall(r"\b[A-Za-z_][A-Za-z_0-9₀-₉]*\b|[𝕜ℝℂ]", declaration))
    explicitly_bound = _binder_names(declaration)
    missing = used - explicitly_bound
    chosen: list[str] = []
    covered: set[str] = set()
    for block in reversed(_variable_blocks_before(lines, decl_line)):
        names = _binder_names(block)
        relevant = (names & missing) - covered
        if relevant:
            chosen.append(block)
            covered |= names
    chosen.reverse()
    return "\n".join(chosen).rstrip()


def declaration_source_texts(index: LeanSourceIndex, name: str) -> list[SourceDeclarationText]:
    """Return source headers for exact or short-name declaration matches.

    Exact qualified-name matches are preferred.  When only a short name is
    available, every source-level candidate is returned so callers can present
    the ambiguity rather than selecting a project-specific path by convention.
    """
    rows = index.by_name.get(name)
    if not rows:
        rows = index.resolve(name)
    out: list[SourceDeclarationText] = []
    for row in rows:
        lines = row.path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, row.line - 1)
        header = _source_header(lines, start)
        ambient = _ambient_variables(lines, start, header)
        out.append(SourceDeclarationText(row, header=header, ambient=ambient))
    return out

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
# Horizontal whitespace only.  `\s` matches a newline, so `end` on one line and
# `section` on the next parse as a single `end section`, which pops the wrong
# entry and silently mis-qualifies every declaration in the rest of the file.
_H = r"[ \t]"
_NAME = r"[A-Za-z0-9_'.₀-₉⁰-⁹′]+"
NAMESPACE_RE = re.compile(rf"(?m)^{_H}*namespace{_H}+({_NAME}){_H}*(?:--.*)?$")
SECTION_RE = re.compile(
    rf"(?m)^{_H}*(?:(?:@\[[^\]\n]*\]|noncomputable|private|public){_H}+)*"
    rf"section(?:{_H}+({_NAME}))?{_H}*(?:--.*)?$"
)
END_RE = re.compile(rf"(?m)^{_H}*end(?:{_H}+({_NAME}))?{_H}*(?:--.*)?$")
# Lean 4 module-system imports may carry `public`, `private`, or `meta`
# modifiers.  Missing them silently drops real edges from every import-derived
# view (layer policy, admission closure, module plans, coverage), which reads as
# a clean architecture rather than as an unparsed line.
IMPORT_RE = re.compile(
    rf"(?m)^{_H}*(?:(?:public|private|meta){_H}+)*import{_H}+"
    rf"({_NAME}){_H}*(?:--.*)?$"
)
ADMISSION_RE = re.compile(r"(?<![A-Za-z0-9_.'])(?:sorry|admit)(?![A-Za-z0-9_.'])")
DOCSTRING_END_RE = re.compile(r"-/\s*$")
# Lines Lean allows between a docstring and the declaration it documents.
INTERSTITIAL_RE = re.compile(
    r"^\s*(?:@\[|attribute\b|omit\b|set_option\b|open\b|variable\b|local\b|--|$)"
)


def module_in_scope(
    module: str,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> bool:
    """Does ``module`` fall under an included prefix and no excluded one?"""
    def under(prefix: str) -> bool:
        return module == prefix or module.startswith(prefix + ".")

    if include and not any(under(prefix) for prefix in include):
        return False
    return not any(under(prefix) for prefix in exclude)


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

    def restricted(
        self,
        *,
        include: Sequence[str] = (),
        exclude: Sequence[str] = (),
    ) -> "LeanSourceIndex":
        """A view of this index limited to modules under the given prefixes.

        Whole-tree scanning is right for imports and admissions, and wrong for
        checks whose question is scoped to part of the architecture -- a
        conformance or challenge library restates library statements on purpose,
        so a repository-wide duplicate-name check reports its whole point as a
        defect.
        """
        if not include and not exclude:
            return self
        keep = {module for module in self.modules if module_in_scope(module, include, exclude)}
        return LeanSourceIndex(
            root=self.root,
            declarations=[row for row in self.declarations if row.module in keep],
            imports={mod: deps for mod, deps in self.imports.items() if mod in keep},
            modules={mod: path for mod, path in self.modules.items() if mod in keep},
            admitted_modules={mod for mod in self.admitted_modules if mod in keep},
        )

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
    """Is the declaration at ``decl_offset`` preceded by its own ``/-- ... -/``?

    Only blank lines, attributes, and the handful of commands Lean allows between
    a docstring and the declaration it documents may intervene.  Stopping at the
    first non-blank line instead reports a documented declaration as undocumented
    whenever it carries an attribute or an explanatory ``--`` line, which in one
    repository was 237 of its declarations.
    """
    prefix = original[:decl_offset]
    lines = prefix.splitlines()
    i = len(lines) - 1
    while i >= 0 and INTERSTITIAL_RE.match(lines[i]) and not lines[i].rstrip().endswith("-/"):
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


def _scan_file(root: Path, path: Path, *, module: str | None = None) -> tuple[list[SourceDecl], set[str], bool]:
    original = path.read_text(encoding="utf-8", errors="replace")
    clean = strip_comments(original)
    if module is None:
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
class SourceRoot:
    """A directory of project Lean source, and the module prefix it carries.

    Lake libraries may set ``srcDir``, in which case a module's name is its path
    relative to that directory rather than to the checkout.  Deriving module
    names from the checkout instead silently renames every module in such a
    library, and then none of its imports resolve against the index.
    """

    path: str
    module_root: str = ""

    @classmethod
    def parse(cls, entry: Any) -> "SourceRoot":
        if isinstance(entry, str):
            cleaned = entry.strip("/")
            return cls(cleaned, cleaned.replace("/", "."))
        if isinstance(entry, Mapping):
            path = str(entry.get("path", "")).strip("/")
            if not path:
                raise ValueError("a source_scope root object requires a 'path'")
            module_root = entry.get("module_root")
            if module_root is None:
                module_root = path.replace("/", ".")
            return cls(path, str(module_root).strip("."))
        raise ValueError("source_scope.roots entries must be strings or objects with a 'path'")

    def contains(self, relative: pathlib.PurePath) -> bool:
        posix = relative.as_posix()
        # A Lake library `Foo` is `Foo.lean` *and* `Foo/**`; the root module sits
        # beside the directory, not inside it, and omitting it drops the module
        # every consumer of the library actually imports.
        return posix in {self.path, self.path + ".lean"} or posix.startswith(self.path + "/")

    def module_name(self, relative: pathlib.PurePath) -> str:
        posix = relative.as_posix()
        if posix == self.path + ".lean":
            return self.module_root or pathlib.PurePosixPath(self.path).name
        inner = relative.relative_to(self.path).with_suffix("").parts
        return ".".join(([self.module_root] if self.module_root else []) + list(inner))


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
          exclude_dirs: [".lake", "retired"]  # directory *names*, at any depth
          roots:                              # optional; default: whole checkout
            - "MyLib"
            - path: "MyPaper/MyPaper"         # a Lake `srcDir` library
              module_root: "MyPaper"
    """

    roots: tuple[SourceRoot, ...] = ()
    exclude_dirs: tuple[str, ...] = DEFAULT_EXCLUDE_DIRS

    @classmethod
    def from_data(cls, data: Mapping[str, Any] | None) -> "SourceScope":
        if not isinstance(data, Mapping):
            return cls()
        roots = data.get("roots", [])
        excludes = data.get("exclude_dirs", list(DEFAULT_EXCLUDE_DIRS))
        if isinstance(roots, (str, Mapping)):
            roots = [roots]
        if isinstance(excludes, str):
            excludes = [excludes]
        if not isinstance(roots, list):
            raise ValueError("source_scope.roots must be a list")
        if not isinstance(excludes, list) or not all(isinstance(x, str) for x in excludes):
            raise ValueError("source_scope.exclude_dirs must be a list of directory names")
        parsed = tuple(SourceRoot.parse(entry) for entry in roots)
        seen = [root.path for root in parsed]
        if len(seen) != len(set(seen)):
            raise ValueError("source_scope.roots contains a duplicate path")
        return cls(parsed, tuple(excludes))

    @classmethod
    def load(cls, root: str | pathlib.Path) -> "SourceScope":
        manifest = Path(root).expanduser().resolve() / "formalization.yaml"
        if not manifest.is_file():
            return cls()
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            return cls()
        return cls.from_data(data.get("source_scope"))

    @property
    def root_paths(self) -> tuple[str, ...]:
        return tuple(root.path for root in self.roots)

    def root_for(self, relative: pathlib.PurePath) -> SourceRoot | None:
        # Longest path first, so a nested root wins over the tree containing it.
        for root in sorted(self.roots, key=lambda r: len(r.path), reverse=True):
            if root.contains(relative):
                return root
        return None

    def includes(self, relative: pathlib.PurePath) -> bool:
        if set(self.exclude_dirs).intersection(relative.parts):
            return False
        return not self.roots or self.root_for(relative) is not None

    def module_name(self, relative: pathlib.PurePath) -> str:
        root = self.root_for(relative)
        if root is not None:
            return root.module_name(relative)
        return ".".join(relative.with_suffix("").parts)

    def source_paths(self, module: str) -> list[pathlib.PurePath]:
        """Where a module of this name could have its source, newest scope first.

        Build products are named by module, so recovering the source path from an
        artifact needs the same `srcDir` mapping the forward direction uses.
        """
        parts = module.split(".")
        out: list[pathlib.PurePath] = []
        for root in self.roots:
            prefix = root.module_root.split(".") if root.module_root else []
            if parts[:len(prefix)] != prefix:
                continue
            inner = parts[len(prefix):]
            if not inner:
                # The library root module, which lives beside the directory.
                out.append(pathlib.PurePosixPath(root.path + ".lean"))
                continue
            out.append(pathlib.PurePosixPath(root.path).joinpath(*inner).with_suffix(".lean"))
        if not self.roots:
            out.append(pathlib.PurePosixPath(*parts).with_suffix(".lean"))
        return out

    def library_names(self, base: str | pathlib.Path) -> frozenset[str]:
        """Top-level module names this project's roots can produce.

        A root with a `module_root` contributes its first component.  A bare
        Lake ``srcDir`` root has no prefix -- module names are relative to the
        directory -- so its top-level names are read from the directory itself.
        Without this a reverse lookup from a build artifact cannot tell a
        project module from a dependency's.
        """
        if not self.roots:
            return frozenset()
        root_path = Path(base).expanduser().resolve()
        names: set[str] = set()
        for root in self.roots:
            if root.module_root:
                names.add(root.module_root.split(".")[0])
                continue
            directory = root_path / root.path
            if not directory.is_dir():
                continue
            for entry in directory.iterdir():
                if entry.is_file() and entry.suffix == ".lean":
                    names.add(entry.stem)
                elif entry.is_dir() and any(entry.rglob("*.lean")):
                    names.add(entry.name)
        return frozenset(names)


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
        module = resolved.module_name(path.relative_to(base))
        rows, deps, has_admission = _scan_file(base, path, module=module)
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


def _split_declaration_header(lines: list[str], start: int) -> tuple[str, str, str]:
    """Split a declaration into its header, the token opening its body, and the body.

    Returns ``(header, delimiter, body)``.  The delimiter is ``":="``,
    ``"where"``, or ``""`` when no top-level body opener was found within the
    scanned lines.  The body is returned only far enough to see how it starts --
    ``by`` or a term -- which is what a caller replacing it with a placeholder
    needs to know.
    """
    out: list[str] = []
    paren = bracket = brace = 0
    for n, line in enumerate(lines[start:], start):
        cut: int | None = None
        delimiter = ""
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
                    cut, delimiter = i, ":="
                    break
                if line.startswith("where", i) and (i == 0 or line[i - 1].isspace()):
                    after = i + len("where")
                    if after == len(line) or line[after].isspace():
                        cut, delimiter = i, "where"
                        break
            i += 1
        if cut is not None:
            prefix = line[:cut].rstrip()
            if prefix:
                out.append(prefix)
            body = "\n".join([line[cut + len(delimiter):]] + lines[n + 1 : n + 4])
            return "\n".join(out).rstrip(), delimiter, body.strip()
        out.append(line.rstrip())
    return "\n".join(out).rstrip(), "", ""


def _source_header(lines: list[str], start: int) -> str:
    """Extract a declaration header and stop before its top-level body."""
    return _split_declaration_header(lines, start)[0]


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


def binder_names(block: str) -> set[str]:
    """Names a declaration's own binder groups introduce.

    A binder is not vocabulary: ``A₀`` in ``(A₀ : E →L[𝕜] E)`` names a local
    hypothesis, and looking it up in a global index finds an unrelated
    projection with the same short name.
    """
    names: set[str] = set()
    for group in re.findall(r"[\{\(]\s*([^:}\)]+?)\s*:(?!=)", block):
        for name in group.split():
            # Any letter may start a Lean name. Spelling the class out as ASCII
            # plus a few favourites rejected every binder named with a Greek
            # capital: `Λ₁` was read as vocabulary and looked up globally,
            # while `hΛ₁` beside it was not.
            if re.match(r"^[^\W\d][\w₀-₉'!?]*$", name):
                names.add(name)
    return names


def _ambient_variables(lines: list[str], decl_line: int, declaration: str) -> str:
    used = set(re.findall(r"\b[A-Za-z_][A-Za-z_0-9₀-₉]*\b|[𝕜ℝℂ]", declaration))
    explicitly_bound = binder_names(declaration)
    missing = used - explicitly_bound
    chosen: list[str] = []
    covered: set[str] = set()
    for block in reversed(_variable_blocks_before(lines, decl_line)):
        names = binder_names(block)
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


#: Tokens that open a new top-level declaration, used to find where one ends.
_DECL_STARTS = (
    "theorem ", "lemma ", "def ", "abbrev ", "instance ", "structure ", "class ",
    "inductive ", "example ", "noncomputable def ", "private theorem ", "private def ",
    "protected theorem ", "protected def ", "@[", "namespace ", "end ", "section ",
    "variable ", "open ", "import ", "/-- ",
)


@functools.lru_cache(maxsize=96)
def _cached_lines(path: Path, stamp: tuple[int, int]) -> tuple[str, ...]:
    """A Lean file's lines, keyed on its stat so an edit invalidates them.

    Reading one declaration means reading its whole file, and a page that shows
    two hundred declarations was reading the same handful of large files two
    hundred times -- fifteen seconds of it.
    """
    del stamp  # part of the key, not the work
    return tuple(path.read_text(encoding="utf-8", errors="replace").splitlines())


def _file_lines(path: Path) -> list[str]:
    try:
        st = path.stat()
    except OSError:
        return []
    return list(_cached_lines(path, (st.st_mtime_ns, st.st_size)))


def _declaration_block_start(lines: list[str], decl: int) -> int:
    """The first line of the declaration's own block: docstring and attributes.

    Walks back over attributes, ``omit ... in``, modifiers, and a docstring
    block, so a caller can present the prose that belongs to the declaration
    together with it.
    """
    start = decl
    while start > 0:
        prev = lines[start - 1].rstrip()
        stripped = prev.lstrip()
        if stripped.startswith("@[") or stripped.startswith("omit ") or stripped.startswith("private ") or stripped.startswith("protected "):
            start -= 1
        elif prev.endswith("-/"):
            start -= 1
            while start > 0 and not lines[start].lstrip().startswith(("/--", "/-")):
                start -= 1
        elif stripped.startswith("/--") and stripped.endswith("-/"):
            start -= 1
        else:
            break
    return start


#: Modifiers that may precede the declaration keyword on its own line.
_DECL_MODIFIERS = ("private", "protected", "noncomputable", "nonrec", "partial", "unsafe", "scoped", "local")

#: Declaration keywords whose body is a *proof*, and so is not shown.
_PROOF_KEYWORDS = ("theorem", "lemma", "example")

#: Every declaration keyword, so an unrecognized prefix is not mistaken for one.
_DECL_KEYWORDS = _PROOF_KEYWORDS + (
    "def", "abbrev", "instance", "structure", "class", "inductive", "alias",
    "axiom", "opaque", "irreducible_def",
)


def _strip_declaration_prefix(decl_line: str) -> str:
    """Drop attributes, an ``omit ... in`` prefix, and modifiers from a declaration line.

    Attributes are routinely written on the declaration's own line
    (``@[simp] theorem ...``), so a check that reads the first word without
    stripping them misclassifies every such declaration.
    """
    text = decl_line.strip()
    while True:
        if text.startswith("@["):
            depth, i = 0, 0
            while i < len(text):
                if text[i] == "[":
                    depth += 1
                elif text[i] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            text = text[i + 1:].lstrip()
            continue
        if re.match(r"^omit\b", text) and " in " in text:
            text = text.split(" in ", 1)[1].lstrip()
            continue
        word = text.split(" ", 1)[0]
        if word in _DECL_MODIFIERS:
            text = text[len(word):].lstrip()
            continue
        return text


def _declaration_keyword(lines: list[str], decl: int, lookahead: int = 4) -> str:
    """The keyword introducing the declaration recorded at ``decl``, or ``""``.

    The scanner records the first line of a declaration, which is often an
    attribute line: ``@[simp]`` on its own line above the ``theorem``.  So the
    keyword is looked for on the next few lines too, and only a keyword this
    recognizes is accepted -- an unparsed prefix reads as "unknown" rather than
    as whatever word happened to come first.
    """
    for line in lines[decl : decl + lookahead]:
        words = _strip_declaration_prefix(line).split()
        if words and words[0] in _DECL_KEYWORDS:
            return words[0]
    return ""


def _has_proof_body(lines: list[str], decl: int) -> bool:
    """Whether this declaration's body proves something rather than defines it.

    A ``def``'s body *is* its meaning, and a reviewer reading a statement's
    vocabulary needs it; a proof body only says how, never what.
    """
    return _declaration_keyword(lines, decl) in _PROOF_KEYWORDS


#: Stands in for the elided proof.  Angle brackets are the usual metavariable
#: notation, so nobody reads this as Lean that was actually written.
PROOF_PLACEHOLDER = "<proof-omitted>"


def declaration_statement_text(path: Path, line: int, limit: int = 500) -> str | None:
    """The declaration as written, docstring included, without a proof body.

    This is what a reviewer judging a source-to-Lean correspondence reads: the
    prose the author wrote about the declaration, and the statement it is
    attached to.  How a theorem is *proved* is a separate question, answered by
    the dependency and axiom evidence, so a proof body is elided -- it is
    usually far longer than the statement and pushes the thing under review off
    the screen.  A definition keeps its body, which is its meaning.

    The elision keeps the delimiter the author wrote, so the result still reads
    as Lean: a tactic proof ends ``:= by <proof-omitted>`` and a term proof ends
    ``:= <proof-omitted>``.
    """
    lines = _file_lines(path)
    decl = max(0, line - 1)
    if not lines or decl >= len(lines):
        return None
    if not _has_proof_body(lines, decl):
        return full_declaration_text(path, line, limit)
    header, delimiter, body = _split_declaration_header(lines, decl)
    start = _declaration_block_start(lines, decl)
    out = lines[start:decl] + header.splitlines()
    while out and not out[-1].strip():
        out.pop()
    if not out:
        return None
    if delimiter:
        opener = "by " if re.match(r"^by\b", body) else ""
        out[-1] = f"{out[-1]} {delimiter} {opener}{PROOF_PLACEHOLDER}".rstrip()
    return "\n".join(out).rstrip() or None


def full_declaration_text(path: Path, line: int, limit: int = 500) -> str | None:
    """The whole declaration, statement and proof, as written.

    ``SourceDeclarationText.render`` deliberately stops before the proof body --
    it answers "what does this say" -- and so does
    :func:`declaration_statement_text`.  Auditing a proof needs the body too.

    The declaration's own line is never treated as a terminator: scanning
    forward from the docstring instead of from the declaration made every
    theorem end at its own first line, returning the docstring alone.
    """
    lines = _file_lines(path)
    decl = max(0, line - 1)
    if not lines or decl >= len(lines):
        return None

    start = _declaration_block_start(lines, decl)
    out = lines[start : decl + 1]
    for text in lines[decl + 1 : decl + limit]:
        if text and not text[0].isspace() and any(text.startswith(t) for t in _DECL_STARTS):
            break
        out.append(text)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) or None

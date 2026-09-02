"""Locate a Lean project and resolve live source/build modules.

Inventory commands still enumerate compiled artifacts because they answer questions about
what a library actually built. Target-focused proof graphs are different: importing every
artifact is both wasteful and vulnerable to abandoned ``.olean`` files from an older Lean
toolchain. For those queries this module resolves the target declaration to its live source
module, follows only the local source import closure, and lets Lean elaborate that closure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ._profile import profile

LAKEFILES = ("lakefile.toml", "lakefile.lean")
_IMPORT_RE = re.compile(r"^\s*import\s+(.+?)\s*$")
_DECL_RE_TEMPLATE = (
    r"(?m)^\s*"
    r"(?:(?:@\[[^\n]*\]\s*)?)*"
    r"(?:(?:private|protected|noncomputable|unsafe|partial|scoped|local|opaque)\s+)*"
    r"(?:theorem|lemma|def|abbrev|structure|class|inductive|axiom)\s+"
    r"`?{name}`?(?=\s|\(|\{|:|$)"
)
#: The same shape, capturing whatever short name a declaration gives, so a whole
#: library can be indexed in one pass instead of re-read once per lookup.
_DECL_RE_ANY = re.compile(
    _DECL_RE_TEMPLATE.replace("{name}", r"(?P<name>[^\s(){}:`]+)")
)


class ProjectError(RuntimeError):
    """Raised when a project cannot be located or is not built."""


@dataclass(frozen=True)
class LeanLibrarySpec:
    """Minimal Lake library layout needed for source/module resolution."""

    name: str
    src_dir: str = "."
    globs: tuple[str, ...] = ()


@dataclass
class LeanProject:
    """A Lake project on disk."""

    root: Path
    #: library -> {short declaration name: modules}, built lazily by
    #: :meth:`declaration_index`.
    _decl_index: dict[str, dict[str, list[str]]] = field(default_factory=dict, repr=False,
                                                         compare=False)

    @property
    def lakefile(self) -> Path:
        for name in LAKEFILES:
            candidate = self.root / name
            if candidate.exists():
                return candidate
        raise ProjectError(f"no lakefile under {self.root}")

    @property
    def build_lib(self) -> Path:
        # `.lake` is frequently a symlink to a cache outside the repo; resolve() follows it.
        return (self.root / ".lake" / "build" / "lib" / "lean").resolve()

    def library_specs(self) -> list[LeanLibrarySpec]:
        """Local ``lean_lib`` declarations with their source roots."""
        text = self.lakefile.read_text(encoding="utf-8")
        if self.lakefile.suffix == ".toml":
            blocks = re.split(r"(?m)^\s*\[\[lean_lib\]\]\s*$", text)[1:]
            specs: list[LeanLibrarySpec] = []
            for block in blocks:
                block = re.split(r"(?m)^\s*\[\[.*?\]\]\s*$", block, maxsplit=1)[0]
                name_match = re.search(
                    r"(?m)^\s*name\s*=\s*[\"']([^\"']+)[\"']", block
                )
                if not name_match:
                    continue
                src_match = re.search(
                    r"(?m)^\s*srcDir\s*=\s*[\"']([^\"']+)[\"']", block
                )
                globs_match = re.search(
                    r"(?ms)^\s*globs\s*=\s*\[(.*?)\]", block
                )
                globs = tuple(
                    re.findall(r"[\"']([^\"']+)[\"']", globs_match.group(1))
                ) if globs_match else ()
                specs.append(
                    LeanLibrarySpec(
                        name_match.group(1),
                        src_match.group(1) if src_match else ".",
                        globs,
                    )
                )
            return specs
        names = re.findall(
            r"(?m)^\s*lean_lib\s+[«]?([A-Za-z_][A-Za-z0-9_']*)", text
        )
        return [LeanLibrarySpec(name) for name in dict.fromkeys(names)]

    def declared_libraries(self) -> list[str]:
        """Lean library names declared by the project lakefile."""
        return [spec.name for spec in self.library_specs()]

    def default_targets(self) -> list[str]:
        """Lake default targets, when they can be read from the project file.

        ``lakefile.toml`` is common for data/research repositories and gives us a
        stable definition of the ordinary claimed build surface.  For a Lean
        lakefile, where robust source parsing would be inappropriate here, the
        caller falls back to declared library roots.
        """
        if self.lakefile.suffix != ".toml":
            return []
        text = self.lakefile.read_text(encoding="utf-8")
        match = re.search(r"(?ms)^\s*defaultTargets\s*=\s*\[(.*?)\]", text)
        if not match:
            return []
        return re.findall(r"[\"']([^\"']+)[\"']", match.group(1))

    def library_for_module(self, module: str) -> str | None:
        """Return the most specific local ``lean_lib`` owning ``module``."""
        matches = [
            spec.name for spec in self.library_specs()
            if module == spec.name or module.startswith(spec.name + ".")
        ]
        return max(matches, key=len) if matches else None

    def _library_build_roots(self, library: str) -> list[str]:
        """Source roots representing a normal Lake build of one library.

        A glob-built library has no requirement that its root module imports all
        files, so every live source module is an import root in that case. Otherwise
        the library root
        is the normal Lake surface when it exists.
        """
        spec = self.library_spec(library)
        if spec.globs:
            roots = self.source_modules(library)
            if roots:
                return roots
        if self.source_of(library).exists():
            return [library]
        return self.source_modules(library)

    def unavailable_import_roots(self, roots: Sequence[str]) -> list[str]:
        """Roots that have source but no current importable build artifact.

        Lake may retain a ``lean_lib`` declaration for an archival aggregate
        whose source is intentionally not part of the current build.  Its
        source layout alone is not enough for ``importModules``: Lean needs an
        ``.olean`` at the module path.  This check deliberately says nothing
        about an existing artifact that later fails elaboration; that remains a
        genuine indexing failure.
        """
        return [
            root for root in roots
            if not (self.build_lib.joinpath(*root.split(".")).with_suffix(".olean")).exists()
        ]

    def project_graph_roots(
        self,
        *,
        include_libraries: Sequence[str] = (),
        exclude_libraries: Sequence[str] = (),
        all_libraries: bool = False,
        only_library: str | None = None,
    ) -> dict[str, list[str]]:
        """Map local libraries to module roots for a reusable project graph.

        The default is the project's ordinary Lake build surface.  This avoids
        optional/challenge libraries that are intentionally outside
        ``defaultTargets`` while still honoring glob-built libraries.
        ``all_libraries`` opts into every declared first-party library.
        """
        declared = self.declared_libraries()
        declared_set = set(declared)
        unknown = sorted(set(include_libraries) - declared_set)
        if unknown:
            raise ProjectError(
                "unknown local lean_lib(s): " + ", ".join(unknown)
            )

        roots_by_library: dict[str, list[str]] = {}
        if all_libraries:
            for library in declared:
                roots_by_library[library] = self._library_build_roots(library)
        else:
            targets = self.default_targets() or declared
            for target in targets:
                library = self.library_for_module(target)
                if library is None:
                    continue
                if target == library:
                    roots = self._library_build_roots(library)
                elif self.source_of(target).exists():
                    roots = [target]
                else:
                    # A Lake target can name the library even when source layout
                    # is nonstandard. Fall back to its ordinary build roots.
                    roots = self._library_build_roots(library)
                roots_by_library.setdefault(library, []).extend(roots)

        for library in include_libraries:
            roots_by_library.setdefault(library, []).extend(
                self._library_build_roots(library)
            )

        excluded = set(exclude_libraries)
        roots_by_library = {
            library: list(dict.fromkeys(roots))
            for library, roots in roots_by_library.items()
            if library not in excluded and roots
        }
        if only_library is not None:
            if only_library not in declared_set:
                raise ProjectError(f"unknown local lean_lib {only_library!r}")
            roots = roots_by_library.get(only_library)
            if roots is None:
                roots = self._library_build_roots(only_library)
            roots_by_library = {only_library: roots}
        if not roots_by_library:
            raise ProjectError("project graph scope contains no source-backed lean_libs")
        return {
            library: roots_by_library[library]
            for library in declared
            if library in roots_by_library
        }

    def library_spec(self, library: str) -> LeanLibrarySpec:
        for spec in self.library_specs():
            if spec.name == library:
                return spec
        return LeanLibrarySpec(library)

    def libraries(self) -> list[str]:
        """Declared library roots that currently have compiled artifacts.

        Falls back to built top-level names for nonstandard projects. This is the historical
        inventory behavior used by ``leanq query``/``stats``. Target-focused graph commands use
        :meth:`libraries_for_import_closure` instead.
        """
        declared = self.declared_libraries()
        built = set(self.built_roots())
        selected = [name for name in declared if name in built]
        return sorted(selected or built)

    @profile
    def built_roots(self) -> list[str]:
        """Top-level module names with compiled artifacts."""
        lib = self.build_lib
        if not lib.is_dir():
            return []
        roots = set()
        for olean in lib.glob("*.olean"):
            roots.add(olean.stem)
        for child in lib.iterdir():
            if child.is_dir() and any(child.rglob("*.olean")):
                roots.add(child.name)
        return sorted(roots)

    @profile
    def modules(self, library: str) -> list[str]:
        """Every built module under ``library``, as dotted module names.

        This remains artifact-driven for whole-library inventory queries. Proof-graph queries
        intentionally do *not* call this method: optional/stale artifacts can exist without
        belonging to a target theorem's import closure.
        """
        lib = self.build_lib
        if not lib.is_dir():
            raise ProjectError(
                f"{self.root} has no build tree at {lib}; run `lake build` first"
            )
        mods = []
        root_olean = lib / f"{library}.olean"
        if root_olean.exists():
            mods.append(library)
        subdir = lib / library
        if subdir.is_dir():
            for olean in sorted(subdir.rglob("*.olean")):
                rel = olean.relative_to(lib).with_suffix("")
                mods.append(".".join(rel.parts))
        if not mods:
            raise ProjectError(
                f"no built modules for library {library!r} under {lib}; "
                f"available: {', '.join(self.built_roots()) or '(none)'}"
            )
        live, stale = self._split_stale(sorted(set(mods)))
        self._stale = stale
        return live

    def source_of(self, module: str) -> Path:
        """Resolve a local module to source, honoring each ``lean_lib.srcDir``."""
        candidates: list[Path] = []
        matching = [
            spec for spec in self.library_specs()
            if module == spec.name or module.startswith(spec.name + ".")
        ]
        for spec in sorted(matching, key=lambda item: len(item.name), reverse=True):
            base = (self.root / spec.src_dir).resolve()
            candidates.append(base.joinpath(*module.split(".")).with_suffix(".lean"))
        candidates.append(self.root.joinpath(*module.split(".")).with_suffix(".lean"))
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0] if candidates else self.root.joinpath(*module.split(".")).with_suffix(".lean")

    def module_of_source(self, path: Path) -> str:
        """Convert a project source path to its dotted module name, honoring ``srcDir``."""
        resolved = path.resolve()
        choices: list[tuple[int, Path]] = []
        for spec in self.library_specs():
            base = (self.root / spec.src_dir).resolve()
            try:
                rel = resolved.relative_to(base)
            except ValueError:
                continue
            choices.append((len(base.parts), rel))
        if not choices:
            rel = resolved.relative_to(self.root.resolve())
        else:
            _, rel = max(choices, key=lambda item: item[0])
        return ".".join(rel.with_suffix("").parts)

    def source_modules(self, library: str) -> list[str]:
        """Live source modules belonging to ``library``."""
        spec = self.library_spec(library)
        source_root = (self.root / spec.src_dir).resolve()
        result: list[str] = []
        root_file = source_root.joinpath(*library.split(".")).with_suffix(".lean")
        if root_file.exists():
            result.append(library)
        root_dir = source_root.joinpath(*library.split("."))
        if root_dir.is_dir():
            result.extend(self.module_of_source(path) for path in sorted(root_dir.rglob("*.lean")))
        return list(dict.fromkeys(result))

    def imports_of(self, module: str) -> list[str]:
        """Direct source imports of a local module.

        Imports are used only to choose which project libraries/modules to ask the elaborator
        about. Declaration dependency edges still come exclusively from Lean's environment.
        """
        path = self.source_of(module)
        if not path.exists():
            return []
        imports: list[str] = []
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.split("--", 1)[0].strip()
            match = _IMPORT_RE.match(line)
            if not match:
                continue
            imports.extend(token for token in match.group(1).split() if token)
        return imports

    def local_import_closure(self, roots: Sequence[str]) -> list[str]:
        """Project-local source import closure of ``roots``, including the roots."""
        root_set = set(roots)
        queue = list(dict.fromkeys(roots))
        seen: set[str] = set()
        ordered: list[str] = []
        while queue:
            module = queue.pop(0)
            if module in seen:
                continue
            source = self.source_of(module)
            if not source.exists():
                if module in root_set:
                    raise ProjectError(
                        f"cannot find source for graph root module {module!r} at {source}"
                    )
                continue
            seen.add(module)
            ordered.append(module)
            for imported in self.imports_of(module):
                if imported not in seen and self.source_of(imported).exists():
                    queue.append(imported)
        return ordered

    def libraries_for_import_closure(self, roots: Sequence[str]) -> list[str]:
        """Local ``lean_lib`` roots actually present in the source import closure."""
        modules = self.local_import_closure(roots)
        declared = self.declared_libraries()
        used: set[str] = set()
        for module in modules:
            matches = [
                lib for lib in declared
                if module == lib or module.startswith(lib + ".")
            ]
            if matches:
                used.add(max(matches, key=len))
        if not used:
            raise ProjectError(
                "could not associate graph import roots with any local lean_lib; "
                "pass --include-lib explicitly"
            )
        return [lib for lib in declared if lib in used]

    def declaration_index(self, library: str) -> dict[str, list[str]]:
        """Short declaration name -> the modules of ``library`` that declare it.

        Built in one pass over the library's sources and cached on the project.
        The previous implementation re-read every source file on every lookup,
        which is fine for the handful of names a graph target needs and
        catastrophic for the several hundred an alignment packet seeds: 2.2
        seconds each, so 643 seeds cost twenty-three minutes of pure re-reading
        before Lean was invoked at all.
        """
        cached = self._decl_index.get(library)
        if cached is not None:
            return cached
        index: dict[str, list[str]] = {}
        for module in self.source_modules(library):
            try:
                text = self.source_of(module).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in _DECL_RE_ANY.finditer(text):
                index.setdefault(match.group("name"), []).append(module)
        self._decl_index[library] = index
        return index

    def candidate_declaration_modules(self, target: str) -> list[str]:
        """Every project source module whose text declares ``target``'s short name.

        A source scan cannot tell two declarations with the same short name apart,
        so this returns all of them; :meth:`declaration_modules` insists on one,
        and a caller that only needs the declaration *present* in the environment
        can import them all.
        """
        declared = self.declared_libraries()
        short = target.rsplit(".", 1)[-1]
        first = target.split(".", 1)[0]
        candidate_libs = [first] if first in declared else declared
        matches: list[str] = []
        for library in candidate_libs:
            matches.extend(self.declaration_index(library).get(short, ()))
        return list(dict.fromkeys(matches))

    def declaration_modules(self, targets: Sequence[str]) -> list[str]:
        """Locate source modules defining requested graph target declarations.

        This source scan is only a bootstrap from declaration name to module. The resulting
        dependency graph remains semantic: Lean imports these modules and reports proof-term
        dependencies from the elaborated environment.
        """
        result: list[str] = []
        for target in targets:
            matches = self.candidate_declaration_modules(target)
            if not matches:
                raise ProjectError(
                    f"cannot locate source declaration {target!r}; "
                    "pass --root-module with the module that defines it"
                )
            if len(matches) > 1:
                shown = ", ".join(matches[:6])
                extra = " ..." if len(matches) > 6 else ""
                raise ProjectError(
                    f"declaration name {target!r} is ambiguous across source modules: "
                    f"{shown}{extra}; pass --root-module explicitly"
                )
            result.append(matches[0])
        return list(dict.fromkeys(result))

    def _split_stale(self, mods: list[str]) -> tuple[list[str], list[str]]:
        """Drop built modules whose source is gone.

        This protects whole-library inventory indexing from ordinary renamed/deleted modules.
        It cannot identify old-toolchain artifacts whose source still exists; target-scoped graph
        indexing avoids importing those unrelated artifacts in the first place.
        """
        live = [m for m in mods if self.source_of(m).exists()]
        if not live:
            return mods, []
        stale = [m for m in mods if not self.source_of(m).exists()]
        return live, stale

    @property
    def stale_modules(self) -> list[str]:
        return list(getattr(self, "_stale", []))


@profile
def find_project(start: Path | None = None) -> LeanProject:
    """Walk up from ``start`` until a lakefile appears."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if any((candidate / name).exists() for name in LAKEFILES):
            return LeanProject(candidate)
    raise ProjectError(f"no lakefile at or above {here}")

"""Generate import-only Lean aggregate modules.

The generator is intentionally policy-light: callers choose the library root,
aggregate filename, skipped subtrees, and whether existing cross-library
re-exports should be retained.  This captures the useful part of the original
``generate_all_aggregates.py`` without encoding any Davis--Kahan directory
names.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from .common import atomic_write_text
from .errors import ValidationError

IMPORT_RE = re.compile(r"(?m)^\s*import\s+([A-Za-z0-9_'.₀-₉⁰-⁹′]+)\s*(?:--.*)?$")


@dataclass(frozen=True)
class AggregateChange:
    path: Path
    module: str
    changed: bool
    dangling_reexports: tuple[str, ...] = ()
    #: Modules that depend on every aggregate that could hold them, so no
    #: generated aggregate reaches them.  Reported rather than dropped silently.
    unplaceable_modules: tuple[str, ...] = ()
    text: str = ""

    def to_json(self, root: Path | None = None) -> dict:
        path = self.path
        if root is not None:
            try:
                path = path.relative_to(root)
            except ValueError:
                pass
        return {
            "path": path.as_posix(),
            "module": self.module,
            "changed": self.changed,
            "dangling_reexports": list(self.dangling_reexports),
            "unplaceable_modules": list(self.unplaceable_modules),
        }


def module_of(root: Path, path: Path) -> str:
    return ".".join(path.relative_to(root).with_suffix("").parts)


def module_path(root: Path, module: str) -> Path:
    return root.joinpath(*module.split(".")).with_suffix(".lean")


def _under_skipped(base: Path, directory: Path, skip_dirs: set[str]) -> bool:
    try:
        rel = directory.relative_to(base)
    except ValueError:
        return True
    return bool(skip_dirs.intersection(rel.parts))


class AggregatePlacement:
    """Decide which aggregate each module belongs in.

    Normally a module belongs to its own directory's aggregate.  A module that
    *depends on* that aggregate cannot: importing it there is a cycle, and Lake
    reports it as `build cycle detected` far from the file that caused it.  Such
    a module belongs to the nearest ancestor aggregate it does not depend on --
    which is what a maintainer editing the file by hand ends up doing, after
    which the generator reports the file as permanently stale and regenerating it
    breaks the build.

    Reachability is read from the source imports actually on disk, with the
    candidate aggregate's own edges ignored, because those are the edges being
    recomputed.
    """

    def __init__(self, root: Path, imports: dict[str, set[str]]) -> None:
        self.root = root
        self.imports = imports

    @classmethod
    def from_tree(cls, root: Path, scope=None) -> "AggregatePlacement":
        from .lean_source import lean_files as _lean_files

        imports: dict[str, set[str]] = {}
        for path in _lean_files(root, scope=scope):
            module = ".".join(path.relative_to(root).with_suffix("").parts)
            text = path.read_text(encoding="utf-8", errors="replace")
            imports[module] = set(IMPORT_RE.findall(text))
        return cls(root, imports)

    def depends_on(self, module: str, target: str) -> bool:
        """Does ``module`` reach ``target``, ignoring ``target``'s own imports?"""
        seen = {module}
        stack = [module]
        while stack:
            current = stack.pop()
            for dep in self.imports.get(current, ()):
                if dep == target:
                    return True
                if dep in seen or dep == target:
                    continue
                seen.add(dep)
                stack.append(dep)
        return False


def _foreign_reexports(
    *,
    repo_root: Path,
    output: Path,
    own_library: str,
) -> tuple[list[str], list[str]]:
    if not output.exists():
        return [], []
    kept: list[str] = []
    dangling: list[str] = []
    for module in IMPORT_RE.findall(output.read_text(encoding="utf-8", errors="replace")):
        if module == own_library or module.startswith(own_library + "."):
            continue
        if module_path(repo_root, module).is_file():
            kept.append(module)
        else:
            dangling.append(module)
    return kept, dangling


def aggregate_text(
    *,
    repo_root: Path,
    base: Path,
    directory: Path,
    own_library: str,
    aggregate_name: str = "All.lean",
    skip_dirs: Iterable[str] = (),
    preserve_foreign_reexports: bool = True,
    root_import: str | None = None,
    header: str = "",
    placement: "AggregatePlacement | None" = None,
    promoted: Iterable[str] = (),
) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    """Build the canonical aggregate text for one directory."""
    skip = set(skip_dirs)
    output = directory / aggregate_name
    # A skipped subtree usually has a sibling root module -- `Experimental/` next
    # to `Experimental.lean` -- and importing that root pulls the whole subtree
    # back into the aggregate, defeating the skip while still compiling.
    skipped_roots = {
        child.name + ".lean"
        for child in (directory.iterdir() if directory.is_dir() else [])
        if child.is_dir() and _under_skipped(base, child, skip)
    }
    aggregate_module = module_of(repo_root, output)
    own_modules = [
        module_of(repo_root, path)
        for path in directory.glob("*.lean")
        if path.name != aggregate_name and path.name not in skipped_roots
    ]
    deferred: list[str] = []
    if placement is not None:
        keep: list[str] = []
        for module in own_modules:
            if placement.depends_on(module, aggregate_module):
                deferred.append(module)
            else:
                keep.append(module)
        own_modules = keep
    own_modules.extend(promoted)
    dangling: list[str] = []
    if preserve_foreign_reexports:
        foreign, dangling = _foreign_reexports(
            repo_root=repo_root, output=output, own_library=own_library
        )
        own_modules.extend(foreign)
    own_modules = sorted(dict.fromkeys(own_modules), key=lambda m: (m.rsplit(".", 1)[-1], m))

    submodules: list[str] = []
    for child in sorted(directory.iterdir()) if directory.is_dir() else []:
        if not child.is_dir() or _under_skipped(base, child, skip):
            continue
        if any(p.is_file() for p in child.rglob("*.lean")):
            submodules.append(module_of(repo_root, child / aggregate_name))

    imports: list[str] = []
    if directory == base and root_import:
        imports.append(root_import)
    imports.extend(submodules)
    imports.extend(own_modules)
    imports = list(dict.fromkeys(imports))
    if not imports:
        return None, tuple(dangling), tuple(deferred)

    title = directory.relative_to(repo_root).as_posix()
    normalized_header = header
    if normalized_header and not normalized_header.endswith("\n"):
        normalized_header += "\n"
    text = normalized_header + "".join(f"import {module}\n" for module in imports)
    text += "\n" + _trailer(output, title)
    return text, tuple(dangling), tuple(deferred)


def _trailer(output: Path, title: str) -> str:
    """The module docstring to end a generated aggregate with.

    An aggregate is the natural place for a directory-level note, and the import
    list is the only part of the file a generator can derive.  Overwriting the
    whole trailer deletes such a note on the next regeneration -- silently,
    because the file still compiles.  A trailer whose first line is the standard
    heading is therefore preserved verbatim; anything else is replaced.
    """
    default = f"/-! # `{title}` -/\n"
    if not output.exists():
        return default
    text = output.read_text(encoding="utf-8", errors="replace")
    start = text.rfind("/-!")
    if start == -1 or not text[start:].rstrip().endswith("-/"):
        return default
    block = text[start:].rstrip() + "\n"
    first = block.splitlines()[0].strip()
    if first not in {f"/-! # `{title}` -/", f"/-! # `{title}`"}:
        return default
    return block


def generate_aggregates(
    repo_root: str | Path,
    base: str | Path,
    *,
    own_library: str | None = None,
    aggregate_name: str = "All.lean",
    skip_dirs: Iterable[str] = (),
    preserve_foreign_reexports: bool = True,
    root_import: str | None = None,
    header: str = "",
    check: bool = False,
    respect_cycles: bool = True,
) -> list[AggregateChange]:
    """Generate or check recursive ``All.lean``-style aggregates.

    ``base`` may be absolute or relative to ``repo_root``.  Directories are
    processed deepest-first so parent aggregates can import child aggregates
    that have just been generated.
    """
    root = Path(repo_root).expanduser().resolve()
    base_path = Path(base).expanduser()
    if not base_path.is_absolute():
        base_path = root / base_path
    base_path = base_path.resolve()
    if not base_path.is_dir():
        raise ValidationError(f"aggregate base is not a directory: {base_path}")
    try:
        base_rel = base_path.relative_to(root)
    except ValueError as ex:
        raise ValidationError("aggregate base must live under the repository root") from ex
    library = own_library or (base_rel.parts[0] if base_rel.parts else base_path.name)
    skip = set(skip_dirs)

    dirs = [
        d for d in base_path.rglob("*")
        if d.is_dir()
        and not _under_skipped(base_path, d, skip)
        and any(p.is_file() for p in d.rglob("*.lean"))
    ]
    dirs.append(base_path)
    placement = AggregatePlacement.from_tree(root) if respect_cycles else None
    changes: list[AggregateChange] = []
    seen: set[Path] = set()
    #: Modules deferred by a child directory, waiting for an ancestor that can
    #: take them.  Keyed by the directory that must offer them next.
    pending: dict[Path, list[str]] = {}
    unplaceable: list[str] = []
    for directory in sorted(dirs, key=lambda p: (-len(p.parts), p.as_posix())):
        if directory in seen:
            continue
        seen.add(directory)
        text, dangling, deferred = aggregate_text(
            repo_root=root,
            base=base_path,
            directory=directory,
            own_library=library,
            aggregate_name=aggregate_name,
            skip_dirs=skip,
            preserve_foreign_reexports=preserve_foreign_reexports,
            root_import=root_import,
            header=header,
            placement=placement,
            promoted=sorted(pending.pop(directory, [])),
        )
        if deferred:
            if directory == base_path:
                unplaceable.extend(deferred)
            else:
                pending.setdefault(directory.parent, []).extend(deferred)
        if text is None:
            continue
        output = directory / aggregate_name
        old = output.read_text(encoding="utf-8") if output.exists() else None
        changed = old != text
        if changed and not check:
            atomic_write_text(output, text)
        changes.append(AggregateChange(
            path=output,
            module=module_of(root, output),
            changed=changed,
            dangling_reexports=dangling,
            unplaceable_modules=tuple(sorted(unplaceable)) if directory == base_path else (),
            text=text,
        ))
    return changes

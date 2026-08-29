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
) -> tuple[str | None, tuple[str, ...]]:
    """Build the canonical aggregate text for one directory."""
    skip = set(skip_dirs)
    output = directory / aggregate_name
    own_modules = [
        module_of(repo_root, path)
        for path in directory.glob("*.lean")
        if path.name != aggregate_name
    ]
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
        return None, tuple(dangling)

    title = directory.relative_to(repo_root).as_posix()
    normalized_header = header
    if normalized_header and not normalized_header.endswith("\n"):
        normalized_header += "\n"
    text = normalized_header + "".join(f"import {module}\n" for module in imports)
    text += f"\n/-! # `{title}` -/\n"
    return text, tuple(dangling)


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
    changes: list[AggregateChange] = []
    seen: set[Path] = set()
    for directory in sorted(dirs, key=lambda p: (-len(p.parts), p.as_posix())):
        if directory in seen:
            continue
        seen.add(directory)
        text, dangling = aggregate_text(
            repo_root=root,
            base=base_path,
            directory=directory,
            own_library=library,
            aggregate_name=aggregate_name,
            skip_dirs=skip,
            preserve_foreign_reexports=preserve_foreign_reexports,
            root_import=root_import,
            header=header,
        )
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
            text=text,
        ))
    return changes

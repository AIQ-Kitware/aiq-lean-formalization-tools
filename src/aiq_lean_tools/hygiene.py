"""Cheap repository hygiene checks that catch expensive Lean failure modes."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Iterable

from .lean_source import SourceScope


_MARKER = re.compile(
    "^(?:" + "|".join(c * 7 + (r"(?: .*)?" if c != "=" else "") for c in "<=>") + ")$"
)
DEFAULT_ARTIFACT_EXTENSIONS = (
    ".olean", ".ilean", ".trace", ".c", ".c.o.export", ".c.o.noexport",
    ".extraDepTrace", ".hash", ".log.json",
)


@dataclass(frozen=True)
class ConflictMarker:
    path: Path
    line: int
    text: str

    def to_json(self, root: Path | None = None) -> dict:
        path = self.path
        if root is not None:
            try:
                path = path.relative_to(root)
            except ValueError:
                pass
        return {"path": path.as_posix(), "line": self.line, "text": self.text}


def tracked_files(root: Path) -> list[Path] | None:
    """Return Git-tracked files, or ``None`` when Git metadata is unavailable."""
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return [root / name for name in proc.stdout.split("\0") if name]


def conflict_markers(
    root: str | Path,
    *,
    tracked_only: bool = True,
    skip_parts: Iterable[str] = (".git", ".lake", "build"),
    skip_suffixes: Iterable[str] = (".patch", ".diff"),
) -> list[ConflictMarker]:
    base = Path(root).expanduser().resolve()
    candidates = tracked_files(base) if tracked_only else None
    if candidates is None:
        candidates = [p for p in base.rglob("*") if p.is_file()]
    skip = set(skip_parts)
    suffixes = tuple(skip_suffixes)
    found: list[ConflictMarker] = []
    for path in candidates:
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        if skip.intersection(rel.parts) or path.name.endswith(suffixes):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "<" * 7 not in text and "=" * 7 not in text and ">" * 7 not in text:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if _MARKER.match(line):
                found.append(ConflictMarker(path, line_no, line[:120]))
    return found


def orphan_build_modules(
    root: str | Path,
    *,
    libraries: Iterable[str] | None = None,
    build_dir: str = ".lake/build/lib/lean",
) -> list[Path]:
    """Return module stems with an ``.olean`` but no matching source file.

    Only modules belonging to a configured project source root are judged: the
    build tree also holds dependency artifacts, which are not ours to call
    orphaned.  The source path is recovered through the project scope, because a
    Lake library with ``srcDir`` does not store its source at the module path.
    """
    base = Path(root).expanduser().resolve()
    artifact_root = base / build_dir
    if not artifact_root.is_dir():
        return []
    scope = SourceScope.load(base)
    allowed = set(libraries or ()) or scope.library_names(base)
    out: list[Path] = []
    for olean in sorted(artifact_root.rglob("*.olean")):
        rel = olean.relative_to(artifact_root)
        module = ".".join(rel.with_suffix("").parts)
        if allowed and (not rel.parts or rel.parts[0] not in allowed):
            continue
        candidates = scope.source_paths(module) or [rel.with_suffix(".lean")]
        if not any((base / candidate).is_file() for candidate in candidates):
            out.append(rel.with_suffix(""))
    return out


def remove_orphan_build_modules(
    root: str | Path,
    modules: Iterable[Path],
    *,
    extensions: Iterable[str] = DEFAULT_ARTIFACT_EXTENSIONS,
) -> list[Path]:
    """Remove all known Lake products for orphan module stems."""
    base = Path(root).expanduser().resolve()
    trees = (base / ".lake/build/lib/lean", base / ".lake/build/ir")
    removed: list[Path] = []
    for module in modules:
        for tree in trees:
            for extension in extensions:
                target = tree / Path(str(module) + extension)
                if target.is_file():
                    target.unlink()
                    removed.append(target)
    return removed

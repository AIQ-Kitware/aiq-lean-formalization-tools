"""Measure raw and source-code uses of a symbol or regex across Lean modules."""
from __future__ import annotations

import fnmatch
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Sequence

from .common import Path
from .lean_source import strip_comments

IMPORT_LINE_RE = re.compile(r"(?m)^\s*(?:public\s+)?import\s+.*$")
COMMAND_LINE_RE = re.compile(r"(?m)^\s*#(?:check|print|eval|reduce|synth)\b.*$")


@dataclass(frozen=True)
class SymbolFileCount:
    path: Path
    raw: int
    code: int

    def to_json(self, root: Path | None = None) -> dict[str, Any]:
        path = self.path
        if root is not None:
            try:
                path = path.relative_to(root)
            except ValueError:
                pass
        return {"path": path.as_posix(), "raw": self.raw, "code": self.code}


@dataclass(frozen=True)
class SymbolCensus:
    expression: str
    files_scanned: int
    raw_total: int
    code_total: int
    files: tuple[SymbolFileCount, ...]

    @property
    def noise_total(self) -> int:
        return self.raw_total - self.code_total

    def to_json(self, root: Path | None = None) -> dict[str, Any]:
        return {
            "expression": self.expression,
            "files_scanned": self.files_scanned,
            "raw_total": self.raw_total,
            "code_total": self.code_total,
            "noise_total": self.noise_total,
            "files": [row.to_json(root) for row in self.files],
        }


def _symbol_regex(symbol: str) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9_'])" + re.escape(symbol) + r"(?![A-Za-z0-9_'])")


def _clean_code(text: str, *, remove_imports: bool, remove_commands: bool) -> str:
    clean = strip_comments(text)
    if remove_imports:
        clean = IMPORT_LINE_RE.sub("", clean)
    if remove_commands:
        clean = COMMAND_LINE_RE.sub("", clean)
    return clean


def symbol_census(
    root: str | pathlib.Path,
    *,
    symbol: str | None = None,
    pattern: str | None = None,
    include: Sequence[str] = ("**/*.lean",),
    exclude: Sequence[str] = (),
    remove_imports: bool = True,
    remove_commands: bool = True,
) -> SymbolCensus:
    if (symbol is None) == (pattern is None):
        raise ValueError("provide exactly one of symbol= or pattern=")
    base = Path(root).expanduser().resolve()
    regex = _symbol_regex(symbol) if symbol is not None else re.compile(str(pattern))
    expression = symbol if symbol is not None else str(pattern)
    candidates: set[Path] = set()
    for glob in include:
        candidates.update(path for path in base.glob(glob) if path.is_file())
    paths = []
    for path in sorted(candidates):
        rel = path.relative_to(base).as_posix()
        if any(fnmatch.fnmatch(rel, glob) for glob in exclude):
            continue
        paths.append(path)

    rows: list[SymbolFileCount] = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        raw = len(regex.findall(text))
        code = len(regex.findall(_clean_code(text, remove_imports=remove_imports, remove_commands=remove_commands)))
        if raw or code:
            rows.append(SymbolFileCount(path, raw, code))
    return SymbolCensus(
        expression=expression,
        files_scanned=len(paths),
        raw_total=sum(row.raw for row in rows),
        code_total=sum(row.code for row in rows),
        files=tuple(sorted(rows, key=lambda row: (-row.code, -row.raw, row.path.as_posix()))),
    )

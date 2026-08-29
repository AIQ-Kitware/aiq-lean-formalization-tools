"""Mechanical Lean file conversion to the Lean 4 module/public-import style."""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

from .common import Path, atomic_write_text

MODULE_LINE_RE = re.compile(r"(?m)^\s*module\s*$")
PLAIN_IMPORT_RE = re.compile(r"^import\s+\S")


@dataclass(frozen=True)
class ModuleMigrationResult:
    path: Path
    changed: bool
    already_module: bool
    text: str


def convert_module_text(text: str) -> tuple[str, bool]:
    """Return ``(new_text, changed)`` for a conservative module-style conversion.

    The transformation mirrors the project migration helper that motivated this
    utility: insert ``module`` after a leading copyright block, make top-level plain
    imports public, and add ``public section`` after the module docstring/import area.
    Already-module files are left unchanged.
    """
    lines = text.split("\n")
    if any(re.fullmatch(r"module\s*", line) for line in lines):
        return text, False

    insert_at = 0
    if lines and lines[0].startswith("/-") and not lines[0].startswith("/-!"):
        for i, line in enumerate(lines):
            if line.strip() == "-/":
                insert_at = i + 1
                break

    lines[insert_at:insert_at] = ["module", ""]
    while insert_at + 2 < len(lines) and lines[insert_at + 1] == "" and lines[insert_at + 2] == "":
        del lines[insert_at + 2]

    last_import = -1
    out: list[str] = []
    for i, line in enumerate(lines):
        if PLAIN_IMPORT_RE.match(line):
            line = "public " + line
            last_import = i
        out.append(line)
    lines = out

    if not any(line.startswith("public section") for line in lines):
        pos = last_import + 1
        j = pos
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and lines[j].startswith("/-!"):
            while j < len(lines) and lines[j].rstrip() != "-/":
                j += 1
            pos = min(j + 1, len(lines))
        lines[pos:pos] = ["", "public section"]

    new_text = "\n".join(lines)
    return new_text, new_text != text


def migrate_module_file(path: str | pathlib.Path, *, write: bool = False) -> ModuleMigrationResult:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    already_module = bool(MODULE_LINE_RE.search(text))
    new_text, changed = convert_module_text(text)
    if write and changed:
        atomic_write_text(p, new_text)
    return ModuleMigrationResult(p, changed, already_module, new_text)

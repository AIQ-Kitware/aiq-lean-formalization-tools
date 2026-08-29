"""Recorded proof-provenance inventories for Lean source trees."""
from __future__ import annotations

import fnmatch
import pathlib
import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from .common import Path, find_workspace_root


def normalize_prose(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class ProvenanceBlock:
    path: Path
    line: int
    heading: str
    text: str

    def to_json(self, root: Path | None = None, markers: Mapping[str, str] | None = None) -> dict:
        path = self.path
        if root is not None:
            try:
                path = path.relative_to(root)
            except ValueError:
                pass
        data = {"file": path.as_posix(), "line": self.line, "heading": self.heading, "text": self.text}
        if markers:
            data["markers"] = {
                name: bool(re.search(pattern, self.text, re.I))
                for name, pattern in markers.items()
            }
        return data


def provenance_blocks(path: str | pathlib.Path, *, heading: str = "Provenance", max_lines: int = 120) -> list[ProvenanceBlock]:
    p = Path(path)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    needle = "## " + heading
    out: list[ProvenanceBlock] = []
    for i, line in enumerate(lines):
        if needle not in line:
            continue
        captured: list[str] = []
        for j in range(i + 1, min(len(lines), i + 1 + max_lines)):
            cur = lines[j]
            stripped = cur.lstrip()
            if j > i + 1 and (stripped.startswith("## ") or "-/" in cur):
                before = cur.split("-/", 1)[0]
                if before.strip():
                    captured.append(before)
                break
            captured.append(cur)
        out.append(ProvenanceBlock(p, i + 1, heading, normalize_prose(" ".join(captured))))
    return out


def _match(rel: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(rel, pattern) or Path(rel).match(pattern) for pattern in patterns)


def provenance_inventory(
    root: str | pathlib.Path | None = None,
    *,
    include: Sequence[str] = ("**/*.lean", "*.lean"),
    heading: str = "Provenance",
    markers: Mapping[str, str] | None = None,
) -> dict:
    base = find_workspace_root(root)
    blocks: list[ProvenanceBlock] = []
    scanned_files = 0
    files_with_blocks: set[str] = set()
    for path in sorted(base.rglob("*.lean")):
        rel = path.relative_to(base).as_posix()
        if {".git", ".lake", "build", "vendor", "external"}.intersection(path.relative_to(base).parts):
            continue
        if not _match(rel, include):
            continue
        scanned_files += 1
        rows = provenance_blocks(path, heading=heading)
        if rows:
            files_with_blocks.add(rel)
            blocks.extend(rows)
    marker_counts: dict[str, int] = {}
    if markers:
        for name, pattern in markers.items():
            marker_counts[name] = sum(bool(re.search(pattern, block.text, re.I)) for block in blocks)
    return {
        "root": str(base),
        "heading": heading,
        "scanned_files": scanned_files,
        "files_with_blocks": len(files_with_blocks),
        "block_count": len(blocks),
        "marker_counts": marker_counts,
        "blocks": [block.to_json(base, markers) for block in blocks],
    }

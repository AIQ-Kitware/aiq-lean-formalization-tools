"""Generated review checklists that preserve marks across regeneration and renames."""
from __future__ import annotations

import fnmatch
import pathlib
import re
import subprocess
from dataclasses import dataclass
from typing import Sequence

from .common import Path, atomic_write_text

DONE_RE = re.compile(r"^\s*-\s*\[[xX]\]\s+`([^`]+)`")


@dataclass(frozen=True)
class ChecklistProgress:
    files_done: int
    files_total: int
    lines_done: int
    lines_total: int
    groups_done: int
    groups_total: int
    ready_groups: tuple[str, ...]

    def to_json(self) -> dict:
        return {
            "files_done": self.files_done,
            "files_total": self.files_total,
            "lines_done": self.lines_done,
            "lines_total": self.lines_total,
            "groups_done": self.groups_done,
            "groups_total": self.groups_total,
            "ready_groups": list(self.ready_groups),
        }


def tracked_files(root: Path) -> list[str]:
    done = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=False)
    if done.returncode == 0:
        return sorted(rel for rel in done.stdout.splitlines() if rel and (root / rel).is_file())
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and ".git" not in path.parts)


def git_renames(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for args in (["diff", "--name-status", "-M", "HEAD"], ["diff", "--name-status", "-M", "--cached", "HEAD"]):
        done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
        if done.returncode not in {0, 128}:
            continue
        for line in done.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3 and parts[0].startswith("R"):
                out[parts[1]] = parts[2]
    return out


def existing_marks(path: Path, *, renames: dict[str, str] | None = None) -> set[str]:
    if not path.exists():
        return set()
    marks = {match.group(1) for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if (match := DONE_RE.match(line))}
    for old, new in (renames or {}).items():
        if old in marks:
            marks.add(new)
    return marks


def line_count(path: Path) -> int:
    try:
        return path.read_text(encoding="utf-8").count("\n") + 1
    except (OSError, UnicodeDecodeError):
        return 0


def _kind(rel: str) -> str:
    suffix = pathlib.PurePath(rel).suffix.lower()
    return {
        ".lean": "Lean source",
        ".py": "tooling",
        ".sh": "tooling",
        ".md": "documentation",
        ".json": "data/config",
        ".yaml": "data/config",
        ".yml": "data/config",
        ".toml": "build config",
        ".tex": "paper source",
        ".bib": "bibliography",
    }.get(suffix, "other")


def select_files(
    root: Path,
    *,
    include: Sequence[str] = ("*",),
    exclude: Sequence[str] = (".git/**", ".lake/**", "build/**", "vendor/**", "external/**"),
) -> list[str]:
    rows = []
    for rel in tracked_files(root):
        if include and not any(fnmatch.fnmatch(rel, pattern) for pattern in include):
            continue
        if any(fnmatch.fnmatch(rel, pattern) or rel.startswith(pattern.rstrip("**").rstrip("/")) for pattern in exclude):
            continue
        rows.append(rel)
    return sorted(rows)


def group_name(rel: str, *, depth: int = 2, split_nonlean_by_kind: bool = True) -> str:
    parts = pathlib.PurePath(rel).parts
    prefix = "/".join(parts[:max(1, depth)]) if len(parts) > 1 else "(root files)"
    if split_nonlean_by_kind and pathlib.PurePath(rel).suffix != ".lean":
        return f"{prefix} :: {_kind(rel)}"
    return prefix


def build_groups(files: Sequence[str], *, depth: int = 2, split_nonlean_by_kind: bool = True) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for rel in files:
        groups.setdefault(group_name(rel, depth=depth, split_nonlean_by_kind=split_nonlean_by_kind), []).append(rel)
    return dict(sorted(groups.items()))


def progress(
    root: Path,
    files: Sequence[str],
    groups: dict[str, list[str]],
    file_marks: set[str],
    group_marks: set[str],
) -> ChecklistProgress:
    total_lines = sum(line_count(root / rel) for rel in files)
    done_lines = sum(line_count(root / rel) for rel in files if rel in file_marks)
    ready = tuple(group for group, members in groups.items() if group not in group_marks and all(rel in file_marks for rel in members))
    return ChecklistProgress(
        files_done=len(set(files) & file_marks),
        files_total=len(files),
        lines_done=done_lines,
        lines_total=total_lines,
        groups_done=len(set(groups) & group_marks),
        groups_total=len(groups),
        ready_groups=ready,
    )


def render_file_checklist(root: Path, files: Sequence[str], groups: dict[str, list[str]], marks: set[str]) -> str:
    total_lines = sum(line_count(root / rel) for rel in files)
    done = len(set(files) & marks)
    out = [
        "# Review checklist — every file, once",
        "",
        "Generated by `aiq-lean source checklist`. Checked marks are preserved across regeneration and Git-detected renames.",
        "",
        f"{done}/{len(files)} files reviewed · {len(files)} files · {total_lines:,} lines · {len(groups)} groups",
        "",
        "Mark a file complete only after its findings are recorded or fixed.",
    ]
    for group, members in groups.items():
        group_lines = sum(line_count(root / rel) for rel in members)
        group_done = sum(rel in marks for rel in members)
        out.extend(["", f"### {group}", "", f"*{len(members)} files, {group_lines:,} lines — {group_done}/{len(members)} reviewed*", ""])
        for rel in sorted(members, key=lambda item: (-line_count(root / item), item)):
            mark = "x" if rel in marks else " "
            out.append(f"- [{mark}] `{rel}` — {line_count(root / rel):,} lines · {_kind(rel)}")
    return "\n".join(out) + "\n"


def render_group_checklist(root: Path, groups: dict[str, list[str]], file_marks: set[str], group_marks: set[str]) -> str:
    done = len(set(groups) & group_marks)
    out = [
        "# Review checklist — holistic group review",
        "",
        "Generated by `aiq-lean source checklist`. A group is ready when every member file has been reviewed.",
        "",
        f"{done}/{len(groups)} groups reviewed",
        "",
    ]
    for group, members in groups.items():
        group_lines = sum(line_count(root / rel) for rel in members)
        ndone = sum(rel in file_marks for rel in members)
        state = "READY" if ndone == len(members) else f"blocked ({ndone}/{len(members)} files)"
        mark = "x" if group in group_marks else " "
        out.append(f"- [{mark}] `{group}` — {len(members)} files, {group_lines:,} lines — **{state}**")
    return "\n".join(out) + "\n"


def update_checklists(
    root: str | pathlib.Path,
    *,
    file_path: str | pathlib.Path,
    group_path: str | pathlib.Path,
    include: Sequence[str] = ("*",),
    exclude: Sequence[str] = (".git/**", ".lake/**", "build/**", "vendor/**", "external/**"),
    group_depth: int = 2,
    split_nonlean_by_kind: bool = True,
    write: bool = True,
) -> ChecklistProgress:
    base = Path(root).expanduser().resolve()
    file_target = Path(file_path)
    group_target = Path(group_path)
    if not file_target.is_absolute():
        file_target = base / file_target
    if not group_target.is_absolute():
        group_target = base / group_target
    renames = git_renames(base)
    file_marks = existing_marks(file_target, renames=renames)
    group_marks = existing_marks(group_target)
    files = select_files(base, include=include, exclude=exclude)
    groups = build_groups(files, depth=group_depth, split_nonlean_by_kind=split_nonlean_by_kind)
    result = progress(base, files, groups, file_marks, group_marks)
    if write:
        atomic_write_text(file_target, render_file_checklist(base, files, groups, file_marks))
        atomic_write_text(group_target, render_group_checklist(base, groups, file_marks, group_marks))
    return result

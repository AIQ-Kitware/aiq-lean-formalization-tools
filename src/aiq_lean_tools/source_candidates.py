"""Candidate-generating static audits for large Lean source trees.

These detectors prioritize human review.  They deliberately report candidates
rather than semantic findings: source normalization cannot establish theorem
or definition equivalence, and occurrence counting cannot establish API value.
Use ``leanq`` when a conclusion depends on elaboration or dependency semantics.
"""
from __future__ import annotations

import collections
import fnmatch
import hashlib
import pathlib
import re
import subprocess
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from .common import Path
from .lean_source import strip_comments

DECL_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)"
    r"(?P<attrs>(?:@\[[^\n]*\]\s*)*)"
    r"(?P<mods>(?:(?:private|protected|noncomputable|partial|unsafe|scoped|local|public)\s+)*)"
    r"(?P<kind>theorem|lemma|def|abbrev|structure|class|instance|inductive|opaque)\s+"
    r"(?P<name>`[^`]+`|[A-Za-z_][A-Za-z0-9_.'’!?₀-₉⁰-⁹′]*)"
)

STRUCTURAL_LINE_RE = re.compile(
    r"^\s*(?:section|namespace|end|open|variable|include|omit|attribute|export|@\[|/-)\b"
)

DEFAULT_NAME_PATTERN = re.compile(
    r"(?i)(genuine|faithful|literature|truly|actual|proper(?!ty)|"
    r"legacy|compat(?!ible)|_aux|Aux[A-Z]|tmp|_v[0-9]|[a-z]V[0-9]|_old|_new)"
)

DEFINITION_KINDS = {"def", "abbrev", "structure", "class", "instance", "inductive", "opaque"}
BODY_ESCAPE_RE = re.compile(r":=[\s\S]*?\b(?:sorry|admit)\b")
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_'’]*")


@dataclass(frozen=True)
class DeclarationBlock:
    name: str
    kind: str
    path: Path
    line: int
    statement: str
    body: str

    def to_json(self, root: Path | None = None) -> dict:
        path = self.path
        if root is not None:
            try:
                path = path.relative_to(root)
            except ValueError:
                pass
        return {
            "name": self.name,
            "kind": self.kind,
            "path": path.as_posix(),
            "line": self.line,
            "statement_lines": max(1, self.statement.count("\n") + 1),
            "body_lines": self.body_line_count,
        }

    @property
    def body_line_count(self) -> int:
        body = _trim_structural_tail(self.body)
        return len([line for line in body.splitlines() if line.strip()])


@dataclass(frozen=True)
class DuplicateGroup:
    digest: str
    mode: str
    normalized: str
    declarations: tuple[DeclarationBlock, ...]
    forwarding_alias: bool = False

    def to_json(self, root: Path | None = None) -> dict:
        return {
            "digest": self.digest,
            "mode": self.mode,
            "normalized": self.normalized,
            "forwarding_alias": self.forwarding_alias,
            "declarations": [row.to_json(root) for row in self.declarations],
        }


@dataclass(frozen=True)
class DeadDefinitionCandidate:
    declaration: DeclarationBlock
    short_name: str
    occurrences: int
    definition_count: int

    def to_json(self, root: Path | None = None) -> dict:
        return {
            "declaration": self.declaration.to_json(root),
            "short_name": self.short_name,
            "occurrences": self.occurrences,
            "definition_count": self.definition_count,
        }


@dataclass(frozen=True)
class NameCandidate:
    declaration: DeclarationBlock
    matched: str

    def to_json(self, root: Path | None = None) -> dict:
        return {"declaration": self.declaration.to_json(root), "matched": self.matched}


@dataclass(frozen=True)
class EscapeCandidate:
    declaration: DeclarationBlock

    def to_json(self, root: Path | None = None) -> dict:
        return self.declaration.to_json(root)


def _git_tracked_lean_files(root: Path) -> list[Path] | None:
    try:
        done = subprocess.run(
            ["git", "ls-files", "*.lean"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if done.returncode != 0:
        return None
    return [root / rel for rel in done.stdout.splitlines() if rel and (root / rel).is_file()]


def lean_files(
    root: str | pathlib.Path,
    *,
    roots: Sequence[str] = (),
    tracked_only: bool = True,
    exclude_parts: Iterable[str] = (".git", ".lake", "build", "vendor", "external", "retired"),
) -> list[Path]:
    """Return Lean files for candidate audits.

    ``roots`` restricts candidate definitions/declarations.  Callers that need a
    whole-repository usage corpus should obtain it separately with ``roots=()``.
    """
    base = Path(root).expanduser().resolve()
    files = _git_tracked_lean_files(base) if tracked_only else None
    if files is None:
        files = [path for path in base.rglob("*.lean") if path.is_file()]
    excluded = set(exclude_parts)
    prefixes = tuple(root.replace("\\", "/").rstrip("/") for root in roots if root)
    out: list[Path] = []
    for path in files:
        try:
            rel = path.relative_to(base).as_posix()
        except ValueError:
            continue
        if excluded.intersection(path.relative_to(base).parts):
            continue
        if prefixes and not any(rel == prefix or rel.startswith(prefix + "/") for prefix in prefixes):
            continue
        out.append(path)
    return sorted(out)


def _find_body_cut(chunk: str) -> int | None:
    """Find the first top-level ``:=`` in a declaration chunk."""
    depth = 0
    in_string = False
    escaped = False
    i = 0
    while i < len(chunk):
        ch = chunk[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}" and depth:
            depth -= 1
        elif depth == 0 and chunk.startswith(":=", i):
            return i
        i += 1
    return None


def declaration_blocks(path: str | pathlib.Path) -> list[DeclarationBlock]:
    path = Path(path)
    original = path.read_text(encoding="utf-8", errors="replace")
    clean = strip_comments(original)
    matches = list(DECL_RE.finditer(clean))
    out: list[DeclarationBlock] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(clean)
        chunk = clean[start:end]
        cut = _find_body_cut(chunk)
        statement = chunk[:cut] if cut is not None else chunk
        body = chunk[cut:] if cut is not None else ""
        out.append(
            DeclarationBlock(
                name=match.group("name").strip("`"),
                kind=match.group("kind"),
                path=path,
                line=clean.count("\n", 0, start) + 1,
                statement=statement,
                body=body,
            )
        )
    return out


def _trim_structural_tail(text: str) -> str:
    lines = text.strip().splitlines()
    out: list[str] = []
    for line in lines:
        if out and STRUCTURAL_LINE_RE.match(line):
            break
        out.append(line)
    return "\n".join(out).strip()


def normalize_declaration_text(text: str, name: str) -> str:
    value = text
    value = re.sub(
        r"^\s*(?:@\[[^\n]*\]\s*)*"
        r"(?:(?:private|protected|noncomputable|partial|unsafe|scoped|local|public)\s+)*"
        r"(?:theorem|lemma|def|abbrev|structure|class|instance|inductive|opaque)\s+"
        + re.escape(name),
        "",
        value,
    )
    value = re.sub(r"\.\{[^}]*\}", "", value)
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"⦃[^⦄]*⦄|\{[^{}]*:\s*", "{:", value)
    return value.strip()


def _is_forwarding_alias(rows: Sequence[DeclarationBlock]) -> bool:
    names = {row.name for row in rows}
    for row in rows:
        body = _trim_structural_tail(row.body)
        if len(body.splitlines()) > 3:
            continue
        for other in names:
            if other != row.name and re.search(r"(?<![A-Za-z0-9_'])" + re.escape(other) + r"(?![A-Za-z0-9_'])", body):
                return True
    return False


def duplicate_candidates(
    root: str | pathlib.Path,
    *,
    roots: Sequence[str] = (),
    definitions: bool = False,
    minimum_chars: int = 60,
    include_forwarders: bool = False,
    tracked_only: bool = True,
) -> list[DuplicateGroup]:
    base = Path(root).expanduser().resolve()
    buckets: dict[str, list[tuple[DeclarationBlock, str]]] = collections.defaultdict(list)
    for path in lean_files(base, roots=roots, tracked_only=tracked_only):
        for row in declaration_blocks(path):
            if definitions:
                if row.kind not in {"def", "abbrev"}:
                    continue
                normalized = normalize_declaration_text(_trim_structural_tail(row.body), row.name)
            else:
                if row.kind not in {"theorem", "lemma"}:
                    continue
                normalized = normalize_declaration_text(row.statement, row.name)
            if len(normalized) < minimum_chars:
                continue
            digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
            buckets[digest].append((row, normalized))
    groups: list[DuplicateGroup] = []
    mode = "definition-body" if definitions else "theorem-statement"
    for digest, members in buckets.items():
        rows = tuple(row for row, _ in members)
        if len({row.path for row in rows}) <= 1:
            continue
        forwarding = not definitions and _is_forwarding_alias(rows)
        if forwarding and not include_forwarders:
            continue
        groups.append(DuplicateGroup(digest, mode, members[0][1], rows, forwarding))
    return sorted(groups, key=lambda group: (-len(group.declarations), group.digest))


def largest_declarations(
    root: str | pathlib.Path,
    *,
    roots: Sequence[str] = (),
    tracked_only: bool = True,
) -> list[DeclarationBlock]:
    rows: list[DeclarationBlock] = []
    for path in lean_files(root, roots=roots, tracked_only=tracked_only):
        rows.extend(declaration_blocks(path))
    return rows


def dead_definition_candidates(
    root: str | pathlib.Path,
    *,
    roots: Sequence[str] = (),
    tracked_only: bool = True,
) -> list[DeadDefinitionCandidate]:
    """Find public-looking definitions whose short name has no extra use.

    Candidate definitions may be scoped with ``roots`` but occurrence counting
    always uses the whole repository, so consumers in another library count.
    """
    base = Path(root).expanduser().resolve()
    candidates: dict[str, list[DeclarationBlock]] = collections.defaultdict(list)
    for path in lean_files(base, roots=roots, tracked_only=tracked_only):
        for row in declaration_blocks(path):
            if row.kind in {"def", "abbrev", "structure", "class", "inductive"}:
                candidates[row.name.rsplit(".", 1)[-1]].append(row)

    counts: collections.Counter[str] = collections.Counter()
    for path in lean_files(base, tracked_only=tracked_only):
        counts.update(TOKEN_RE.findall(strip_comments(path.read_text(encoding="utf-8", errors="replace"))))

    out: list[DeadDefinitionCandidate] = []
    for short_name, rows in candidates.items():
        count = counts[short_name]
        if count <= len(rows):
            out.extend(DeadDefinitionCandidate(row, short_name, count, len(rows)) for row in rows)
    return sorted(out, key=lambda item: (item.declaration.path.as_posix(), item.declaration.line))


def name_candidates(
    root: str | pathlib.Path,
    *,
    roots: Sequence[str] = (),
    pattern: str | re.Pattern[str] | None = None,
    tracked_only: bool = True,
) -> list[NameCandidate]:
    regex = DEFAULT_NAME_PATTERN if pattern is None else (re.compile(pattern) if isinstance(pattern, str) else pattern)
    out: list[NameCandidate] = []
    for path in lean_files(root, roots=roots, tracked_only=tracked_only):
        for row in declaration_blocks(path):
            match = regex.search(row.name)
            if match:
                out.append(NameCandidate(row, match.group(0)))
    return sorted(out, key=lambda item: (item.declaration.path.as_posix(), item.declaration.line))


def definitional_escape_candidates(
    root: str | pathlib.Path,
    *,
    roots: Sequence[str] = (),
    tracked_only: bool = True,
) -> list[EscapeCandidate]:
    out: list[EscapeCandidate] = []
    for path in lean_files(root, roots=roots, tracked_only=tracked_only):
        for row in declaration_blocks(path):
            if row.kind in DEFINITION_KINDS and BODY_ESCAPE_RE.search(_trim_structural_tail(row.body)):
                out.append(EscapeCandidate(row))
    return sorted(out, key=lambda item: (item.declaration.path.as_posix(), item.declaration.line))



@dataclass(frozen=True)
class FileReviewProfile:
    path: Path
    lines: int
    declaration_counts: dict[str, int]
    max_statement_lines: int
    max_body_lines: int
    duplicate_candidates: tuple[str, ...]
    dead_candidates: tuple[str, ...]
    name_candidates: tuple[str, ...]
    definitional_escapes: tuple[str, ...]

    def to_json(self, root: Path | None = None) -> dict:
        path = self.path
        if root is not None:
            try:
                path = path.relative_to(root)
            except ValueError:
                pass
        return {
            "path": path.as_posix(),
            "lines": self.lines,
            "declarations": dict(self.declaration_counts),
            "max_statement_lines": self.max_statement_lines,
            "max_body_lines": self.max_body_lines,
            "duplicate_candidates": list(self.duplicate_candidates),
            "dead_candidates": list(self.dead_candidates),
            "name_candidates": list(self.name_candidates),
            "definitional_escapes": list(self.definitional_escapes),
        }


def review_profiles(
    root: str | pathlib.Path,
    *,
    roots: Sequence[str] = (),
    minimum_duplicate_chars: int = 60,
    tracked_only: bool = True,
) -> list[FileReviewProfile]:
    """Build one review-oriented structural profile per Lean file."""
    base = Path(root).expanduser().resolve()
    files = lean_files(base, roots=roots, tracked_only=tracked_only)
    duplicates = duplicate_candidates(
        base,
        roots=roots,
        minimum_chars=minimum_duplicate_chars,
        tracked_only=tracked_only,
    )
    duplicate_members: dict[Path, set[str]] = collections.defaultdict(set)
    for group in duplicates:
        for row in group.declarations:
            duplicate_members[row.path].add(row.name)
    dead_members: dict[Path, set[str]] = collections.defaultdict(set)
    for row in dead_definition_candidates(base, roots=roots, tracked_only=tracked_only):
        dead_members[row.declaration.path].add(row.declaration.name)
    name_members: dict[Path, set[str]] = collections.defaultdict(set)
    for row in name_candidates(base, roots=roots, tracked_only=tracked_only):
        name_members[row.declaration.path].add(row.declaration.name)
    escape_members: dict[Path, set[str]] = collections.defaultdict(set)
    for row in definitional_escape_candidates(base, roots=roots, tracked_only=tracked_only):
        escape_members[row.declaration.path].add(row.declaration.name)

    out: list[FileReviewProfile] = []
    for path in files:
        blocks = declaration_blocks(path)
        kinds = collections.Counter(row.kind for row in blocks)
        text = path.read_text(encoding="utf-8", errors="replace")
        out.append(
            FileReviewProfile(
                path=path,
                lines=text.count("\n") + 1,
                declaration_counts=dict(kinds),
                max_statement_lines=max((row.statement.count("\n") + 1 for row in blocks), default=0),
                max_body_lines=max((row.body_line_count for row in blocks), default=0),
                duplicate_candidates=tuple(sorted(duplicate_members[path])),
                dead_candidates=tuple(sorted(dead_members[path])),
                name_candidates=tuple(sorted(name_members[path])),
                definitional_escapes=tuple(sorted(escape_members[path])),
            )
        )
    return sorted(out, key=lambda row: (-row.lines, row.path.as_posix()))

def filter_paths(paths: Sequence[Path], patterns: Sequence[str], *, root: Path) -> list[Path]:
    """Filter paths by shell-style relative path patterns."""
    if not patterns:
        return list(paths)
    return [path for path in paths if any(fnmatch.fnmatch(path.relative_to(root).as_posix(), pat) for pat in patterns)]

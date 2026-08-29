"""Auditable Git history/provenance summaries for formalization projects."""
from __future__ import annotations

import collections
import html
import pathlib
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from .common import Path, find_workspace_root

COAUTHOR_LINE_RE = re.compile(r"^Co[- ]?Authored[- ]By:\s*(.*?)\s*$", re.I | re.M)
EMAIL_TRAILER_RE = re.compile(r"^(.*?)\s*(?:<([^>]+)>|([^\s<>]+@[^\s<>]+))\s*$")


def parse_coauthors(body: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for match in COAUTHOR_LINE_RE.finditer(body):
        payload = match.group(1).strip()
        em = EMAIL_TRAILER_RE.match(payload)
        if em:
            name = em.group(1).strip()
            email = (em.group(2) or em.group(3) or "").strip()
        else:
            name, email = payload, ""
        if name:
            out.append((name, email))
    return out


def parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class GitCommit:
    commit: str
    timestamp: str
    author_name: str
    author_email: str
    subject: str
    body: str
    files: tuple[str, ...]
    additions: int
    deletions: int
    binary_files: int
    lean_additions: int
    lean_deletions: int

    @property
    def coauthors(self) -> list[tuple[str, str]]:
        return parse_coauthors(self.body)

    def to_json(self) -> dict:
        return {
            "commit": self.commit,
            "timestamp": self.timestamp,
            "author_name": self.author_name,
            "author_email": self.author_email,
            "subject": self.subject,
            "coauthors": [{"name": name, "email": email} for name, email in self.coauthors],
            "files": list(self.files),
            "additions": self.additions,
            "deletions": self.deletions,
            "binary_files": self.binary_files,
            "lean_additions": self.lean_additions,
            "lean_deletions": self.lean_deletions,
        }


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={root}", *args],
        cwd=root,
        text=True,
        errors="replace",
    )


def load_git_history(
    root: str | pathlib.Path | None = None,
    *,
    rev: str = "HEAD",
    pathspecs: Sequence[str] = (),
) -> list[GitCommit]:
    base = find_workspace_root(root)
    fmt = "%x1e%H%x1f%aI%x1f%an%x1f%ae%x1f%s%x1f%B%x1d"
    args = ["log", rev, "--reverse", "--numstat", f"--format={fmt}"]
    if pathspecs:
        args += ["--", *pathspecs]
    raw = _git(base, *args)
    commits: list[GitCommit] = []
    for record in raw.split("\x1e"):
        if not record.strip() or "\x1d" not in record:
            continue
        header, numstats = record.split("\x1d", 1)
        parts = header.split("\x1f", 5)
        if len(parts) != 6:
            continue
        commit, ts, author, email, subject, body = [part.strip() for part in parts]
        files: list[str] = []
        additions = deletions = binary_files = lean_additions = lean_deletions = 0
        for line in numstats.splitlines():
            bits = line.split("\t")
            if len(bits) < 3:
                continue
            a, d, path = bits[0], bits[1], bits[2]
            files.append(path)
            if a == "-" or d == "-":
                binary_files += 1
                continue
            try:
                ai, di = int(a), int(d)
            except ValueError:
                continue
            additions += ai
            deletions += di
            if path.endswith(".lean"):
                lean_additions += ai
                lean_deletions += di
        commits.append(GitCommit(
            commit=commit,
            timestamp=ts,
            author_name=author,
            author_email=email,
            subject=subject,
            body=body,
            files=tuple(files),
            additions=additions,
            deletions=deletions,
            binary_files=binary_files,
            lean_additions=lean_additions,
            lean_deletions=lean_deletions,
        ))
    return commits


def history_summary(commits: Sequence[GitCommit]) -> dict:
    authors = collections.Counter(commit.author_name for commit in commits)
    coauthors = collections.Counter(name for commit in commits for name, _ in commit.coauthors)
    touched = collections.Counter(path for commit in commits for path in set(commit.files))
    timestamps = [parse_dt(commit.timestamp) for commit in commits if commit.timestamp]
    return {
        "commit_count": len(commits),
        "first_timestamp": min(timestamps).isoformat() if timestamps else None,
        "last_timestamp": max(timestamps).isoformat() if timestamps else None,
        "authors": dict(authors.most_common()),
        "coauthors": dict(coauthors.most_common()),
        "additions": sum(c.additions for c in commits),
        "deletions": sum(c.deletions for c in commits),
        "lean_additions": sum(c.lean_additions for c in commits),
        "lean_deletions": sum(c.lean_deletions for c in commits),
        "binary_files": sum(c.binary_files for c in commits),
        "unique_files_touched": len(touched),
        "most_touched_files": [{"path": path, "commits": count} for path, count in touched.most_common(20)],
    }


def render_history_html(commits: Sequence[GitCommit], *, title: str = "Formalization history") -> str:
    summary = history_summary(commits)
    rows = []
    for commit in reversed(commits):
        coauthors = ", ".join(name for name, _ in commit.coauthors)
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(commit.commit[:12])}</code></td>"
            f"<td>{html.escape(commit.timestamp)}</td>"
            f"<td>{html.escape(commit.author_name)}</td>"
            f"<td>{html.escape(coauthors)}</td>"
            f"<td>{html.escape(commit.subject)}</td>"
            f"<td>{commit.lean_additions:+d}/{commit.lean_deletions:+d}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<meta charset='utf-8'>
<title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:2rem;color:#222;line-height:1.4}} .stats{{display:flex;gap:1rem;flex-wrap:wrap}} .stat{{border:1px solid #ccc;border-radius:9px;padding:.7rem 1rem}} table{{border-collapse:collapse;width:100%;margin-top:1rem}} th,td{{padding:.45rem;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}} code{{white-space:nowrap}}
</style>
<h1>{html.escape(title)}</h1>
<div class='stats'><div class='stat'><strong>{summary['commit_count']}</strong><br>commits</div><div class='stat'><strong>{summary['lean_additions']:,}</strong><br>Lean additions</div><div class='stat'><strong>{summary['lean_deletions']:,}</strong><br>Lean deletions</div><div class='stat'><strong>{summary['unique_files_touched']}</strong><br>files touched</div></div>
<table><thead><tr><th>Commit</th><th>Timestamp</th><th>Author</th><th>Co-authors</th><th>Subject</th><th>Lean +/−</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
"""

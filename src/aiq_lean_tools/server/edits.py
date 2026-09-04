"""Annotating a ledger from the browser, without hiding what that costs.

These files are gated. A census row carries an accepted `completion_certification`;
an inventory carries `canonical_evidence_sha256` computed over declaration
evidence; a review row carries statement pins. An edit that silently re-stamped
any of those would defeat the review it is supposed to serve, so this module
does the opposite of silence:

* only fields a reviewer is *meant* to write are writable at all -- notes,
  clause status, review prose. Everything else is refused, by JSON pointer.
* nothing is re-stamped. If a document carries an acceptance that this edit may
  have invalidated, the response says so and names the field, and the gate is
  left to fail honestly on its next run.
* every write appends to a journal next to the data, recording who, when, the
  pointer, the value before and after, and the file digest either side.

The journal is the audit trail the read-write decision requires: any annotation
made in a browser can be replayed, attributed, and reverted from it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JOURNAL_NAME = "annotation-journal.jsonl"

#: JSON pointers a reviewer may write, as regular expressions over the whole
#: pointer. Anything not matched here is refused.
WRITABLE = (
    r"/items/\d+/notes",
    r"/items/\d+/next_action",
    r"/items/\d+/summary",
    r"/items/\d+/semantic_review/note",
    r"/items/\d+/semantic_review/claim",
    r"/items/\d+/semantic_review/clause_map/\d+/status",
    r"/items/\d+/semantic_review/clause_map/\d+/lean_realization",
    r"/items/\d+/semantic_review/clause_map/\d+/source_clause",
    r"/rows/\d+/(notes|note|status)",
    # A review kept beside its census grades clauses by `relation` and has no
    # `status`; these are the same three reviewer claims as the embedded form.
    r"/rows/\d+/clauses/\d+/(relation|status|note)",
    r"/rows/\d+/clauses/\d+/(lean_clause|source_clause)",
    r"/results/\d+/(notes|note)",
)

#: Fields whose acceptance an edit can invalidate. Named back to the caller so
#: a reviewer knows a gate is now stale rather than discovering it later.
ACCEPTANCE_FIELDS = (
    "canonical_evidence_sha256",
    "source_fidelity_inventory_sha256",
    "completion_certification",
    "semantic_review_sweep",
    "boundary_review",
    "statement_pins",
)


class EditRefused(Exception):
    """The edit is outside what a browser may write."""


@dataclass
class EditResult:
    pointer: str
    before: Any
    after: Any
    path: Path
    digest_before: str
    digest_after: str
    acceptance_warnings: list[str]
    journal: Path

    def as_json(self, root: Path) -> dict[str, Any]:
        try:
            rel = self.path.relative_to(root).as_posix()
        except ValueError:
            rel = str(self.path)
        return {
            "ok": True,
            "path": rel,
            "pointer": self.pointer,
            "before": self.before,
            "after": self.after,
            "digest_before": self.digest_before,
            "digest_after": self.digest_after,
            "acceptance_warnings": self.acceptance_warnings,
            "journal": self.journal.name,
        }


def _tokens(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise EditRefused(f"pointer must start with '/': {pointer!r}")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def _resolve_parent(data: Any, tokens: list[str]) -> tuple[Any, str | int]:
    node = data
    for tok in tokens[:-1]:
        if isinstance(node, list):
            node = node[int(tok)]
        elif isinstance(node, dict):
            node = node[tok]
        else:
            raise EditRefused(f"pointer does not resolve: /{'/'.join(tokens)}")
    last = tokens[-1]
    if isinstance(node, list):
        return node, int(last)
    if isinstance(node, dict):
        return node, last
    raise EditRefused(f"pointer does not resolve to a container: /{'/'.join(tokens)}")


def check_writable(pointer: str) -> None:
    if not any(re.fullmatch(p, pointer) for p in WRITABLE):
        raise EditRefused(
            f"{pointer} is not a reviewer-writable field. Writable: "
            + ", ".join(WRITABLE)
        )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _acceptance_warnings(data: Any, pointer: str) -> list[str]:
    """Name any acceptance this document carries that the edit may invalidate."""
    found: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ACCEPTANCE_FIELDS:
                    found.append(f"{path}/{k}")
                walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node[:400]):
                walk(v, f"{path}/{i}")

    walk(data, "")
    if not found:
        return []
    return [
        "This document carries acceptance/pin fields that an annotation can invalidate: "
        + ", ".join(sorted({f.rsplit('/', 1)[-1] for f in found}))
        + ". Nothing was re-stamped -- re-run the relevant gate and re-accept deliberately."
    ]


def apply_edit(
    path: Path,
    pointer: str,
    value: Any,
    *,
    author: str = "browser",
    note: str = "",
    root: Path | None = None,
) -> EditResult:
    """Write one reviewer-writable field, journaling what changed."""
    check_writable(pointer)
    path = Path(path)
    if not path.is_file():
        raise EditRefused(f"no such document: {path}")

    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    tokens = _tokens(pointer)
    parent, key = _resolve_parent(data, tokens)
    try:
        before = parent[key]
    except (KeyError, IndexError) as ex:
        raise EditRefused(f"pointer does not exist in the document: {pointer}") from ex

    if type(before) is not type(value) and before is not None:
        raise EditRefused(
            f"{pointer} holds {type(before).__name__}; refusing to write {type(value).__name__}"
        )

    digest_before = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    warnings = _acceptance_warnings(data, pointer)

    parent[key] = value
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    # Write atomically beside the target so a crash cannot truncate a ledger.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

    digest_after = hashlib.sha256(text.encode("utf-8")).hexdigest()
    journal = _append_journal(
        path,
        {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "author": author,
            "document": path.name,
            "pointer": pointer,
            "before": before,
            "after": value,
            "note": note,
            "digest_before": digest_before,
            "digest_after": digest_after,
            "acceptance_warnings": warnings,
        },
    )
    return EditResult(pointer, before, value, path, digest_before, digest_after, warnings, journal)


def _append_journal(path: Path, record: dict[str, Any]) -> Path:
    journal = path.parent / JOURNAL_NAME
    with journal.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return journal


def read_journal(root: Path, limit: int = 200) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for journal in sorted(root.glob(f"**/{JOURNAL_NAME}")):
        if {".git", ".lake", "build"} & set(journal.relative_to(root).parts):
            continue
        for line in journal.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    out.sort(key=lambda r: str(r.get("at", "")), reverse=True)
    return out[:limit]

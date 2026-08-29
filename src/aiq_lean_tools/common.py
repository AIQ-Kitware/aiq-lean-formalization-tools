from __future__ import annotations

import copy
import json
import os
import pathlib
import tempfile
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, MutableMapping, Sequence

from .errors import ValidationError

Path = pathlib.Path


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str
    location: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "location": self.location,
        }


def find_workspace_root(start: str | os.PathLike[str] | None = None) -> Path:
    """Find the nearest repository/Lake root without requiring Git."""
    path = Path(start or os.getcwd()).expanduser().resolve()
    if path.is_file():
        path = path.parent
    markers = ("lakefile.toml", "lakefile.lean", "formalization.yaml", ".git")
    for candidate in (path, *path.parents):
        if any((candidate / marker).exists() for marker in markers):
            return candidate
    return path


def infer_artifact_root(path: Path) -> Path:
    """Infer the repo root for a census/review file.

    The historic tools put generated audit state in ``dev/``.  Prefer a real
    repository marker, then fall back to the parent of common metadata dirs.
    """
    resolved = path.expanduser().resolve()
    for candidate in (resolved.parent, *resolved.parents):
        if any((candidate / marker).exists() for marker in ("lakefile.toml", "lakefile.lean", "formalization.yaml", ".git")):
            return candidate
    if resolved.parent.name in {"dev", "audit", "audits", "tracking"}:
        return resolved.parent.parent
    return resolved.parent


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as ex:
        raise ValidationError(f"file does not exist: {path}") from ex
    except json.JSONDecodeError as ex:
        raise ValidationError(f"invalid JSON in {path}: {ex}") from ex


def atomic_write_text(path: Path, text: str) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode if path.exists() else None
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(text)
        tmp = Path(handle.name)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def clone_json(data: Any) -> Any:
    return copy.deepcopy(data)


def dotted_parts(expr: str) -> list[str | int]:
    """Parse ``a.b.0.c`` into mapping/list path components."""
    out: list[str | int] = []
    for token in expr.split("."):
        if not token:
            raise ValidationError(f"empty component in field path {expr!r}")
        out.append(int(token) if token.isdigit() else token)
    return out


def dotted_get(data: Any, expr: str) -> Any:
    cur = data
    for part in dotted_parts(expr):
        try:
            cur = cur[part]
        except (KeyError, IndexError, TypeError) as ex:
            raise ValidationError(f"field path does not exist: {expr}") from ex
    return cur


def dotted_set(data: Any, expr: str, value: Any, *, create: bool = True) -> None:
    parts = dotted_parts(expr)
    cur = data
    for idx, part in enumerate(parts[:-1]):
        next_part = parts[idx + 1]
        container_factory = list if isinstance(next_part, int) else dict
        if isinstance(part, int):
            if not isinstance(cur, list):
                raise ValidationError(f"cannot traverse field path {expr!r}")
            if part >= len(cur):
                if not create:
                    raise ValidationError(f"field path does not exist: {expr}")
                cur.extend([None] * (part + 1 - len(cur)))
            if cur[part] is None and create:
                cur[part] = container_factory()
            cur = cur[part]
        else:
            if not isinstance(cur, MutableMapping):
                raise ValidationError(f"cannot traverse field path {expr!r}")
            if part not in cur or (cur[part] is None and create):
                if not create:
                    raise ValidationError(f"field path does not exist: {expr}")
                cur[part] = container_factory()
            cur = cur[part]
    last = parts[-1]
    if isinstance(last, int):
        if not isinstance(cur, list):
            raise ValidationError(f"cannot set field path {expr!r}")
        if last >= len(cur):
            if not create:
                raise ValidationError(f"cannot set field path {expr!r}")
            cur.extend([None] * (last + 1 - len(cur)))
        cur[last] = value
    else:
        if not isinstance(cur, MutableMapping):
            raise ValidationError(f"cannot set field path {expr!r}")
        cur[last] = value


def dotted_delete(data: Any, expr: str) -> None:
    parts = dotted_parts(expr)
    cur = data
    for part in parts[:-1]:
        try:
            cur = cur[part]
        except (KeyError, IndexError, TypeError) as ex:
            raise ValidationError(f"field path does not exist: {expr}") from ex
    last = parts[-1]
    try:
        if isinstance(last, int):
            del cur[last]
        else:
            del cur[last]
    except (KeyError, IndexError, TypeError) as ex:
        raise ValidationError(f"field path does not exist: {expr}") from ex


def parse_scalar(text: str, *, force_json: bool = False) -> Any:
    if force_json:
        try:
            return json.loads(text)
        except json.JSONDecodeError as ex:
            raise ValidationError(f"invalid JSON value {text!r}: {ex}") from ex
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if text.startswith(("[", "{", '"')):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


def md_escape(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def compact_counter(mapping: Mapping[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in mapping.items() if v)


def unique_in_order(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))

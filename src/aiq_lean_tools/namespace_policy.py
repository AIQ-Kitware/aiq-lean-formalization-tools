"""Configurable namespace-placement checks for Lean source modules."""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
from typing import Any

import yaml

from .errors import ValidationError
from .lean_source import lean_files, strip_comments

NAMESPACE_RE = re.compile(r"(?m)^\s*namespace\s+([A-Za-z0-9_'.₀-₉⁰-⁹′]+)\s*$")
END_RE = re.compile(r"(?m)^\s*end(?:\s+([A-Za-z0-9_'.₀-₉⁰-⁹′]+))?\s*$")
SECTION_RE = re.compile(r"(?m)^\s*section(?:\s+([A-Za-z0-9_'.₀-₉⁰-⁹′]+))?\s*$")


@dataclass(frozen=True)
class NamespaceViolation:
    rule: str
    module: str
    namespace: str
    path: Path
    detail: str

    def to_json(self, root: Path | None = None) -> dict[str, Any]:
        path = self.path
        if root is not None:
            try:
                path = path.relative_to(root)
            except ValueError:
                pass
        return {
            "rule": self.rule,
            "module": self.module,
            "namespace": self.namespace,
            "path": path.as_posix(),
            "detail": self.detail,
        }


def _match(value: str, pattern: str) -> bool:
    if any(ch in pattern for ch in "*?["):
        return fnmatchcase(value, pattern)
    return value == pattern or value.startswith(pattern + ".")


def declared_namespaces(text: str) -> list[str]:
    """Return fully-qualified namespaces opened in a file, comments removed."""
    clean = strip_comments(text)
    events: list[tuple[int, str, str | None]] = []
    for m in NAMESPACE_RE.finditer(clean): events.append((m.start(), "namespace", m.group(1)))
    for m in SECTION_RE.finditer(clean): events.append((m.start(), "section", m.group(1)))
    for m in END_RE.finditer(clean): events.append((m.start(), "end", m.group(1)))
    stack: list[tuple[str, str | None]] = []
    out: list[str] = []
    for _, kind, name in sorted(events):
        if kind == "namespace":
            stack.append((kind, name))
            parts = [n for k, n in stack if k == "namespace" and n]
            full = ".".join(parts)
            if full:
                out.append(full)
        elif kind == "section":
            stack.append((kind, name))
        elif stack:
            if name is None:
                stack.pop()
            else:
                for idx in range(len(stack) - 1, -1, -1):
                    if stack[idx][1] == name:
                        del stack[idx:]
                        break
    return list(dict.fromkeys(out))


def load_namespace_policy(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    text = p.read_text(encoding="utf-8")
    data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValidationError("namespace policy requires a 'rules' list")
    for raw in data["rules"]:
        if not isinstance(raw, dict) or not raw.get("id"):
            raise ValidationError("every namespace rule requires an id")
        modules = raw.get("modules", [])
        allow = raw.get("allow", [])
        if isinstance(modules, str): modules = [modules]
        if isinstance(allow, str): allow = [allow]
        if not modules or not allow:
            raise ValidationError(f"namespace rule {raw.get('id')!r} requires modules and allow")
        raw["modules"] = modules
        raw["allow"] = allow
    return data


def check_namespace_policy(root: str | Path, policy: dict[str, Any]) -> list[NamespaceViolation]:
    base = Path(root).expanduser().resolve()
    violations: list[NamespaceViolation] = []
    for path in lean_files(base):
        module = ".".join(path.relative_to(base).with_suffix("").parts)
        for raw in policy["rules"]:
            if not any(_match(module, pat) for pat in raw["modules"]):
                continue
            allow = raw["allow"]
            for namespace in declared_namespaces(path.read_text(encoding="utf-8", errors="replace")):
                if not any(_match(namespace, pat) for pat in allow):
                    violations.append(NamespaceViolation(
                        rule=str(raw["id"]),
                        module=module,
                        namespace=namespace,
                        path=path,
                        detail=str(raw.get("message") or f"namespace {namespace} is outside the allowed namespace set"),
                    ))
    return violations

"""Mockable boundary around Lake/Lean subprocess execution."""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from .common import Path
from .errors import LeanExecutionError

BEGIN = "AIQ_LEAN_PROBE_BEGIN|"
END = "AIQ_LEAN_PROBE_END|"
#: A name that must never resolve.  A probe whose parser cannot fail proves
#: nothing, and the failure is silent: every declaration reads as resolved and
#: the census reports full coverage.  One extra query per run rules that out.
CANARY = "AiqLeanTools.ProbeCanary.MustNotResolve"


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined(self) -> str:
        if self.stdout and self.stderr:
            return self.stdout + "\n" + self.stderr
        return self.stdout or self.stderr


@dataclass(frozen=True)
class DeclarationProbe:
    name: str
    resolved: bool
    output: str = ""


@dataclass(frozen=True)
class LeanQueryProbe:
    mode: str
    name: str
    resolved: bool
    output: str = ""


class LeanBackend(Protocol):
    def run(self, root: Path, argv: Sequence[str], *, timeout: int = 3600) -> CommandResult:
        ...

    def probe_queries(
        self,
        root: Path,
        queries: Sequence[tuple[str, str]],
        imports: Sequence[str],
        *,
        timeout: int = 3600,
    ) -> list[LeanQueryProbe]:
        ...

    def probe_declarations(
        self,
        root: Path,
        declarations: Sequence[str],
        imports: Sequence[str],
        *,
        timeout: int = 3600,
    ) -> list[DeclarationProbe]:
        ...


class SubprocessLeanBackend:
    """Real Lake/Lean backend.  All external calls pass through this object."""

    def __init__(self, *, env: Mapping[str, str] | None = None) -> None:
        self.env = dict(env or {})

    def run(self, root: Path, argv: Sequence[str], *, timeout: int = 3600) -> CommandResult:
        env = {**os.environ, **self.env}
        try:
            proc = subprocess.run(
                list(argv), cwd=root, text=True, capture_output=True, timeout=timeout, env=env
            )
        except FileNotFoundError as ex:
            raise LeanExecutionError(f"command not found: {argv[0]}") from ex
        except subprocess.TimeoutExpired as ex:
            raise LeanExecutionError(f"command timed out after {timeout}s: {' '.join(argv)}") from ex
        return CommandResult(tuple(argv), proc.returncode, proc.stdout or "", proc.stderr or "")

    def probe_queries(
        self,
        root: Path,
        queries: Sequence[tuple[str, str]],
        imports: Sequence[str],
        *,
        timeout: int = 3600,
    ) -> list[LeanQueryProbe]:
        build = root / "build" / "aiq-lean-tools"
        build.mkdir(parents=True, exist_ok=True)
        probed = [*queries, ("check", CANARY)]
        lines = [*(f"import {module}" for module in imports), "", "-- generated compiler probe"]
        for index, (mode, name) in enumerate(probed):
            if mode not in {"check", "print"}:
                raise LeanExecutionError(f"unknown probe mode {mode!r}; expected check or print")
            command = f"#check @{name}" if mode == "check" else f"#print {name}"
            lines.extend([
                f'#eval IO.println "{BEGIN}{index}"',
                command,
                f'#eval IO.println "{END}{index}"',
                "",
            ])
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".lean", dir=build, delete=False
        ) as handle:
            handle.write("\n".join(lines) + "\n")
            probe_path = Path(handle.name)
        try:
            rel = probe_path.relative_to(root)
            result = self.run(root, ["lake", "env", "lean", "-DmaxErrors=100000", str(rel)], timeout=timeout)
            rows = _parse_query_probe(result.combined, probed)
            if rows[-1].resolved:
                raise LeanExecutionError(
                    "the probe canary resolved: the diagnostic parser is broken and "
                    "every result in this run is meaningless"
                )
            return rows[:-1]
        finally:
            probe_path.unlink(missing_ok=True)

    def probe_declarations(
        self,
        root: Path,
        declarations: Sequence[str],
        imports: Sequence[str],
        *,
        timeout: int = 3600,
    ) -> list[DeclarationProbe]:
        rows = self.probe_queries(root, [("check", name) for name in declarations], imports, timeout=timeout)
        return [DeclarationProbe(row.name, row.resolved, row.output) for row in rows]


class MockLeanBackend:
    """Deterministic backend for unit tests and non-Lean consumers.

    ``resolved`` maps declaration names to either ``True``/``False`` or a string
    containing the synthetic compiler output returned for that declaration.
    """

    def __init__(self, resolved: Mapping[str, bool | str] | None = None) -> None:
        self.resolved = dict(resolved or {})
        self.calls: list[tuple] = []

    def run(self, root: Path, argv: Sequence[str], *, timeout: int = 3600) -> CommandResult:
        self.calls.append(("run", root, tuple(argv), timeout))
        return CommandResult(tuple(argv), 0, "", "")

    def probe_queries(
        self,
        root: Path,
        queries: Sequence[tuple[str, str]],
        imports: Sequence[str],
        *,
        timeout: int = 3600,
    ) -> list[LeanQueryProbe]:
        self.calls.append(("probe_queries", root, tuple(queries), tuple(imports), timeout))
        out = []
        for mode, name in queries:
            value = self.resolved.get(f"{mode}:{name}", self.resolved.get(name, False))
            out.append(LeanQueryProbe(mode, name, bool(value), value if isinstance(value, str) else ""))
        return out

    def probe_declarations(
        self,
        root: Path,
        declarations: Sequence[str],
        imports: Sequence[str],
        *,
        timeout: int = 3600,
    ) -> list[DeclarationProbe]:
        rows = self.probe_queries(root, [("check", name) for name in declarations], imports, timeout=timeout)
        return [DeclarationProbe(row.name, row.resolved, row.output) for row in rows]


def _parse_query_probe(text: str, queries: Sequence[tuple[str, str]]) -> list[LeanQueryProbe]:
    blocks: dict[int, list[str]] = {}
    current: int | None = None
    for line in text.splitlines():
        if BEGIN in line:
            try:
                current = int(line.split(BEGIN, 1)[1].split()[0])
                blocks[current] = []
            except ValueError:
                current = None
            continue
        if END in line:
            current = None
            continue
        if current is not None:
            blocks[current].append(line)
    rows: list[LeanQueryProbe] = []
    for index, (mode, name) in enumerate(queries):
        raw = "\n".join(blocks.get(index, [])).strip()
        # Lean still emits the marker after a failed query, so success is about
        # the diagnostic inside the bracketed block rather than process status.
        resolved = bool(raw) and not re.search(r"(^|\n).*\berror(?:\(|:)", raw, re.I)
        rows.append(LeanQueryProbe(mode, name, resolved, raw))
    return rows

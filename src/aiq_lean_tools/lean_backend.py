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
#: How a probe emits the markers that bracket one query's output.
#:
#: `#check` on a string literal, not `#eval IO.println`.  Both put the marker in
#: the output, but `#eval` compiles and runs a term through the interpreter,
#: while `#check` only elaborates one literal.  A probe emits two markers per
#: query, so the difference is the dominant cost of every probe this package
#: runs: on the Davis--Kahan census -- 1212 declarations, 2424 markers -- the
#: `#eval` form takes 91s and this one takes 20s against the same build.
#:
#: It is also the more reliable of the two.  `#eval` writes to the process's
#: stdout directly while `#check` goes through Lean's message log, so the old
#: form interleaved two streams and relied on them staying in step; now the
#: markers and the output they bracket are messages in one ordered log.
_MARKER_COMMAND = '#check "{marker}{index}"'
#: The marker's index, read back out of a line that carries it.  The line is
#: `"AIQ_LEAN_PROBE_BEGIN|7" : String`, so the digits are followed by the
#: literal's closing quote rather than by whitespace.
_MARKER_INDEX_RE = re.compile(r"(\d+)")
#: A name that must never resolve.  A probe whose parser cannot fail proves
#: nothing, and the failure is silent: every declaration reads as resolved and
#: the census reports full coverage.  One extra query per run rules that out.
CANARY = "AiqLeanTools.ProbeCanary.MustNotResolve"
#: What each probe mode asks Lean.  `check_pp_all` exists because `#check` alone
#: alpha-normalizes universe parameters and hides the instance telescope, and an
#: exporter that compares declarations sees both.
_PROBE_COMMANDS = {
    "check": lambda name: f"#check @{name}",
    "print": lambda name: f"#print {name}",
    "check_pp_all": lambda name: f"set_option pp.all true in\n#check @{name}",
}


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
            if mode not in _PROBE_COMMANDS:
                raise LeanExecutionError(
                    f"unknown probe mode {mode!r}; expected one of "
                    + ", ".join(sorted(_PROBE_COMMANDS))
                )
            command = _PROBE_COMMANDS[mode](name)
            lines.extend([
                _MARKER_COMMAND.format(marker=BEGIN, index=index),
                command,
                _MARKER_COMMAND.format(marker=END, index=index),
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
            match = _MARKER_INDEX_RE.search(line.split(BEGIN, 1)[1])
            if match is None:
                current = None
            else:
                current = int(match.group(1))
                blocks[current] = []
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

"""Discover and run repository check scripts without duplicating their CLI policy."""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import yaml

from .common import Path
from .errors import FormalizationToolsError

CHECK_FLAG_RE = re.compile(r"add_argument\(\s*[\"']--check[\"']")
SOFT_CHECK_RE = re.compile(r"(?:return\s+|SystemExit\(\s*)1\s+if\s+[A-Za-z_]\w*\.check\s+else\s+0")


@dataclass(frozen=True)
class CommandGate:
    """A gate the repository declares outright instead of shipping a script for.

    Once the generic engines live in a package, most of a repository's gates are
    invocations of installed commands with project policy files.  Discovery by
    filename cannot see those, and a runner that only sees scripts pushes a
    project back into writing wrapper scripts purely so the runner can find them.
    """

    name: str
    command: tuple[str, ...]
    description: str = ""
    slow: bool = False


@dataclass(frozen=True)
class GateSuiteConfig:
    pattern: str = "scripts/check_*.py"
    slow: frozenset[str] = frozenset()
    check_is_stronger: Mapping[str, str] = field(default_factory=dict)
    advisory: Mapping[str, str] = field(default_factory=dict)
    unavailable_exit_code: int = 3
    commands: tuple[CommandGate, ...] = ()

    @classmethod
    def load(cls, path: str | pathlib.Path | None) -> "GateSuiteConfig":
        if path is None:
            return cls()
        source = Path(path).expanduser()
        data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise FormalizationToolsError(f"gate config must be a mapping: {source}")
        slow = data.get("slow", [])
        stronger = data.get("check_is_stronger", {})
        advisory = data.get("advisory", {})
        if not isinstance(slow, list) or not all(isinstance(x, str) for x in slow):
            raise FormalizationToolsError("gate config slow must be a list of gate names")
        if not isinstance(stronger, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in stronger.items()):
            raise FormalizationToolsError("gate config check_is_stronger must map gate names to reasons")
        if not isinstance(advisory, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in advisory.items()):
            raise FormalizationToolsError("gate config advisory must map gate names to reasons")
        commands = _load_command_gates(data.get("gates", []), frozenset(slow))
        return cls(
            pattern=str(data.get("pattern", "scripts/check_*.py")),
            slow=frozenset(slow),
            check_is_stronger=dict(stronger),
            advisory=dict(advisory),
            unavailable_exit_code=int(data.get("unavailable_exit_code", 3)),
            commands=commands,
        )


def _load_command_gates(raw: Any, slow_names: frozenset[str]) -> tuple[CommandGate, ...]:
    if not isinstance(raw, list):
        raise FormalizationToolsError("gate config 'gates' must be a list of gate objects")
    out: list[CommandGate] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise FormalizationToolsError("every declared gate must be an object")
        name = str(entry.get("name", "")).strip()
        command = entry.get("command")
        if not name:
            raise FormalizationToolsError("every declared gate requires a name")
        if name in seen:
            raise FormalizationToolsError(f"duplicate declared gate name: {name}")
        if isinstance(command, str) or not isinstance(command, list) or not command:
            raise FormalizationToolsError(
                f"gate {name!r} requires 'command' as a nonempty argv list, not a shell string"
            )
        if not all(isinstance(part, str) for part in command):
            raise FormalizationToolsError(f"gate {name!r} command entries must all be strings")
        seen.add(name)
        out.append(CommandGate(
            name=name,
            command=tuple(command),
            description=str(entry.get("description", "")),
            slow=bool(entry.get("slow", False)) or name in slow_names,
        ))
    return tuple(out)


@dataclass(frozen=True)
class Gate:
    name: str
    accepts_check: bool
    soft: bool
    path: Path | None = None
    argv: tuple[str, ...] = ()
    check_is_stronger_reason: str | None = None
    advisory_reason: str | None = None
    slow: bool = False
    description: str = ""

    @property
    def declared(self) -> bool:
        """Was this gate declared as a command rather than discovered as a script?"""
        return self.path is None

    @property
    def pass_check(self) -> bool:
        return self.accepts_check and self.check_is_stronger_reason is None

    @property
    def kind(self) -> str:
        if self.declared:
            return "declared command" + (" (advisory)" if self.advisory_reason else "")
        if self.check_is_stronger_reason:
            return "strict; --check withheld because it is stronger than the regression gate"
        if self.advisory_reason:
            return "advisory"
        if self.soft:
            return "soft; made strict by --check"
        if self.accepts_check:
            return "strict; accepts --check"
        return "strict; no --check flag"

    def command(self, python: str = sys.executable) -> list[str]:
        if self.path is None:
            return list(self.argv)
        command = [python, str(self.path)]
        if self.pass_check:
            command.append("--check")
        return command

    def to_json(self, root: Path | None = None) -> dict[str, Any]:
        path = self.path
        if path is not None and root is not None:
            try:
                path = path.relative_to(root)
            except ValueError:
                pass
        return {
            "name": self.name,
            "path": path.as_posix() if path is not None else None,
            "command": list(self.argv) if self.declared else None,
            "description": self.description,
            "kind": self.kind,
            "accepts_check": self.accepts_check,
            "pass_check": self.pass_check,
            "soft": self.soft,
            "slow": self.slow,
            "advisory_reason": self.advisory_reason,
            "check_is_stronger_reason": self.check_is_stronger_reason,
        }


@dataclass(frozen=True)
class GateResult:
    gate: Gate
    status: str
    returncode: int
    elapsed: float
    output: str

    def to_json(self, root: Path | None = None, *, tail_lines: int | None = None) -> dict[str, Any]:
        output = self.output
        if tail_lines is not None:
            output = "\n".join(output.splitlines()[-tail_lines:])
        return {
            "gate": self.gate.to_json(root),
            "status": self.status,
            "returncode": self.returncode,
            "elapsed_seconds": round(self.elapsed, 6),
            "output": output,
        }


def discover_gates(root: str | pathlib.Path, config: GateSuiteConfig | None = None) -> list[Gate]:
    base = Path(root).expanduser().resolve()
    config = config or GateSuiteConfig()
    gates: list[Gate] = []
    if config.pattern:
        paths = sorted(path for path in base.glob(config.pattern) if path.is_file())
        for path in paths:
            source = path.read_text(encoding="utf-8", errors="replace")
            name = path.stem
            gates.append(
                Gate(
                    path=path,
                    name=name,
                    accepts_check=bool(CHECK_FLAG_RE.search(source)),
                    soft=bool(SOFT_CHECK_RE.search(source)),
                    check_is_stronger_reason=config.check_is_stronger.get(name),
                    advisory_reason=config.advisory.get(name),
                    slow=name in config.slow,
                )
            )
    for declared in config.commands:
        gates.append(
            Gate(
                path=None,
                argv=declared.command,
                name=declared.name,
                accepts_check=False,
                soft=False,
                advisory_reason=config.advisory.get(declared.name),
                slow=declared.slow,
                description=declared.description,
            )
        )
    return sorted(gates, key=lambda gate: gate.name)


def run_gate_suite(
    root: str | pathlib.Path,
    *,
    config: GateSuiteConfig | None = None,
    filter_text: str = "",
    fast: bool = False,
    timeout: int = 3600,
    python: str = sys.executable,
) -> tuple[list[GateResult], list[Gate]]:
    base = Path(root).expanduser().resolve()
    config = config or GateSuiteConfig()
    selected = [gate for gate in discover_gates(base, config) if filter_text in gate.name]
    skipped = [gate for gate in selected if fast and gate.slow]
    results: list[GateResult] = []
    for gate in selected:
        if gate in skipped:
            continue
        started = time.monotonic()
        try:
            done = subprocess.run(
                gate.command(python),
                cwd=base,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            returncode = done.returncode
            output = done.stdout + done.stderr
        except subprocess.TimeoutExpired as ex:
            returncode = 124
            stdout = ex.stdout.decode() if isinstance(ex.stdout, bytes) else (ex.stdout or "")
            stderr = ex.stderr.decode() if isinstance(ex.stderr, bytes) else (ex.stderr or "")
            output = stdout + stderr + f"\ntimed out after {timeout}s\n"
        elapsed = time.monotonic() - started
        if returncode == 0:
            status = "passed"
        elif returncode == config.unavailable_exit_code:
            status = "unavailable"
        elif gate.advisory_reason:
            status = "advisory"
        else:
            status = "failed"
        results.append(GateResult(gate, status, returncode, elapsed, output))
    return results, skipped

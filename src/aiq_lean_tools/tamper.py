"""Verify that a repository's gates actually reject the bugs they were written for.

A gate suite is only evidence if its gates *fail* on bad input.  Nothing in a
green run distinguishes a check that inspects the registration from a check that
reports success unconditionally, and both of those have shipped here: a census
row can be retargeted at the wrong declaration, a hypothesis can be dropped from
a source-exact facade, and a checker that stopped looking at the field will say
nothing.

A tamper test closes that hole from the other side.  It applies one textual
mutation corresponding to a real defect, runs the narrowest gate that is supposed
to catch it, and passes only when that gate fails.  Each command is first run on
the unmutated tree, because a gate that fails for an unrelated reason would
otherwise "detect" every mutation put to it.

The mutations are repository policy and live in the repository; what lives here
is the engine: parse, apply, run, restore, and refuse to leave the tree dirty.
"""
from __future__ import annotations

import pathlib
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import yaml

from .common import Path
from .errors import FormalizationToolsError


@dataclass(frozen=True)
class Mutation:
    """One textual defect, and the gate that must reject it."""

    name: str
    path: str
    find: str
    replace: str
    command: tuple[str, ...]
    description: str = ""
    occurrences: int = 1

    @classmethod
    def parse(cls, raw: Any, index: int) -> "Mutation":
        where = f"mutation {index}"
        if not isinstance(raw, Mapping):
            raise FormalizationToolsError(f"{where}: each mutation must be a mapping")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise FormalizationToolsError(f"{where}: needs a non-empty name")
        where = f"mutation {name!r}"
        path = raw.get("file")
        if not isinstance(path, str) or not path:
            raise FormalizationToolsError(f"{where}: needs a file")
        find = raw.get("find")
        replace = raw.get("replace")
        if not isinstance(find, str) or not find:
            raise FormalizationToolsError(f"{where}: needs a non-empty find")
        if not isinstance(replace, str):
            raise FormalizationToolsError(f"{where}: needs a replace string")
        if find == replace:
            raise FormalizationToolsError(f"{where}: find and replace are identical, so nothing is tampered with")
        command = raw.get("detected_by")
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise FormalizationToolsError(f"{where}: detected_by must be a non-empty list of strings")
        occurrences = raw.get("occurrences", 1)
        if not isinstance(occurrences, int) or occurrences < 1:
            raise FormalizationToolsError(f"{where}: occurrences must be a positive integer")
        description = raw.get("description", "")
        if not isinstance(description, str):
            raise FormalizationToolsError(f"{where}: description must be a string")
        return cls(
            name=name,
            path=path,
            find=find,
            replace=replace,
            command=tuple(command),
            description=description,
            occurrences=occurrences,
        )


@dataclass(frozen=True)
class TamperConfig:
    mutations: tuple[Mutation, ...]
    description: str = ""

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "TamperConfig":
        source = Path(path).expanduser()
        if not source.is_file():
            raise FormalizationToolsError(f"tamper config not found: {source}")
        data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        if not isinstance(data, Mapping):
            raise FormalizationToolsError(f"tamper config must be a mapping: {source}")
        raw = data.get("mutations")
        if not isinstance(raw, list) or not raw:
            raise FormalizationToolsError(f"tamper config needs a non-empty mutations list: {source}")
        mutations = tuple(Mutation.parse(item, i) for i, item in enumerate(raw))
        seen: set[str] = set()
        for mutation in mutations:
            if mutation.name in seen:
                raise FormalizationToolsError(f"duplicate mutation name: {mutation.name}")
            seen.add(mutation.name)
        description = data.get("description", "")
        if not isinstance(description, str):
            raise FormalizationToolsError("tamper config description must be a string")
        return cls(mutations=mutations, description=description)


@dataclass(frozen=True)
class MutationResult:
    name: str
    status: str
    detail: str
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "detected"


@dataclass(frozen=True)
class TamperReport:
    baselines: tuple[tuple[str, int], ...]
    results: tuple[MutationResult, ...]
    restored: bool

    @property
    def undetected(self) -> tuple[MutationResult, ...]:
        return tuple(r for r in self.results if r.status == "undetected")

    @property
    def errors(self) -> tuple[MutationResult, ...]:
        return tuple(r for r in self.results if r.status not in {"detected", "undetected"})

    @property
    def ok(self) -> bool:
        return self.restored and not self.undetected and not self.errors and all(
            code == 0 for _, code in self.baselines
        )


def _run(command: Sequence[str], root: pathlib.Path, timeout: int) -> int:
    try:
        completed = subprocess.run(
            list(command), cwd=str(root), capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise FormalizationToolsError(f"command not found: {command[0]} ({exc})") from exc
    except subprocess.TimeoutExpired:
        return 124
    return completed.returncode


def _tree_is_clean(root: pathlib.Path) -> bool:
    completed = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(root), capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise FormalizationToolsError("tamper testing needs a git worktree to verify restoration")
    return completed.stdout.strip() == ""


def run_tamper_suite(
    config: TamperConfig,
    root: str | pathlib.Path = ".",
    *,
    only: Sequence[str] = (),
    timeout: int = 1800,
    allow_dirty: bool = False,
) -> TamperReport:
    """Apply each mutation in turn and require the named gate to reject it.

    The tree is restored from the bytes read before the mutation, in a `finally`,
    and the whole run reports `restored=False` if anything is left modified --
    a tamper harness that can corrupt the repository it is checking would be
    worse than no harness at all.
    """
    base = Path(root).expanduser().resolve()
    if not allow_dirty and not _tree_is_clean(base):
        raise FormalizationToolsError(
            "working tree is dirty; commit or stash first so restoration is verifiable"
        )

    selected = config.mutations
    if only:
        wanted = set(only)
        unknown = wanted - {m.name for m in selected}
        if unknown:
            raise FormalizationToolsError(f"unknown mutation(s): {', '.join(sorted(unknown))}")
        selected = tuple(m for m in selected if m.name in wanted)

    baselines: list[tuple[str, int]] = []
    baseline_codes: dict[tuple[str, ...], int] = {}
    for mutation in selected:
        if mutation.command in baseline_codes:
            continue
        code = _run(mutation.command, base, timeout)
        baseline_codes[mutation.command] = code
        baselines.append((" ".join(mutation.command), code))

    results: list[MutationResult] = []
    for mutation in selected:
        target = base / mutation.path
        label = " ".join(mutation.command)
        if not target.is_file():
            results.append(MutationResult(mutation.name, "error", f"no such file: {mutation.path}"))
            continue
        if baseline_codes[mutation.command] != 0:
            results.append(
                MutationResult(
                    mutation.name,
                    "error",
                    f"gate already fails on the clean tree, so it proves nothing: {label}",
                )
            )
            continue
        original = target.read_bytes()
        text = original.decode("utf-8")
        found = text.count(mutation.find)
        if found != mutation.occurrences:
            results.append(
                MutationResult(
                    mutation.name,
                    "error",
                    f"expected {mutation.occurrences} occurrence(s) of the find text in "
                    f"{mutation.path}, found {found}",
                )
            )
            continue
        started = time.monotonic()
        try:
            target.write_text(text.replace(mutation.find, mutation.replace), encoding="utf-8")
            code = _run(mutation.command, base, timeout)
        finally:
            target.write_bytes(original)
        elapsed = time.monotonic() - started
        if code == 0:
            results.append(
                MutationResult(
                    mutation.name,
                    "undetected",
                    f"{label} still passed with the mutation applied",
                    elapsed,
                )
            )
        else:
            results.append(
                MutationResult(mutation.name, "detected", f"{label} exited {code}", elapsed)
            )

    restored = allow_dirty or _tree_is_clean(base)
    return TamperReport(tuple(baselines), tuple(results), restored)


def render_report(report: TamperReport) -> str:
    lines = ["tamper tests"]
    for label, code in report.baselines:
        state = "clean" if code == 0 else f"ALREADY FAILING (exit {code})"
        lines.append(f"  baseline {state}: {label}")
    for result in report.results:
        mark = {"detected": "ok", "undetected": "UNDETECTED", "error": "ERROR"}[result.status]
        lines.append(f"  [{mark}] {result.name}: {result.detail}")
    if not report.restored:
        lines.append("  RESTORATION FAILED: the working tree was left modified")
    detected = sum(1 for r in report.results if r.ok)
    lines.append(f"  {detected}/{len(report.results)} mutations rejected by their gate")
    return "\n".join(lines)

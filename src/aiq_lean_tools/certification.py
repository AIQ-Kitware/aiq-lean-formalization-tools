"""Build reproducible evidence bundles for a formalization checkpoint.

The bundle engine extracts the reusable mechanics of the DKPS Davis--Kahan
certificate: snapshot declared inputs, hash a source tree, record Git state and
tool versions, run named verification commands with complete logs, and write a
self-describing manifest plus ``SHA256SUMS``.  Which commands and artifacts
constitute scientific completion remains project policy in a YAML/JSON plan.
"""
from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

import yaml

from .common import Finding, Path, atomic_write_json, atomic_write_text, find_workspace_root
from .errors import ValidationError
from .lean_backend import CommandResult


def _strings(value: Any, name: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValidationError(f"{name} must be a list of strings")
    out = tuple(str(x).strip() for x in value if isinstance(x, str) and x.strip())
    if len(out) != len(value) or (not allow_empty and not out):
        raise ValidationError(f"{name} must contain only non-empty strings")
    return out


@dataclass(frozen=True)
class CertificationCommand:
    name: str
    argv: tuple[str, ...]
    required: bool = True
    timeout: int = 3600

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, index: int) -> "CertificationCommand":
        name = data.get("name")
        argv = data.get("argv", data.get("command"))
        if not isinstance(name, str) or not name.strip():
            raise ValidationError(f"commands[{index}] requires name")
        if isinstance(argv, str):
            # Deliberately no shell splitting: plans must make argument boundaries explicit.
            raise ValidationError(f"commands[{index}].argv must be a list, not a shell string")
        args = _strings(argv, f"commands[{index}].argv", allow_empty=False)
        timeout = data.get("timeout", 3600)
        if not isinstance(timeout, int) or timeout <= 0:
            raise ValidationError(f"commands[{index}].timeout must be a positive integer")
        return cls(name.strip(), args, bool(data.get("required", True)), timeout)


@dataclass(frozen=True)
class ToolVersionProbe:
    name: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class CertificationPlan:
    name: str
    inputs: tuple[str, ...]
    source_include: tuple[str, ...]
    source_exclude_parts: tuple[str, ...]
    commands: tuple[CertificationCommand, ...]
    tool_versions: tuple[ToolVersionProbe, ...]
    require_clean_git: bool = False
    require_input_matches: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CertificationPlan":
        name = str(data.get("name") or "Formalization certification")
        inputs = _strings(data.get("inputs", ["formalization.yaml"]), "inputs")
        source = data.get("source_tree", {}) or {}
        if not isinstance(source, Mapping):
            raise ValidationError("source_tree must be a mapping")
        include = _strings(source.get("include", ["**/*.lean"]), "source_tree.include", allow_empty=False)
        exclude = _strings(
            source.get("exclude_parts", [".git", ".lake", "build", "dist", "vendor", "external"]),
            "source_tree.exclude_parts",
        )
        raw_commands = data.get("commands", [])
        if not isinstance(raw_commands, list):
            raise ValidationError("commands must be a list")
        commands = []
        for index, row in enumerate(raw_commands):
            if not isinstance(row, Mapping):
                raise ValidationError(f"commands[{index}] must be a mapping")
            commands.append(CertificationCommand.from_mapping(row, index=index))
        raw_tools = data.get("tool_versions", {}) or {}
        tools: list[ToolVersionProbe] = []
        if isinstance(raw_tools, Mapping):
            for label, argv in raw_tools.items():
                tools.append(ToolVersionProbe(str(label), _strings(argv, f"tool_versions.{label}", allow_empty=False)))
        elif isinstance(raw_tools, list):
            for index, row in enumerate(raw_tools):
                if not isinstance(row, Mapping) or not isinstance(row.get("name"), str):
                    raise ValidationError(f"tool_versions[{index}] must have name and argv")
                tools.append(ToolVersionProbe(row["name"], _strings(row.get("argv"), f"tool_versions[{index}].argv", allow_empty=False)))
        else:
            raise ValidationError("tool_versions must be a mapping or list")
        return cls(
            name=name,
            inputs=inputs,
            source_include=include,
            source_exclude_parts=exclude,
            commands=tuple(commands),
            tool_versions=tuple(tools),
            require_clean_git=bool(data.get("require_clean_git", False)),
            require_input_matches=bool(data.get("require_input_matches", True)),
        )

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "CertificationPlan":
        p = Path(path).expanduser().resolve()
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError as ex:
            raise ValidationError(f"certification plan does not exist: {p}") from ex
        try:
            data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
        except (json.JSONDecodeError, yaml.YAMLError) as ex:
            raise ValidationError(f"invalid certification plan {p}: {ex}") from ex
        if not isinstance(data, Mapping):
            raise ValidationError("certification plan root must be a mapping")
        return cls.from_mapping(data)


class CommandRunner(Protocol):
    def run(self, root: Path, argv: Sequence[str], *, timeout: int = 3600) -> CommandResult:
        ...


class SubprocessCommandRunner:
    def __init__(self, *, env: Mapping[str, str] | None = None) -> None:
        self.env = dict(env or {})

    def run(self, root: Path, argv: Sequence[str], *, timeout: int = 3600) -> CommandResult:
        env = {**os.environ, **self.env}
        try:
            proc = subprocess.run(list(argv), cwd=root, text=True, capture_output=True, timeout=timeout, env=env)
        except FileNotFoundError:
            return CommandResult(tuple(argv), 127, "", f"command not found: {argv[0]}")
        except subprocess.TimeoutExpired as ex:
            stdout = ex.stdout.decode() if isinstance(ex.stdout, bytes) else (ex.stdout or "")
            stderr = ex.stderr.decode() if isinstance(ex.stderr, bytes) else (ex.stderr or "")
            return CommandResult(tuple(argv), 124, stdout, stderr + f"\ncommand timed out after {timeout}s")
        return CommandResult(tuple(argv), proc.returncode, proc.stdout or "", proc.stderr or "")


@dataclass(frozen=True)
class CertificationReport:
    output_dir: Path
    metadata: Mapping[str, Any]
    findings: tuple[Finding, ...]

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "output_dir": str(self.output_dir),
            "findings": [f.to_json() for f in self.findings],
            "metadata": dict(self.metadata),
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 127, ""
    return proc.returncode, (proc.stdout or proc.stderr or "").strip()


def git_snapshot(root: Path) -> dict[str, Any]:
    code, head = _git(root, "rev-parse", "HEAD")
    if code != 0:
        return {"available": False}
    _, branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _, status = _git(root, "status", "--porcelain=v1")
    _, submodules = _git(root, "submodule", "status", "--recursive")
    return {
        "available": True,
        "commit": head,
        "branch": branch,
        "dirty": bool(status.strip()),
        "status_porcelain": status.splitlines() if status else [],
        "submodules": submodules.splitlines() if submodules else [],
    }


def _expand_patterns(root: Path, patterns: Sequence[str], *, exclude_parts: Sequence[str] = ()) -> list[Path]:
    out: set[Path] = set()
    excluded = set(exclude_parts)
    for pattern in patterns:
        candidate = root / pattern
        if candidate.is_file():
            matches = [candidate]
        else:
            matches = [path for path in root.glob(pattern) if path.is_file()]
        for path in matches:
            rel = path.relative_to(root)
            if excluded.intersection(rel.parts):
                continue
            out.add(path.resolve())
    return sorted(out, key=lambda path: path.relative_to(root).as_posix())


def source_tree_hash(root: Path, paths: Sequence[Path]) -> tuple[str, list[dict[str, str]]]:
    digest = hashlib.sha256()
    rows: list[dict[str, str]] = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        file_hash = sha256_file(path)
        rows.append({"path": rel, "sha256": file_hash})
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), rows


def _safe_slug(text: str) -> str:
    value = "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return value or "command"


def _copy_snapshot(root: Path, paths: Sequence[Path], destination: Path) -> list[dict[str, str]]:
    rows = []
    for path in paths:
        rel = path.relative_to(root)
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        rows.append({"path": rel.as_posix(), "sha256": sha256_file(path)})
    return rows


def build_certification_bundle(
    plan: CertificationPlan,
    *,
    root: str | pathlib.Path | None = None,
    output_dir: str | pathlib.Path,
    runner: CommandRunner | None = None,
    overwrite: bool = False,
) -> CertificationReport:
    base = find_workspace_root(root)
    out = Path(output_dir).expanduser().resolve()
    findings: list[Finding] = []
    if out.exists():
        if not overwrite:
            raise ValidationError(f"certification output already exists: {out}")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "logs").mkdir()

    git = git_snapshot(base)
    if plan.require_clean_git and git.get("available") and git.get("dirty"):
        findings.append(Finding("error", "certification-dirty-git", "certification plan requires a clean Git worktree"))

    input_paths = _expand_patterns(base, plan.inputs)
    if plan.require_input_matches:
        for pattern in plan.inputs:
            if not _expand_patterns(base, [pattern]):
                findings.append(Finding("error", "certification-input", f"input pattern matched no files: {pattern}", pattern))
    input_rows = _copy_snapshot(base, input_paths, out / "inputs")

    source_paths = _expand_patterns(base, plan.source_include, exclude_parts=plan.source_exclude_parts)
    # Never make a certificate self-referential when the output lives below the project root.
    try:
        out_rel = out.relative_to(base)
    except ValueError:
        out_rel = None
    if out_rel is not None:
        if not out_rel.parts:
            raise ValidationError("certification output directory cannot be the project root")
        source_paths = [
            p for p in source_paths
            if p.relative_to(base).parts[: len(out_rel.parts)] != out_rel.parts
        ]
    tree_hash, source_rows = source_tree_hash(base, source_paths)

    engine = runner or SubprocessCommandRunner()
    tool_rows: dict[str, Any] = {}
    for probe in plan.tool_versions:
        result = engine.run(base, probe.argv, timeout=60)
        tool_rows[probe.name] = {
            "argv": list(probe.argv),
            "returncode": result.returncode,
            "output": result.combined.strip(),
        }

    command_rows: list[dict[str, Any]] = []
    used_slugs: set[str] = set()
    for index, command in enumerate(plan.commands):
        slug = _safe_slug(command.name)
        if slug in used_slugs:
            slug = f"{slug}-{index + 1}"
        used_slugs.add(slug)
        result = engine.run(base, command.argv, timeout=command.timeout)
        log_rel = Path("logs") / f"{slug}.txt"
        log_text = (
            "$ " + " ".join(command.argv) + "\n"
            f"returncode: {result.returncode}\n\n"
            "--- stdout ---\n" + result.stdout + "\n"
            "--- stderr ---\n" + result.stderr + "\n"
        )
        atomic_write_text(out / log_rel, log_text)
        row = {
            "name": command.name,
            "argv": list(command.argv),
            "required": command.required,
            "timeout": command.timeout,
            "returncode": result.returncode,
            "log": log_rel.as_posix(),
        }
        command_rows.append(row)
        if command.required and result.returncode != 0:
            findings.append(Finding("error", "certification-command", f"required certification command failed with exit code {result.returncode}: {command.name}", command.name))
        elif not command.required and result.returncode != 0:
            findings.append(Finding("warning", "certification-command-optional", f"optional certification command failed with exit code {result.returncode}: {command.name}", command.name))

    metadata: dict[str, Any] = {
        "schema": "aiq-lean-certification/v1",
        "name": plan.name,
        "created_utc": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        "project_root": str(base),
        "git": git,
        "source_tree": {
            "sha256": tree_hash,
            "files": source_rows,
            "include": list(plan.source_include),
            "exclude_parts": list(plan.source_exclude_parts),
        },
        "inputs": input_rows,
        "tool_versions": tool_rows,
        "commands": command_rows,
        "findings": [f.to_json() for f in findings],
        "ok": not any(f.level == "error" for f in findings),
    }
    atomic_write_json(out / "metadata.json", metadata)

    readme = [
        f"# {plan.name}", "",
        "This directory is a generated formalization evidence bundle.", "",
        f"- Result: **{'PASS' if metadata['ok'] else 'FAIL'}**",
        f"- Created UTC: `{metadata['created_utc']}`",
        f"- Git commit: `{git.get('commit', 'unavailable')}`",
        f"- Git dirty: `{git.get('dirty', 'unknown')}`",
        f"- Source-tree SHA256: `{tree_hash}`",
        f"- Snapshotted inputs: **{len(input_rows)}**",
        f"- Verification commands: **{len(command_rows)}**",
        "",
        "## Commands", "",
    ]
    if command_rows:
        for row in command_rows:
            readme.append(f"- `{row['name']}`: exit `{row['returncode']}`; log `{row['log']}`")
    else:
        readme.append("No commands were configured.")
    if findings:
        readme += ["", "## Findings", ""]
        for finding in findings:
            readme.append(f"- **{finding.level.upper()}** `{finding.code}`: {finding.message}")
    readme += [
        "", "## Interpretation", "",
        "The bundle certifies only the commands and artifacts named by its plan. Scientific completion criteria, source-faithfulness judgments, and external comparator requirements remain project policy.", "",
    ]
    atomic_write_text(out / "README.md", "\n".join(readme))

    checksum_paths = [path for path in out.rglob("*") if path.is_file() and path.name != "SHA256SUMS"]
    checksum_lines = []
    for path in sorted(checksum_paths, key=lambda p: p.relative_to(out).as_posix()):
        checksum_lines.append(f"{sha256_file(path)}  {path.relative_to(out).as_posix()}")
    atomic_write_text(out / "SHA256SUMS", "\n".join(checksum_lines) + "\n")
    return CertificationReport(out, metadata, tuple(findings))


def archive_certification_bundle(directory: str | pathlib.Path, output: str | pathlib.Path | None = None) -> Path:
    source = Path(directory).expanduser().resolve()
    if not source.is_dir():
        raise ValidationError(f"certification bundle directory does not exist: {source}")
    target = Path(output).expanduser().resolve() if output else source.with_suffix(".zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=f"{source.name}/{path.relative_to(source).as_posix()}")
    return target

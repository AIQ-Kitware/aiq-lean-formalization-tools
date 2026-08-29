"""Exact Lean declaration-signature comparison.

This module extracts the reusable core of DKPS's comparator preflight.  It is
stricter than ordinary source/name comparison: each declaration is inspected in
its own import environment and compared on both the raw positional universe
signature printed by Lean and the fully explicit type emitted by
``set_option pp.all true``.

The check is useful whenever two independently importable modules are expected
to expose byte-for-byte compatible theorem interfaces.  It does *not* replace a
third-party exporter/comparator whose serialization rules may be stricter.
"""
from __future__ import annotations

import json
import pathlib
import re
import tempfile
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import yaml

from .common import Finding, Path, find_workspace_root
from .errors import LeanExecutionError, ValidationError
from .lean_backend import CommandResult, LeanBackend, SubprocessLeanBackend

_DECL_PRINT_RE_TEMPLATE = r"(?:theorem|lemma|def|abbrev|axiom|opaque)\s+{name}(?P<universes>\.\{{[^}}]*\}})?\s*:"


@dataclass(frozen=True)
class SignaturePair:
    """One module pair and the declarations expected to agree."""

    name: str
    left_module: str
    right_module: str
    declarations: tuple[str, ...]
    left_label: str = "left"
    right_label: str = "right"
    build_targets: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, index: int = 0) -> "SignaturePair":
        # Accept the historical DKPS comparator config shape as a migration aid,
        # without giving those field names semantic priority in the API.
        left = data.get("left_module", data.get("challenge_module"))
        right = data.get("right_module", data.get("solution_module"))
        declarations = data.get("declarations", data.get("theorem_names"))
        if not isinstance(left, str) or not left.strip():
            raise ValidationError(f"signature pair {index} requires left_module")
        if not isinstance(right, str) or not right.strip():
            raise ValidationError(f"signature pair {index} requires right_module")
        if isinstance(declarations, str):
            declarations = [declarations]
        if not isinstance(declarations, Sequence) or isinstance(declarations, (str, bytes)):
            raise ValidationError(f"signature pair {index} declarations must be a non-empty list")
        decls = tuple(str(x).strip() for x in declarations if isinstance(x, str) and x.strip())
        if not decls or len(decls) != len(declarations):
            raise ValidationError(f"signature pair {index} declarations must contain only non-empty strings")
        targets = data.get("build_targets", ())
        if isinstance(targets, str):
            targets = [targets]
        if not isinstance(targets, Sequence) or isinstance(targets, (str, bytes)):
            raise ValidationError(f"signature pair {index} build_targets must be a list of strings")
        target_tuple = tuple(str(x).strip() for x in targets if isinstance(x, str) and x.strip())
        if len(target_tuple) != len(targets):
            raise ValidationError(f"signature pair {index} build_targets must contain only non-empty strings")
        return cls(
            name=str(data.get("name") or data.get("id") or f"pair-{index + 1}"),
            left_module=left.strip(),
            right_module=right.strip(),
            declarations=decls,
            left_label=str(data.get("left_label", "left")),
            right_label=str(data.get("right_label", "right")),
            build_targets=target_tuple,
        )


@dataclass(frozen=True)
class SignaturePolicy:
    pairs: tuple[SignaturePair, ...]
    build: bool = True

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SignaturePolicy":
        # A single legacy comparator config is itself a valid policy.
        if "pairs" not in data and (
            "left_module" in data or "challenge_module" in data
        ):
            return cls((SignaturePair.from_mapping(data),), bool(data.get("build", True)))
        raw = data.get("pairs")
        if not isinstance(raw, list) or not raw:
            raise ValidationError("signature policy requires a non-empty pairs list")
        pairs = []
        for index, row in enumerate(raw):
            if not isinstance(row, Mapping):
                raise ValidationError(f"signature pair {index} must be a mapping")
            pairs.append(SignaturePair.from_mapping(row, index=index))
        return cls(tuple(pairs), bool(data.get("build", True)))

    @classmethod
    def load(cls, path: str | pathlib.Path) -> "SignaturePolicy":
        p = Path(path).expanduser().resolve()
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError as ex:
            raise ValidationError(f"signature policy does not exist: {p}") from ex
        try:
            data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
        except (json.JSONDecodeError, yaml.YAMLError) as ex:
            raise ValidationError(f"invalid signature policy {p}: {ex}") from ex
        if not isinstance(data, Mapping):
            raise ValidationError("signature policy root must be a mapping")
        return cls.from_mapping(data)


@dataclass(frozen=True)
class LeanSignature:
    module: str
    declaration: str
    universe_signature: str | None
    full_type: str | None
    print_output: str
    check_output: str

    @property
    def resolved(self) -> bool:
        return self.universe_signature is not None and self.full_type is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "declaration": self.declaration,
            "resolved": self.resolved,
            "universe_signature": self.universe_signature,
            "full_type": self.full_type,
            "print_output": self.print_output,
            "check_output": self.check_output,
        }


@dataclass(frozen=True)
class SignatureComparison:
    pair: str
    declaration: str
    left_label: str
    right_label: str
    left: LeanSignature
    right: LeanSignature
    status: str
    findings: tuple[Finding, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "declaration": self.declaration,
            "left_label": self.left_label,
            "right_label": self.right_label,
            "status": self.status,
            "left": self.left.to_json(),
            "right": self.right.to_json(),
            "findings": [f.to_json() for f in self.findings],
        }


@dataclass(frozen=True)
class SignatureReport:
    comparisons: tuple[SignatureComparison, ...]
    findings: tuple[Finding, ...]
    build_results: tuple[CommandResult, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "comparisons": [row.to_json() for row in self.comparisons],
            "findings": [f.to_json() for f in self.findings],
            "build_results": [
                {
                    "argv": list(row.argv),
                    "returncode": row.returncode,
                    "stdout": row.stdout,
                    "stderr": row.stderr,
                }
                for row in self.build_results
            ],
            "semantic_warning": (
                "Matching Lean print/check output is a preflight contract; an external exporter or comparator remains authoritative when one is required."
            ),
        }


def _run_lean_source(
    backend: LeanBackend,
    root: Path,
    source: str,
    *,
    timeout: int,
) -> CommandResult:
    build = root / "build" / "aiq-lean-tools" / "signatures"
    build.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".lean", dir=build, delete=False
    ) as handle:
        handle.write(source.rstrip() + "\n")
        probe = Path(handle.name)
    try:
        rel = probe.relative_to(root)
        return backend.run(root, ["lake", "env", "lean", "-DmaxErrors=100000", str(rel)], timeout=timeout)
    finally:
        probe.unlink(missing_ok=True)


def _parse_universe_signature(output: str, declaration: str) -> str | None:
    if not output.strip():
        return None
    pattern = re.compile(
        _DECL_PRINT_RE_TEMPLATE.format(name=re.escape(declaration))
    )
    match = pattern.search(output)
    if not match:
        return None
    return match.group("universes") or "(no universes)"


def _parse_full_type(output: str, declaration: str) -> str | None:
    if not output.strip():
        return None
    # Prefer the explicit @name emitted by #check.  The output may include file
    # prefixes/diagnostics before it, so only normalize whitespace after the
    # declaration's own colon.
    anchor = "@" + declaration
    start = output.find(anchor)
    if start < 0:
        # Lean can omit the @ in some pretty-printer configurations.
        anchor = declaration
        start = output.find(anchor)
    if start < 0:
        return None
    colon = output.find(" : ", start)
    if colon < 0:
        return None
    raw = output[colon + 3 :].strip()
    # Stop before a conventional compiler diagnostic if one follows the check.
    lines = raw.splitlines()
    kept: list[str] = []
    for line in lines:
        if re.search(r"(?:^|:)\s*(?:error|warning):", line, re.I):
            break
        kept.append(line)
    value = re.sub(r"\s+", " ", "\n".join(kept)).strip()
    return value or None


def inspect_signature(
    root: str | pathlib.Path,
    module: str,
    declaration: str,
    *,
    backend: LeanBackend | None = None,
    timeout: int = 600,
) -> LeanSignature:
    base = find_workspace_root(root)
    engine = backend or SubprocessLeanBackend()
    printed = _run_lean_source(engine, base, f"import {module}\n#print {declaration}", timeout=timeout)
    checked = _run_lean_source(
        engine,
        base,
        f"import {module}\nset_option pp.all true in\n#check @{declaration}",
        timeout=timeout,
    )
    print_output = printed.combined
    check_output = checked.combined
    return LeanSignature(
        module=module,
        declaration=declaration,
        universe_signature=_parse_universe_signature(print_output, declaration),
        full_type=_parse_full_type(check_output, declaration),
        print_output=print_output,
        check_output=check_output,
    )


def compare_signatures(
    policy: SignaturePolicy,
    *,
    root: str | pathlib.Path | None = None,
    backend: LeanBackend | None = None,
    timeout: int = 600,
    build: bool | None = None,
) -> SignatureReport:
    base = find_workspace_root(root)
    engine = backend or SubprocessLeanBackend()
    findings: list[Finding] = []
    comparisons: list[SignatureComparison] = []
    build_results: list[CommandResult] = []
    do_build = policy.build if build is None else build

    if do_build:
        seen_targets: set[tuple[str, ...]] = set()
        for pair in policy.pairs:
            targets = pair.build_targets or (pair.left_module, pair.right_module)
            if targets in seen_targets:
                continue
            seen_targets.add(targets)
            result = engine.run(base, ["lake", "build", *targets], timeout=timeout)
            build_results.append(result)
            if result.returncode != 0:
                findings.append(Finding(
                    "error",
                    "signature-build",
                    f"lake build failed for signature pair {pair.name}: {', '.join(targets)}",
                    pair.name,
                ))

    # Still inspect after a failed build.  This produces useful resolution
    # diagnostics from an existing cache/source tree instead of hiding all rows.
    for pair in policy.pairs:
        for declaration in pair.declarations:
            try:
                left = inspect_signature(base, pair.left_module, declaration, backend=engine, timeout=timeout)
                right = inspect_signature(base, pair.right_module, declaration, backend=engine, timeout=timeout)
            except LeanExecutionError as ex:
                findings.append(Finding("error", "signature-execution", str(ex), f"{pair.name}:{declaration}"))
                continue
            row_findings: list[Finding] = []
            if not left.resolved:
                row_findings.append(Finding(
                    "error", "signature-left-unresolved",
                    f"could not obtain complete signature evidence from {pair.left_label} module {pair.left_module}",
                    declaration,
                ))
            if not right.resolved:
                row_findings.append(Finding(
                    "error", "signature-right-unresolved",
                    f"could not obtain complete signature evidence from {pair.right_label} module {pair.right_module}",
                    declaration,
                ))
            if left.resolved and right.resolved:
                if left.universe_signature != right.universe_signature:
                    row_findings.append(Finding(
                        "error", "signature-universe-mismatch",
                        f"raw universe signatures differ: {pair.left_label}={left.universe_signature}; {pair.right_label}={right.universe_signature}",
                        declaration,
                    ))
                if left.full_type != right.full_type:
                    row_findings.append(Finding(
                        "error", "signature-type-mismatch",
                        "fully explicit declaration types differ",
                        declaration,
                    ))
            status = "PASS" if not row_findings else "ERROR" if not (left.resolved and right.resolved) else "FAIL"
            comparisons.append(SignatureComparison(
                pair=pair.name,
                declaration=declaration,
                left_label=pair.left_label,
                right_label=pair.right_label,
                left=left,
                right=right,
                status=status,
                findings=tuple(row_findings),
            ))
            findings.extend(row_findings)

    return SignatureReport(tuple(comparisons), tuple(findings), tuple(build_results))

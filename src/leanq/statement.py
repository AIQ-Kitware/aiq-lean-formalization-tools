"""Statement closures: what a declaration's *type* means, read from the elaborator.

A source-to-Lean audit has to answer one question the proof graph cannot: which
hypotheses does this theorem actually impose?  Compact predicates such as
``IsTrialResidual A A₀ E₀ R`` hide their content behind a name, and a hand-written
dictionary of what each name means is exactly the thing an auditor cannot trust.

The exporter's ``statement`` mode walks the constants a statement *means*: a
definition is unfolded through its body, a structure or class through its
constructor fields, a theorem is a leaf, and anything in a boundary library
(Mathlib and the Lean core by default) is emitted with its type and docstring but
never unfolded.  Each record carries the pretty-printed type and a structural hash
of the elaborated type, so an accepted review can pin the statement it accepted
and notice when it changes.

The sidecar is optional and separate from the ordinary index: pretty-printing every
declaration of a library is far more expensive than recording its dependencies,
and most consumers only need the closures of a few reviewed declarations.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ._profile import profile
from .index import LEAN_SCRIPT, graph_scope_fingerprint, index_path
from .project import LeanProject, ProjectError

STATEMENT_CACHE_VERSION = 1

DEFAULT_BOUNDARY = (
    "Init", "Lean", "Std", "Mathlib", "Batteries", "Aesop", "Qq", "ProofWidgets",
    "Plausible", "LeanSearchClient", "ImportGraph",
)


@dataclass(frozen=True)
class StatementRecord:
    """One constant in a statement closure, as the elaborator sees it."""

    name: str
    module: str = ""
    kind: str = ""
    library: str | None = None
    role: str = "unfolded"
    boundary: bool = False
    is_prop: bool | None = None
    line: int | None = None
    type_deps: tuple[str, ...] = ()
    body_deps: tuple[str, ...] = ()
    type: str = ""
    signature: str = ""
    type_expr_hash: str = ""
    docstring: str | None = None
    fields: tuple[Mapping[str, str], ...] = ()
    flags: tuple[str, ...] = ()
    missing: bool = False

    @classmethod
    def from_json(cls, obj: Mapping) -> "StatementRecord":
        if obj.get("missing"):
            return cls(name=obj["name"], role=str(obj.get("role", "seed")), missing=True)
        return cls(
            name=obj["name"],
            module=obj.get("module", ""),
            kind=obj.get("kind", ""),
            library=obj.get("library"),
            role=obj.get("role", "unfolded"),
            boundary=bool(obj.get("boundary", False)),
            is_prop=obj.get("isProp"),
            line=obj.get("line"),
            type_deps=tuple(obj.get("typeDeps", ())),
            body_deps=tuple(obj.get("bodyDeps", ())),
            type=obj.get("type", "") or "",
            signature=obj.get("signature", "") or "",
            type_expr_hash=str(obj.get("typeExprHash", "") or ""),
            docstring=obj.get("docstring"),
            fields=tuple(dict(f) for f in obj.get("fields") or ()),
            flags=tuple(obj.get("flags") or ()),
        )

    def to_json(self) -> dict:
        if self.missing:
            return {"name": self.name, "role": self.role, "missing": True}
        return {
            "name": self.name,
            "module": self.module,
            "kind": self.kind,
            "library": self.library,
            "role": self.role,
            "boundary": self.boundary,
            "isProp": self.is_prop,
            "line": self.line,
            "typeDeps": list(self.type_deps),
            "bodyDeps": list(self.body_deps),
            "type": self.type,
            "signature": self.signature,
            "typeExprHash": self.type_expr_hash,
            "typeTextSha256": self.type_text_sha256,
            "docstring": self.docstring,
            "fields": [dict(f) for f in self.fields],
            "flags": list(self.flags),
        }

    @property
    def short_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    @property
    def type_text_sha256(self) -> str:
        """Hash of the pretty-printed type.

        ``type_expr_hash`` is the elaborator's structural hash and ignores binder
        names and notation; this one changes whenever the printed statement does.
        A reviewer pins both: the structural hash says whether the *term* moved,
        the text hash says whether what a reader sees moved.
        """
        return hashlib.sha256(self.type.encode("utf-8")).hexdigest()

    @property
    def plumbing(self) -> bool:
        """Instance, projection and constructor constants: the plumbing every
        Mathlib-typed statement drags in.  A renderer collapses them; they are never
        what a reviewer is looking for, and never what a statement is about."""
        return "instance" in self.flags or "projection" in self.flags or self.kind == "ctor"

    @property
    def has_body(self) -> bool:
        """Whether this constant's meaning has a body: a definition's value or an
        inductive type's constructor fields.  A theorem's value is its proof, which is
        not part of what the statement says."""
        return not self.missing and self.kind in {"def", "inductive"}

    @property
    def expands(self) -> bool:
        """Whether the closure walks through this constant when it is reached."""
        return self.has_body and not self.boundary

    def children(self) -> list[tuple[str, str]]:
        """``(constant, via)`` pairs one step below this constant in its closure."""
        out = [(dep, "type") for dep in self.type_deps]
        if self.has_body:
            out += [(dep, "body") for dep in self.body_deps if dep not in self.type_deps]
        return out

    def location(self) -> str:
        path = "/".join(self.module.split(".")) + ".lean"
        return f"{path}:{self.line}" if self.line else path


def statement_sidecar_path(
    project: LeanProject, library: str, seeds: Sequence[str] | None
) -> Path:
    """Where a statement sidecar is cached, next to the library's ordinary index.

    A whole-library sidecar has no seed label; a seeded one is keyed by the seed
    names so several reviewed sets can coexist without invalidating each other.
    """
    base = index_path(project, library)
    if not seeds:
        return base.with_name(f"{library}.statements-v{STATEMENT_CACHE_VERSION}.jsonl")
    label = "__".join(seed.replace(".", "-") for seed in seeds)
    if len(label) > 96:
        label = hashlib.sha256("\0".join(seeds).encode()).hexdigest()[:16]
    return base.with_name(
        f"{library}.statements-{label}-v{STATEMENT_CACHE_VERSION}.jsonl"
    )


def _meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def _toolchain(project: LeanProject) -> str:
    path = project.root / "lean-toolchain"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def statement_fingerprint(
    project: LeanProject, library: str, modules: Sequence[str],
    seeds: Sequence[str] | None, boundary: Sequence[str],
) -> str:
    """Inputs that can change a sidecar: the imported sources, the seeds, the boundary."""
    digest = hashlib.sha256()
    digest.update(f"leanq-statement-v{STATEMENT_CACHE_VERSION}\0".encode())
    digest.update(graph_scope_fingerprint(project, library, modules).encode())
    digest.update(b"\0seeds\0")
    for seed in seeds or ():
        digest.update(seed.encode())
        digest.update(b"\0")
    digest.update(b"\0boundary\0")
    for prefix in boundary:
        digest.update(prefix.encode())
        digest.update(b"\0")
    return digest.hexdigest()


@profile
def build_statement_sidecar(
    project: LeanProject,
    library: str,
    *,
    seeds: Sequence[str] | None = None,
    modules: Sequence[str] | None = None,
    boundary: Sequence[str] = DEFAULT_BOUNDARY,
    out: Path | None = None,
    timeout: int = 3600,
    verbose: bool = True,
) -> Path:
    """Run the exporter in ``statement`` mode and write a JSONL sidecar.

    With ``seeds`` the closure starts from exactly those constants and only the
    modules in ``modules`` (default: every built module of the library) are
    imported.  Without seeds every public declaration of the library is a seed,
    which is the whole-library sidecar -- optional, and slow in proportion to
    how much there is to pretty-print.
    """
    modules = list(modules) if modules is not None else project.modules(library)
    if not modules:
        raise ProjectError(f"no built modules for library {library!r}")
    out = out or statement_sidecar_path(project, library, seeds)
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write("\n".join(modules))
        modules_file = handle.name
    seeds_file = ""
    if seeds:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("\n".join(seeds))
            seeds_file = handle.name

    cmd = [
        "lake", "env", "lean", "--run", str(LEAN_SCRIPT), library, modules_file,
        "statement", seeds_file, ",".join(boundary),
    ]
    if verbose:
        what = f"{len(seeds)} seed(s)" if seeds else "every declaration"
        print(
            f"leanq: statement closure of {what} in {library}, importing "
            f"{len(modules)} module(s) [statement]",
            file=sys.stderr,
        )
    try:
        proc = subprocess.run(
            cmd,
            cwd=project.root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LEAN_NUM_THREADS": os.environ.get("LEAN_NUM_THREADS", "4")},
        )
    finally:
        os.unlink(modules_file)
        if seeds_file:
            os.unlink(seeds_file)

    stdout = proc.stdout or ""
    records = [line for line in stdout.splitlines() if line.startswith("{")]
    junk = [line for line in stdout.splitlines() if line and not line.startswith("{")]
    if proc.returncode != 0 and not records:
        detail = (proc.stderr or "").strip() or "\n".join(junk[:20]) or "(no output)"
        raise ProjectError(f"lean exited {proc.returncode}:\n{detail}")
    if proc.stderr and "limit" in proc.stderr and verbose:
        print(proc.stderr.rstrip(), file=sys.stderr)
    if os.environ.get("LEANQ_TIMINGS") == "1" and proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)

    out.write_text("\n".join(records) + ("\n" if records else ""), encoding="utf-8")
    meta = {
        "statementCacheVersion": STATEMENT_CACHE_VERSION,
        "fingerprint": statement_fingerprint(project, library, modules, seeds, boundary),
        "library": library,
        "seeds": list(seeds or ()),
        "boundary": list(boundary),
        "modules": len(modules),
        "toolchain": _toolchain(project),
        "records": len(records),
    }
    _meta_path(out).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    if verbose:
        print(f"leanq: wrote {len(records)} statement record(s) to {out}", file=sys.stderr)
    return out


def load_statement_sidecar(path: Path) -> list[StatementRecord]:
    if not path.exists():
        raise ProjectError(f"no statement sidecar at {path}; run `leanq statement` first")
    out = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(StatementRecord.from_json(json.loads(line)))
    return out


def load_sidecar_meta(path: Path) -> dict:
    meta_path = _meta_path(path)
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def sidecar_is_current(
    project: LeanProject, library: str, modules: Sequence[str],
    seeds: Sequence[str] | None, boundary: Sequence[str], path: Path,
) -> bool:
    if not path.exists():
        return False
    meta = load_sidecar_meta(path)
    return (
        meta.get("statementCacheVersion") == STATEMENT_CACHE_VERSION
        and meta.get("fingerprint")
        == statement_fingerprint(project, library, modules, seeds, boundary)
    )


@profile
def ensure_statement_sidecar(
    project: LeanProject,
    library: str,
    *,
    seeds: Sequence[str] | None = None,
    modules: Sequence[str] | None = None,
    boundary: Sequence[str] = DEFAULT_BOUNDARY,
    refresh: bool = False,
    verbose: bool = True,
) -> tuple[Path, list[StatementRecord]]:
    """Load a sidecar, rebuilding it when its inputs changed."""
    modules = list(modules) if modules is not None else project.modules(library)
    path = statement_sidecar_path(project, library, seeds)
    if refresh or not sidecar_is_current(project, library, modules, seeds, boundary, path):
        if verbose and path.exists() and not refresh:
            print(f"leanq: statement sidecar stale, rebuilding {path}", file=sys.stderr)
        build_statement_sidecar(
            project, library, seeds=seeds, modules=modules, boundary=boundary,
            out=path, verbose=verbose,
        )
    return path, load_statement_sidecar(path)


# ---------------------------------------------------------------------------
# Pure closure work: no Lean, so it is testable on hand-built records.


def by_name(records: Iterable[StatementRecord]) -> dict[str, StatementRecord]:
    return {record.name: record for record in records}


def closure_edges(
    records: Mapping[str, StatementRecord], seed: str
) -> list[tuple[str, str, str]]:
    """Edges ``(parent, child, via)`` of the statement closure rooted at ``seed``.

    ``via`` is ``"type"`` when the child appears in the parent's type and
    ``"body"`` when it is reached only through a definition body or a structure's
    fields.  The seed is expanded even if it is a boundary constant; anything else
    stops at the boundary.
    """
    root = records.get(seed)
    if root is None:
        return []
    edges: list[tuple[str, str, str]] = []
    seen = {seed}
    queue = [seed]
    while queue:
        name = queue.pop(0)
        record = records.get(name)
        if record is None or record.missing:
            continue
        if name != seed and not record.expands:
            continue
        for child, via in record.children():
            edges.append((name, child, via))
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return edges


def closure_summary(records: Mapping[str, StatementRecord], seed: str) -> dict:
    """What a reviewer needs at a glance: unfolded project constants and boundary leaves."""
    edges = closure_edges(records, seed)
    reached = list(dict.fromkeys(child for _, child, _ in edges))
    unfolded = [n for n in reached if n in records and records[n].expands]
    boundary = [
        n for n in reached
        if n in records and records[n].boundary and not records[n].plumbing
    ]
    plumbing = [n for n in reached if n in records and records[n].plumbing]
    leaves = [
        n for n in reached
        if n in records and not records[n].expands and not records[n].boundary
        and not records[n].plumbing
    ]
    unknown = [n for n in reached if n not in records or records[n].missing]
    return {
        "seed": seed,
        "edgeCount": len(edges),
        "reached": reached,
        "unfolded": unfolded,
        "boundary": boundary,
        "plumbing": plumbing,
        "leaves": leaves,
        "unknown": unknown,
    }


def closure_payload(
    records: Mapping[str, StatementRecord], seeds: Sequence[str], *, meta: Mapping | None = None
) -> dict:
    """JSON payload for one or more seeds: records, typed edges, and per-seed summaries."""
    names: list[str] = []
    edges: list[dict] = []
    for seed in seeds:
        for parent, child, via in closure_edges(records, seed):
            edges.append({"source": parent, "target": child, "via": via})
            for n in (parent, child):
                if n not in names:
                    names.append(n)
        if seed not in names:
            names.append(seed)
    return {
        "schemaVersion": 1,
        "payloadKind": "statement-closure",
        "seeds": list(seeds),
        "meta": dict(meta or {}),
        "records": [
            records[n].to_json() if n in records else {"name": n, "missing": True}
            for n in names
        ],
        "edges": edges,
        "summaries": [closure_summary(records, seed) for seed in seeds],
    }


def render_closure_text(
    records: Mapping[str, StatementRecord],
    seed: str,
    *,
    show_boundary: bool = True,
    show_docstrings: bool = False,
    width: int = 100,
) -> str:
    """A tree a reviewer can read top-down: each constant once, in full, where first reached.

    The signature is shown rather than the raw type -- it is the `#check` form a
    reader compares with the paper.  Instance and projection plumbing is collapsed
    into one line per node, and boundary constants can be hidden altogether; the
    summary at the end still counts them.
    """
    root = records.get(seed)
    if root is None:
        return f"no statement record for {seed!r}\n"
    lines: list[str] = []
    printed: set[str] = set()

    def header(record: StatementRecord) -> str:
        tag = record.kind or "?"
        for flag in ("class", "structure"):
            if flag in record.flags:
                tag = flag
        if record.boundary:
            tag += ", boundary"
        return f"{record.name}  ({tag}, {record.location()})"

    def body(record: StatementRecord, indent: str) -> None:
        text = record.signature or record.type
        for i, line in enumerate(text.splitlines()):
            lines.append(f"{indent}{line}" if i == 0 else f"{indent}{line}")
        for f in record.fields:
            ftype = " ".join(str(f.get("type", "")).split())
            lines.append(f"{indent}field {f.get('name')} : {ftype}")
        if show_docstrings and record.docstring:
            doc = " ".join(record.docstring.split())
            lines.append(f"{indent}doc: {doc[:width]}")
        lines.append(
            f"{indent}hash: expr={record.type_expr_hash} text={record.type_text_sha256[:16]}"
        )

    def walk(name: str, via: str, depth: int) -> None:
        indent = "  " * depth
        record = records.get(name)
        marker = f"[{via}] " if via else ""
        if record is None:
            lines.append(f"{indent}{marker}{name}  (no record)")
            return
        if record.missing:
            lines.append(f"{indent}{marker}{name}  (not in environment)")
            return
        if name != seed and record.boundary and not show_boundary:
            return
        if name in printed:
            lines.append(f"{indent}{marker}{name}  (above)")
            return
        printed.add(name)
        lines.append(f"{indent}{marker}{header(record)}")
        if name != seed and record.boundary:
            return
        body(record, indent + "    ")
        if name != seed and not record.expands:
            return
        plumbing: list[str] = []
        for dep, dep_via in record.children():
            child = records.get(dep)
            if child is not None and child.plumbing and dep != seed:
                if dep not in printed:
                    printed.add(dep)
                    plumbing.append(dep)
                continue
            walk(dep, dep_via, depth + 1)
        if plumbing and show_boundary:
            lines.append(f"{indent}  via instances/projections: " + ", ".join(plumbing))

    walk(seed, "", 0)
    summary = closure_summary(records, seed)
    lines.append("")
    lines.append(
        f"{len(summary['unfolded'])} project constant(s) unfolded, "
        f"{len(summary['leaves'])} project leaf/leaves, "
        f"{len(summary['boundary'])} boundary constant(s), "
        f"{len(summary['plumbing'])} instance/projection constant(s)"
        + (f", {len(summary['unknown'])} unknown" if summary["unknown"] else "")
    )
    if summary["boundary"]:
        lines.append("boundary: " + ", ".join(summary["boundary"]))
    return "\n".join(lines) + "\n"

"""Building and loading the declaration index."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from ._profile import profile
from .project import LeanProject, ProjectError

LEAN_SCRIPT = Path(__file__).with_name("lean") / "decl_index.lean"
GRAPH_CACHE_VERSION = 3


@dataclass(frozen=True)
class Decl:
    """One declaration, as the elaborator sees it."""

    name: str
    module: str
    kind: str
    is_prop: bool | None
    prop_valued: bool | None
    sorried: bool | None
    line: int | None
    axioms: tuple[str, ...] | None
    deps: tuple[str, ...]
    library: str | None = None
    internal: bool = False

    @classmethod
    def from_json(cls, obj: dict) -> "Decl":
        return cls(
            name=obj["name"],
            module=obj["module"],
            kind=obj["kind"],
            is_prop=obj.get("isProp"),
            prop_valued=obj.get("propValued"),
            sorried=obj.get("sorried"),
            line=obj.get("line"),
            axioms=(
                None if obj.get("axioms") is None else tuple(obj.get("axioms", ()))
            ),
            deps=tuple(obj.get("deps", ())),
            library=obj.get("library"),
            internal=bool(obj.get("internal", False)),
        )

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "module": self.module,
            "kind": self.kind,
            "isProp": self.is_prop,
            "propValued": self.prop_valued,
            "sorried": self.sorried,
            "line": self.line,
            "axioms": None if self.axioms is None else list(self.axioms),
            "deps": list(self.deps),
            "library": self.library,
            "internal": self.internal,
        }

    @property
    def short_name(self) -> str:
        return self.name.rsplit(".", 1)[-1]

    def location(self) -> str:
        """`path:line`, clickable in most terminals."""
        path = "/".join(self.module.split(".")) + ".lean"
        return f"{path}:{self.line}" if self.line else path


def index_path(project: LeanProject, library: str) -> Path:
    """Where the index is cached.

    Defaults to ``<project>/.leanq``. Set ``LEANQ_CACHE_DIR`` to keep generated files out of a
    project you do not own -- a git submodule, for instance, where an untracked directory shows
    up as a dirty worktree in the parent repository.
    """
    base = os.environ.get("LEANQ_CACHE_DIR")
    if base:
        return Path(base) / project.root.name / f"{library}.jsonl"
    return project.root / ".leanq" / f"{library}.jsonl"


def detail_index_path(project: LeanProject, library: str, detail: str) -> Path:
    """Cache path for an ordinary whole-library index at a chosen detail level."""
    if detail == "full":
        return index_path(project, library)
    if detail not in {"deps", "graph"}:
        raise ProjectError(f"unknown index detail {detail!r}; expected full, deps, or graph")
    base = index_path(project, library)
    detail_label = (
        f"graph-v{GRAPH_CACHE_VERSION}" if detail == "graph" else detail
    )
    return base.with_name(f"{library}.{detail_label}.jsonl")


@profile
def build_index(
    project: LeanProject,
    library: str,
    *,
    out: Path | None = None,
    modules: Sequence[str] | None = None,
    timeout: int = 3600,
    verbose: bool = True,
    detail: str = "full",
) -> Path:
    """Run the Lean metaprogram and write a JSONL index.

    By default every built module of the library is imported explicitly.  Importing only the
    root would quietly index nothing for modules the root does not import, and a confident zero
    is a worse answer than an obvious error.  ``modules`` intentionally overrides that behavior
    for root-scoped questions such as the production promotion boundary.
    """
    if detail not in {"full", "deps", "graph"}:
        raise ProjectError(
            f"unknown index detail {detail!r}; expected full, deps, or graph"
        )
    scoped = modules is not None
    modules = list(modules) if scoped else project.modules(library)
    if project.stale_modules and verbose and not scoped:
        print(
            f"leanq: skipping {len(project.stale_modules)} stale artifact(s) with no source, "
            f"e.g. {project.stale_modules[0]}",
            file=sys.stderr,
        )
    out = out or detail_index_path(project, library, detail)
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write("\n".join(modules))
        modules_file = handle.name

    cmd = [
        "lake", "env", "lean", "--run", str(LEAN_SCRIPT), library, modules_file, detail
    ]
    if verbose:
        print(
            f"leanq: indexing {len(modules)} module(s) of {library} "
            f"in {project.root} [{detail}]",
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

    if os.environ.get("LEANQ_TIMINGS") == "1" and proc.stderr:
        print(proc.stderr.rstrip(), file=sys.stderr)

    stdout = proc.stdout or ""
    records: list[str] = []
    junk: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("{"):
            records.append(line)
        elif line:
            junk.append(line)
    if proc.returncode != 0 and not records:
        error_detail = (proc.stderr or "").strip() or "\n".join(junk[:20]) or "(no output)"
        if scoped and "incompatible header" in error_detail:
            roots = " ".join(modules)
            error_detail += (
                "\nleanq imported only the requested graph root module(s). "
                "At least one artifact in that real import closure was built by an "
                "incompatible Lean toolchain. Rebuild the roots with:\n"
                f"  lake build {roots}"
            )
        raise ProjectError(f"lean exited {proc.returncode}:\n{error_detail}")
    if junk and verbose:
        print(f"leanq: {len(junk)} non-record line(s) from lean, first:", file=sys.stderr)
        print(f"  {junk[0][:200]}", file=sys.stderr)

    out.write_text("\n".join(records) + ("\n" if records else ""), encoding="utf-8")
    if verbose:
        print(f"leanq: wrote {len(records)} declaration(s) to {out}", file=sys.stderr)
    return out


@profile
def load_index(path: Path) -> list[Decl]:
    if not path.exists():
        raise ProjectError(f"no index at {path}; run `leanq index` first")
    decls = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                decls.append(Decl.from_json(json.loads(line)))
    return decls


@profile
def ensure_index(
    project: LeanProject, library: str, *, refresh: bool = False, verbose: bool = True
) -> list[Decl]:
    path = index_path(project, library)
    if refresh or not path.exists():
        build_index(project, library, out=path, verbose=verbose)
    return load_index(path)


def _cache_meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def graph_scope_fingerprint(
    project: LeanProject, library: str, roots: Sequence[str]
) -> str:
    """Fingerprint source/tool inputs that can change a scoped graph index.

    This intentionally hashes source content rather than timestamps.  The cost is
    tiny compared with Lean import/elaboration and it makes cache reuse safe across
    rebases, archive extraction, and timestamp-preserving copies.  The local import
    closure is included because an unchanged consumer source can elaborate to a
    different constant after an imported local API changes.
    """
    digest = hashlib.sha256()
    digest.update(f"leanq-graph-v{GRAPH_CACHE_VERSION}\0{library}\0".encode())
    for config in (project.lakefile, project.root / "lean-toolchain", LEAN_SCRIPT):
        if config.exists():
            digest.update(str(config.relative_to(project.root) if config.is_relative_to(project.root) else config).encode())
            digest.update(b"\0")
            digest.update(config.read_bytes())
            digest.update(b"\0")
    try:
        modules = project.local_import_closure(tuple(dict.fromkeys(roots)))
    except ProjectError:
        modules = list(dict.fromkeys(roots))
    for module in sorted(set(modules)):
        source = project.source_of(module)
        if not source.exists():
            continue
        digest.update(module.encode())
        digest.update(b"\0")
        digest.update(source.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def scoped_index_cache_state(
    project: LeanProject, library: str, roots: Sequence[str], *, detail: str = "deps"
) -> dict:
    """Describe whether a scoped cache can be reused without invoking Lean."""
    path = scoped_index_path(project, library, roots, detail=detail)
    state = {"path": path, "exists": path.exists(), "current": path.exists()}
    if detail != "graph":
        return state
    fingerprint = graph_scope_fingerprint(project, library, roots)
    meta_path = _cache_meta_path(path)
    state.update({"fingerprint": fingerprint, "metaPath": meta_path, "current": False})
    if not path.exists() or not meta_path.exists():
        return state
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    state["current"] = (
        meta.get("graphCacheVersion") == GRAPH_CACHE_VERSION
        and meta.get("fingerprint") == fingerprint
        and meta.get("library") == library
        and meta.get("roots") == list(dict.fromkeys(roots))
    )
    return state


def scoped_index_path(
    project: LeanProject, library: str, roots: Sequence[str], *, detail: str = "deps"
) -> Path:
    """Cache path for an index produced by importing only ``roots``.

    Root-scoped indexes answer public-surface questions.  The ordinary library
    index deliberately imports every built module, which is the right behavior
    for inventory queries but would make an unused experimental module look public.
    """
    if not roots:
        raise ProjectError("a scoped index needs at least one root module")
    label = "__".join(root.replace(".", "-") for root in roots)
    if len(label) > 96:
        import hashlib
        label = hashlib.sha256("\0".join(roots).encode()).hexdigest()[:16]
    base = index_path(project, library)
    detail_label = (
        f"graph-v{GRAPH_CACHE_VERSION}" if detail == "graph" else detail
    )
    return base.with_name(f"{library}.roots-{label}.{detail_label}.jsonl")


@profile
def ensure_scoped_index(
    project: LeanProject,
    library: str,
    roots: Sequence[str],
    *,
    refresh: bool = False,
    verbose: bool = True,
    detail: str = "deps",
) -> list[Decl]:
    """Load an index for exactly the environment obtained by importing ``roots``.

    Promotion-boundary queries default to ``detail="deps"`` because they only need
    declaration identity and dependency edges.  The dependency-only Lean pass skips
    axiom closure, proposition normalization, and source-range lookup.
    """
    roots = tuple(dict.fromkeys(roots))
    path = scoped_index_path(project, library, roots, detail=detail)
    state = scoped_index_cache_state(project, library, roots, detail=detail)
    if refresh or not state["current"]:
        if verbose and path.exists() and detail == "graph" and not refresh:
            print(f"leanq: graph cache stale, rebuilding {path}", file=sys.stderr)
        build_index(
            project, library, out=path, modules=roots, verbose=verbose, detail=detail
        )
        if detail == "graph":
            meta_path = _cache_meta_path(path)
            meta = {
                "graphCacheVersion": GRAPH_CACHE_VERSION,
                "fingerprint": graph_scope_fingerprint(project, library, roots),
                "library": library,
                "roots": list(roots),
            }
            meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return load_index(path)


@profile
def ensure_graph_index(
    project: LeanProject, library: str, *, refresh: bool = False, verbose: bool = True
) -> list[Decl]:
    """Load the dependency-complete graph index for one local library.

    Graph indexes include internal/private implementation constants so a public
    theorem's path through private support is not cut before it reaches another
    public theorem. Expensive axiom/source metadata stays disabled.
    """
    path = detail_index_path(project, library, "graph")
    if refresh or not path.exists():
        build_index(
            project, library, out=path, verbose=verbose, detail="graph"
        )
    return load_index(path)


def filter_decls(
    decls: Iterable[Decl],
    *,
    kind: str | None = None,
    sorried: bool | None = None,
    prop_valued: bool | None = None,
    is_prop: bool | None = None,
    module: str | None = None,
    name: str | None = None,
    axiom: str | None = None,
    uses: str | None = None,
) -> Iterator[Decl]:
    """Apply the CLI's filters.  ``module`` and ``name`` are substring matches."""
    for decl in decls:
        if kind is not None and decl.kind != kind:
            continue
        if sorried is not None and decl.sorried != sorried:
            continue
        if prop_valued is not None and decl.prop_valued != prop_valued:
            continue
        if is_prop is not None and decl.is_prop != is_prop:
            continue
        if module is not None and module not in decl.module:
            continue
        if name is not None and name not in decl.name:
            continue
        if axiom is not None and (
            decl.axioms is None or not any(axiom in a for a in decl.axioms)
        ):
            continue
        if uses is not None and not any(
            d == uses or d.rsplit(".", 1)[-1] == uses for d in decl.deps
        ):
            continue
        yield decl


@profile
def by_name(decls: Iterable[Decl]) -> dict[str, Decl]:
    """Index by full name, with short names as a fallback key."""
    table: dict[str, Decl] = {}
    for decl in decls:
        table[decl.name] = decl
        table.setdefault(decl.short_name, decl)
    return table


@profile
def closure(
    decls: Iterable[Decl], root: str, *, library: str | None = None, depth: int = 0
) -> list[Decl]:
    """Everything ``root`` transitively needs, restricted to declarations we have.

    With ``library`` set, only declarations from that library are followed, which is the
    "what would I have to bring along" question: dependencies on Mathlib are already available
    wherever the declaration is restated, local helpers are not.
    """
    table = by_name(decls)
    start = table.get(root)
    if start is None:
        return []
    seen: dict[str, Decl] = {}
    frontier = [(start, 0)]
    while frontier:
        decl, level = frontier.pop()
        for dep in decl.deps:
            target = table.get(dep) or table.get(dep.rsplit(".", 1)[-1])
            if target is None or target.name in seen or target.name == start.name:
                continue
            if library is not None and not target.module.startswith(library):
                continue
            seen[target.name] = target
            if depth == 0 or level + 1 < depth:
                frontier.append((target, level + 1))
    return sorted(seen.values(), key=lambda d: (d.module, d.name))

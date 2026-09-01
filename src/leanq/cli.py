"""``leanq`` command line.

Output defaults to plain text and every subcommand takes ``--json``, so an agent can either
read it or parse it without a second thought.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from ._profile import profile
from .index import (
    Decl,
    build_index,
    by_name,
    closure,
    ensure_index,
    ensure_scoped_index,
    filter_decls,
    index_path,
    scoped_index_path,
    scoped_index_cache_state,
    GRAPH_CACHE_VERSION,
)
from .graph import (
    declarations_from_graph_payload,
    environment_dependency_graph,
    merge_declarations,
    strongly_connected_components,
    target_dependency_graph,
    transitive_reduction,
)
from .presentation import build_presentation, load_presentation
from .headlines import analyze_headlines, parse_consumption_landmark, prepare_project_explorer
from .viewer import write_comparison_html, write_graph_html
from .project import ProjectError, find_project
from .promotion import DEFAULT_TAGS, promotion_report
from .statement import (
    DEFAULT_BOUNDARY,
    by_name as statements_by_name,
    closure_payload,
    ensure_statement_sidecar,
    load_sidecar_meta,
    render_closure_text,
)


def _tristate(args, flag: str) -> bool | None:
    value = getattr(args, flag, None)
    return value


@profile
def _resolve(args):
    project = find_project(Path(args.project) if args.project else None)
    library = args.lib
    if library is None:
        libs = project.libraries()
        if len(libs) != 1:
            raise ProjectError(
                f"{project.root} builds {len(libs)} libraries ({', '.join(libs) or 'none'}); "
                f"pass --lib"
            )
        library = libs[0]
    return project, library


def _emit(decls: list[Decl], args) -> None:
    if args.json:
        json.dump([d.to_json() for d in decls], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    if args.names:
        for decl in decls:
            print(decl.name)
        return
    width = max((len(d.short_name) for d in decls), default=0)
    for decl in decls:
        marks = "".join(
            [
                "S" if decl.sorried is True else ("?" if decl.sorried is None else "-"),
                "P" if decl.prop_valued is True else ("?" if decl.prop_valued is None else "-"),
            ]
        )
        print(f"{marks}  {decl.kind:8s} {decl.short_name:{width}s}  {decl.location()}")
    print(f"\n{len(decls)} declaration(s)   [S]orried  [P]rop-valued", file=sys.stderr)


def cmd_index(args) -> int:
    project, library = _resolve(args)
    out = build_index(project, library, out=Path(args.out) if args.out else None)
    print(out)
    return 0


def cmd_query(args) -> int:
    project, library = _resolve(args)
    decls = ensure_index(project, library, refresh=args.refresh, verbose=not args.json)
    selected = sorted(
        filter_decls(
            decls,
            kind=args.kind,
            sorried=_tristate(args, "sorried"),
            prop_valued=_tristate(args, "prop_valued"),
            is_prop=_tristate(args, "is_prop"),
            module=args.module,
            name=args.name,
            axiom=args.axiom,
            uses=getattr(args, "uses", None),
        ),
        key=lambda d: (d.module, d.name),
    )
    _emit(selected, args)
    return 0


def cmd_stubs(args) -> int:
    """Declarations whose axiom closure contains `sorryAx` — the honest `sorry` count."""
    args.sorried = True
    args.axiom = None
    args.is_prop = None
    return cmd_query(args)


def cmd_stats(args) -> int:
    project, library = _resolve(args)
    decls = ensure_index(project, library, refresh=args.refresh, verbose=not args.json)
    depth = args.depth
    table: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for decl in decls:
        group = ".".join(decl.module.split(".")[:depth]) if depth else decl.module
        counter = table[group]
        if decl.kind in ("def", "theorem"):
            counter[f"{decl.kind}_total"] += 1
            if decl.sorried:
                counter[f"{decl.kind}_stub"] += 1
            if decl.kind == "def" and decl.prop_valued and decl.sorried:
                counter["prop_stub"] += 1
    if args.json:
        json.dump(
            {g: dict(c) for g, c in sorted(table.items())}, sys.stdout, indent=2
        )
        sys.stdout.write("\n")
        return 0
    width = max((len(g) for g in table), default=10)
    print(
        f"{'module':{width}s} {'defs':>6s} {'stub':>6s} {'%':>5s} "
        f"{'thms':>6s} {'stub':>6s} {'%':>5s} {'Prop:=sorry':>12s}"
    )
    for group, c in sorted(table.items(), key=lambda kv: -kv[1]["def_stub"]):
        dt, ds = c["def_total"], c["def_stub"]
        tt, ts = c["theorem_total"], c["theorem_stub"]
        dp = f"{100 * ds / dt:.0f}%" if dt else "-"
        tp = f"{100 * ts / tt:.0f}%" if tt else "-"
        print(
            f"{group:{width}s} {dt:6d} {ds:6d} {dp:>5s} {tt:6d} {ts:6d} {tp:>5s} "
            f"{c['prop_stub']:12d}"
        )
    return 0


def cmd_deps(args) -> int:
    """What a declaration needs — directly, or transitively within the library."""
    project, library = _resolve(args)
    decls = ensure_index(project, library, refresh=args.refresh, verbose=not args.json)
    table = by_name(decls)
    if args.decl not in table:
        print(f"no declaration named {args.decl!r}", file=sys.stderr)
        return 1
    if args.transitive or args.local:
        found = closure(
            decls,
            args.decl,
            library=library if args.local else None,
            depth=0 if args.transitive else 1,
        )
    else:
        start = table[args.decl]
        found = sorted(
            (table[d] for d in start.deps if d in table),
            key=lambda d: (d.module, d.name),
        )
    _emit(found, args)
    return 0


def cmd_rdeps(args) -> int:
    """Which declarations reference this one."""
    args.uses = args.decl
    args.kind = getattr(args, "kind", None)
    args.name = None
    args.axiom = None
    args.sorried = getattr(args, "sorried", None)
    args.is_prop = None
    args.prop_valued = getattr(args, "prop_valued", None)
    return cmd_query(args)


@profile
def cmd_promotions(args) -> int:
    """Tagged declarations that a chosen production root actually depends on."""
    project, library = _resolve(args)
    roots = args.root or [library]
    decls = ensure_scoped_index(
        project, library, roots, refresh=args.refresh, verbose=not args.json, detail="deps"
    )
    tags = args.tag or list(DEFAULT_TAGS)
    report = promotion_report(
        decls, tags=tags, consumer_prefixes=args.consumer_prefix or ()
    )
    entries = list(report.entries)
    if args.boundary_only:
        entries = [entry for entry in entries if entry.role == "boundary"]
    if args.kind:
        entries = [entry for entry in entries if entry.decl.kind == args.kind]

    if args.json:
        payload = {
            "library": library,
            "roots": roots,
            "tags": list(report.tags),
            "consumerPrefixes": args.consumer_prefix or [],
            "scopeDeclarations": report.scope_declarations,
            "taggedReachableDeclarations": len(report.tagged_reachable),
            "taggedReachableTheorems": sum(
                decl.kind == "theorem" for decl in report.tagged_reachable
            ),
            "neededDeclarations": len(report.entries),
            "neededTheorems": report.count_kind("theorem"),
            "boundaryDeclarations": sum(e.role == "boundary" for e in report.entries),
            "boundaryTheorems": report.count_kind("theorem", role="boundary"),
            "rows": [entry.to_json() for entry in entries],
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print(f"roots: {', '.join(roots)}")
    print(f"tags: {', '.join(report.tags)}")
    print(f"root-scope declarations: {report.scope_declarations}")
    print(
        "tagged but reachable: "
        f"{len(report.tagged_reachable)} declaration(s), "
        f"{sum(d.kind == 'theorem' for d in report.tagged_reachable)} theorem(s)"
    )
    print(
        "actually needed across the boundary: "
        f"{len(report.entries)} declaration(s), "
        f"{report.count_kind('theorem')} theorem(s)"
    )
    print(
        "direct boundary: "
        f"{sum(e.role == 'boundary' for e in report.entries)} declaration(s), "
        f"{report.count_kind('theorem', role='boundary')} theorem(s)"
    )
    print()
    width = max((len(entry.decl.short_name) for entry in entries), default=0)
    for entry in entries:
        if args.names:
            print(entry.decl.name)
            continue
        consumers = ""
        if entry.direct_consumers:
            shown = ", ".join(entry.direct_consumers[:3])
            extra = len(entry.direct_consumers) - 3
            consumers = f"  <- {shown}" + (f" (+{extra})" if extra else "")
        print(
            f"{entry.role:8s} {entry.decl.kind:8s} "
            f"{entry.decl.short_name:{width}s}  {entry.decl.location()}{consumers}"
        )
    return 0

def _graph_scope(project, targets, root_modules, include_lib, exclude_lib, lib):
    roots = list(dict.fromkeys(root_modules or ()))
    if not roots:
        if not targets:
            raise ProjectError("graph indexing needs a target declaration or --root-module")
        roots = project.declaration_modules(targets)
    if include_lib:
        libraries = list(dict.fromkeys(include_lib))
    elif lib:
        libraries = [lib]
    else:
        libraries = project.libraries_for_import_closure(roots)
    excluded = set(exclude_lib or ())
    libraries = [library for library in libraries if library not in excluded]
    if not libraries:
        raise ProjectError("graph scope contains no libraries")
    return roots, libraries


def _read_json_object(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise ProjectError(f"cannot read JSON payload {path}: {ex}") from ex
    if not isinstance(obj, dict):
        raise ProjectError(f"JSON payload {path} must contain an object")
    return obj


@profile
def cmd_graph_index(args) -> int:
    """Stage 1: build a reusable semantic dependency index.

    With no target/root arguments this indexes the project's ordinary Lake
    build surface.  Target/root arguments retain the narrower imported-
    environment mode for repositories where a smaller cache is useful.
    """
    project = find_project(Path(args.project) if args.project else None)
    targets = list(args.target or ())
    explicit_roots = list(args.root_module or ())
    project_mode = bool(args.whole_project or (not targets and not explicit_roots))
    if args.whole_project and (targets or explicit_roots):
        raise ProjectError("--whole-project cannot be combined with targets or --root-module")

    if project_mode:
        roots_by_library = project.project_graph_roots(
            include_libraries=args.include_lib or (),
            exclude_libraries=args.exclude_lib or (),
            all_libraries=bool(args.all_libraries),
            only_library=args.lib,
        )
        scope = "project-all-libraries" if args.all_libraries else "project-default-build"
        bootstrap_targets: list[str] = []
    else:
        roots, libraries = _graph_scope(
            project,
            targets,
            explicit_roots,
            args.include_lib,
            args.exclude_lib,
            args.lib,
        )
        roots_by_library = {library: list(roots) for library in libraries}
        scope = "imported-environment"
        bootstrap_targets = targets

    libraries = list(roots_by_library)
    refresh_libs = set(args.refresh_lib or ())
    unknown_refresh = sorted(refresh_libs - set(libraries))
    if unknown_refresh:
        raise ProjectError(
            "--refresh-lib names libraries outside this graph scope: "
            + ", ".join(unknown_refresh)
        )

    groups = []
    cache_rows = []
    unavailable_libraries: list[dict[str, object]] = []
    for library, roots in roots_by_library.items():
        unavailable_roots = project.unavailable_import_roots(roots) if args.all_libraries else []
        if unavailable_roots:
            reason = (
                "declared library has no importable build root "
                "(missing .olean artifact)"
            )
            if args.all_libraries and not getattr(args, "strict_all_libraries", False):
                unavailable_libraries.append(
                    {"library": library, "roots": list(roots), "unavailableRoots": unavailable_roots,
                     "reason": reason}
                )
                if not args.json:
                    print(
                        f"leanq: skipping {library}: {reason}: {', '.join(unavailable_roots)}",
                        file=sys.stderr,
                    )
                continue
            raise ProjectError(
                f"cannot index {library}: {reason}: {', '.join(unavailable_roots)}"
            )
        forced = bool(args.refresh or library in refresh_libs)
        before = scoped_index_cache_state(project, library, roots, detail="graph")
        path = before["path"]
        if before["current"] and not forced and not args.json:
            print(f"leanq: using current graph cache {path}", file=sys.stderr)
        groups.append(
            ensure_scoped_index(
                project,
                library,
                roots,
                refresh=forced,
                verbose=not args.json,
                detail="graph",
            )
        )
        cache_rows.append(
            {
                "library": library,
                "path": str(path),
                "roots": list(roots),
                "fingerprint": before.get("fingerprint"),
                "reused": bool(before["current"] and not forced),
                "rebuilt": bool(forced or not before["current"]),
            }
        )

    if not groups:
        raise ProjectError("graph scope contains no importable libraries")
    table = merge_declarations(groups)
    graph = environment_dependency_graph(table.values())
    payload = graph.to_json()
    payload["payloadKind"] = "semantic-index"
    payload["graphCacheVersion"] = GRAPH_CACHE_VERSION
    payload["project"] = str(project.root)
    payload["libraries"] = [row["library"] for row in cache_rows]
    payload["importRootsByLibrary"] = roots_by_library
    payload["importRoots"] = list(dict.fromkeys(
        root for roots in roots_by_library.values() for root in roots
    ))
    payload["bootstrapTargets"] = bootstrap_targets
    payload["scope"] = scope
    if project_mode:
        payload["projectDefaultTargets"] = project.default_targets()
    payload["cache"] = cache_rows
    payload["unavailableLibraries"] = unavailable_libraries
    components = strongly_connected_components(graph.nodes, graph.edges)
    cyclic = [list(component) for component in components if len(component) > 1]
    payload["cyclicComponentCount"] = len(cyclic)
    payload["cyclicComponents"] = cyclic
    unresolved_internal = sorted(
        {dependency for _, dependency in graph.unresolved if dependency.startswith("_private.")}
    )
    payload["unresolvedInternalDependencyCount"] = len(unresolved_internal)
    if unresolved_internal:
        payload["unresolvedInternalDependencies"] = unresolved_internal

    text = json.dumps(payload, indent=2) + "\n"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if args.json:
        sys.stdout.write(text)
    else:
        print(out)
        print(
            f"semantic index: {len(graph.nodes)} declarations, {len(graph.edges)} direct edges, "
            f"{len(cache_rows)} libraries [{scope}]; downstream queries are Lean-free",
            file=sys.stderr,
        )
    return 0


@profile
def cmd_graph_slice(args) -> int:
    """Stage 2 generic target slice from a saved semantic index, without Lean."""
    semantic_path = Path(args.semantic_index)
    payload = _read_json_object(semantic_path)
    if payload.get("payloadKind") != "semantic-index":
        raise ProjectError(
            f"{semantic_path} is not a reusable semantic index; run `leanq graph-index` first"
        )
    targets = list(args.target or ())
    if not targets:
        raise ProjectError("graph-slice needs at least one target declaration")
    decls = declarations_from_graph_payload(payload)
    graph = target_dependency_graph(decls, targets)
    reduced = transitive_reduction(graph.nodes, graph.edges) if args.transitive_reduction else None
    result = graph.to_json(reduced_edges=reduced)
    result["payloadKind"] = "graph-slice"
    result["sourceSemanticIndex"] = {
        "path": str(semantic_path),
        "scope": payload.get("scope"),
        "graphCacheVersion": payload.get("graphCacheVersion"),
    }
    result["project"] = payload.get("project")
    result["libraries"] = payload.get("libraries", [])
    text = json.dumps(result, indent=2) + "\n"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if args.json:
        sys.stdout.write(text)
    else:
        print(out)
        print(
            f"graph slice: {len(graph.nodes)} declarations, {len(graph.edges)} direct edges; no Lean invoked",
            file=sys.stderr,
        )
    return 0


@profile
def cmd_graph_headlines(args) -> int:
    """Stage 2: census-aware headline analysis, without Lean."""
    semantic_path = Path(args.semantic_index)
    payload = _read_json_object(semantic_path)
    if payload.get("payloadKind") != "semantic-index":
        raise ProjectError(
            f"{semantic_path} is not a reusable semantic index; run `leanq graph-index ...` first"
        )
    view = getattr(args, "view", "dependencies")
    targets = list(args.target or ())
    if view == "consumption" and not targets:
        targets = list(payload.get("bootstrapTargets") or ())
    if view == "consumption" and not targets:
        raise ProjectError("graph-headlines --view consumption needs --target")
    census_paths = [Path(path) for path in args.census]
    importances = args.importance or ["headline"]
    landmarks = [
        parse_consumption_landmark(spec)
        for spec in (getattr(args, "landmark", None) or ())
    ]
    result = analyze_headlines(
        payload,
        targets=targets,
        census_paths=census_paths,
        importances=importances,
        view=view,
        include_supporting=getattr(args, "include_supporting", False),
        default_claims=list(getattr(args, "default_claim", None) or ()),
        landmarks=landmarks,
        terminal_families=list(getattr(args, "terminal_family", None) or ()),
    )
    result["sourceSemanticIndex"]["path"] = str(semantic_path)
    text = json.dumps(result, indent=2) + "\n"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if args.json:
        sys.stdout.write(text)
    else:
        counts = result["headlineAnalysis"]["familyCounts"]
        print(out)
        if view == "dependencies":
            print(
                f"headline dependency union: {result['nodeCount']} declarations, "
                f"{result['edgeCount']} direct edges, "
                f"{result['headlineAnalysis']['headlineDeclarationCount']} headline landmarks, "
                f"{result['headlineAnalysis']['sharedDependencyCount']} shared dependencies; no Lean invoked",
                file=sys.stderr,
            )
        else:
            for family, row in counts.items():
                print(
                    f"{family}: {row['consumed']}/{row['claims']} headline claim(s) consumed",
                    file=sys.stderr,
                )
            library_counts = result["headlineAnalysis"].get("targetClosureLibraryCounts", {})
            if library_counts:
                print(
                    "target closure library machinery: "
                    + ", ".join(f"{name}={count}" for name, count in library_counts.items()),
                    file=sys.stderr,
                )
    return 0


@profile
def cmd_graph_html(args) -> int:
    """Stage 3: render saved graph/headline JSON to self-contained HTML, without Lean."""
    payload_path = Path(args.input)
    payload = _read_json_object(payload_path)
    census_paths = [Path(path) for path in (getattr(args, "census", None) or ())]
    if payload.get("payloadKind") == "semantic-index" and census_paths:
        payload = prepare_project_explorer(
            payload,
            census_paths=census_paths,
            importances=getattr(args, "importance", None) or ["headline"],
            targets=getattr(args, "target", None) or (),
            default_claims=list(getattr(args, "default_claim", None) or ()),
            boundary=getattr(args, "boundary", None) or "none",
            foundations=(
                Path(args.foundations) if getattr(args, "foundations", None) else None
            ),
        )
    out = Path(args.out)
    write_graph_html(out, payload, title=args.title)
    print(out)
    return 0


def _compact_comparison_payload(payload: dict) -> dict:
    """Keep exactly the union of all loaded census-claim dependency closures."""
    nodes = list(payload.get("nodes") or ())
    edges = list(payload.get("edges") or ())
    by_name = {row.get("id"): row for row in nodes if row.get("id")}
    analysis = payload.get("headlineAnalysis") or {}
    seeds: list[str] = []
    for claim in analysis.get("claims") or ():
        rows = claim.get("leafDeclarations") or claim.get("canonicalDeclarations") or ()
        for name in rows:
            if name in by_name and name not in seeds:
                seeds.append(name)
    if not seeds:
        return payload
    incoming: dict[str, list[str]] = collections.defaultdict(list)
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        if source in by_name and target in by_name:
            incoming[target].append(source)
    keep = set(seeds)
    queue = list(seeds)
    for name in queue:
        for dependency in incoming.get(name, ()):
            if dependency not in keep:
                keep.add(dependency)
                queue.append(dependency)
    result = dict(payload)
    result["nodes"] = [row for row in nodes if row.get("id") in keep]
    result["edges"] = [
        row for row in edges
        if row.get("source") in keep and row.get("target") in keep
    ]
    result["nodeCount"] = len(result["nodes"])
    result["edgeCount"] = len(result["edges"])
    result["comparisonScope"] = {
        "kind": "loaded-census-claim-union",
        "seedDeclarationCount": len(seeds),
        "declarationCount": len(result["nodes"]),
        "edgeCount": len(result["edges"]),
    }
    boundary = result.get("boundary")
    if isinstance(boundary, dict):
        boundary = dict(boundary)
        boundary_library = boundary.get("library", "Mathlib")
        boundary_ids = {
            row.get("id") for row in result["nodes"]
            if row.get("library") == boundary_library
        }
        boundary["declarationCount"] = len(boundary_ids)
        boundary["edgeCount"] = sum(
            row.get("source") in boundary_ids for row in result["edges"]
        )
        result["boundary"] = boundary
    return result


@profile
def cmd_graph_compare_html(args) -> int:
    """Render the package-first generic census ancestry/comparison publisher."""
    payload = _read_json_object(Path(args.input))
    census_paths = [Path(path) for path in (getattr(args, "census", None) or ())]
    if payload.get("payloadKind") == "semantic-index":
        if not census_paths:
            raise ProjectError("graph-compare-html needs --census with a semantic index")
        payload = prepare_project_explorer(
            payload,
            census_paths=census_paths,
            importances=getattr(args, "importance", None) or ["headline"],
            targets=getattr(args, "target", None) or (),
            default_claims=list(getattr(args, "default_claim", None) or ()),
            boundary=getattr(args, "boundary", None) or "none",
            foundations=(Path(args.foundations) if getattr(args, "foundations", None) else None),
        )
    if payload.get("payloadKind") not in {"project-explorer", "headline-dependencies"}:
        raise ProjectError(
            "graph-compare-html needs a semantic index plus censuses, or a saved explorer payload"
        )
    payload = _compact_comparison_payload(dict(payload))
    payload["comparisonPublisher"] = {
        "initialFamilies": list(dict.fromkeys(getattr(args, "family", None) or ())),
        "overlayFamily": getattr(args, "overlay_family", None),
    }
    out = Path(args.out)
    write_comparison_html(out, payload, title=args.title)
    print(out)
    return 0


@profile
def cmd_graph(args) -> int:
    """Exact project-local declaration graph and optional interactive viewer."""
    project = find_project(Path(args.project) if args.project else None)
    presentation_spec = (
        load_presentation(Path(args.presentation)) if args.presentation else None
    )
    targets = list(args.target or ())
    if not targets and presentation_spec is not None:
        targets = list(presentation_spec.targets)
    if not targets:
        raise ProjectError(
            "graph needs at least one target declaration, either positionally or in --presentation"
        )

    root_modules = list(dict.fromkeys(args.root_module or ()))
    if not root_modules:
        root_modules = project.declaration_modules(targets)

    if args.include_lib:
        libraries = list(dict.fromkeys(args.include_lib))
    elif args.lib:
        libraries = [args.lib]
    else:
        libraries = project.libraries_for_import_closure(root_modules)
    excluded = set(args.exclude_lib or ())
    libraries = [library for library in libraries if library not in excluded]
    if not libraries:
        raise ProjectError("graph scope contains no libraries")

    groups = []
    for library in libraries:
        groups.append(
            ensure_scoped_index(
                project,
                library,
                root_modules,
                refresh=args.refresh,
                verbose=not args.json,
                detail="graph",
            )
        )
    table = merge_declarations(groups)
    graph = target_dependency_graph(table.values(), targets)
    need_reduction = args.transitive_reduction or bool(args.html)
    reduced = (
        transitive_reduction(graph.nodes, graph.edges) if need_reduction else None
    )
    payload = graph.to_json(reduced_edges=reduced)
    payload["project"] = str(project.root)
    payload["libraries"] = libraries
    payload["importRoots"] = root_modules
    payload["scope"] = "target-import-closure"
    components = strongly_connected_components(graph.nodes, graph.edges)
    cyclic = [list(component) for component in components if len(component) > 1]
    payload["cyclicComponentCount"] = len(cyclic)
    payload["cyclicComponents"] = cyclic
    unresolved_internal = sorted(
        {dependency for _, dependency in graph.unresolved if dependency.startswith("_private.")}
    )
    payload["unresolvedInternalDependencyCount"] = len(unresolved_internal)
    if unresolved_internal:
        payload["unresolvedInternalDependencies"] = unresolved_internal
        shown = ", ".join(unresolved_internal[:3])
        extra = len(unresolved_internal) - 3
        suffix = f", ... (+{extra})" if extra else ""
        print(
            "leanq: warning: exact graph has unresolved private support dependencies: "
            f"{shown}{suffix}",
            file=sys.stderr,
        )
    if args.include_unresolved:
        payload["unresolvedDependencies"] = [
            {"consumer": consumer, "dependency": dependency}
            for consumer, dependency in graph.unresolved
        ]

    if presentation_spec is not None or args.headline:
        payload["presentation"] = build_presentation(
            graph,
            presentation_spec,
            extra_headlines=args.headline or (),
            title=args.title,
            subtitle=args.subtitle,
            strict=args.strict_presentation,
        )
    elif args.title or args.subtitle:
        payload["presentation"] = build_presentation(
            graph,
            None,
            title=args.title,
            subtitle=args.subtitle,
            strict=args.strict_presentation,
        )

    presentation = payload.get("presentation") or {}
    missing_headlines = presentation.get("missingHeadlines") or []
    if missing_headlines:
        shown = ", ".join(repr(name) for name in missing_headlines[:5])
        extra = len(missing_headlines) - 5
        suffix = f", ... (+{extra})" if extra else ""
        print(
            "leanq: warning: presentation skipped "
            f"{len(missing_headlines)} headline(s) not in the target dependency closure: "
            f"{shown}{suffix}",
            file=sys.stderr,
        )

    text = json.dumps(payload, indent=2) + "\n"
    written = []
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        written.append(out)
    if args.html:
        html_path = Path(args.html)
        if args.out and html_path.resolve() == Path(args.out).resolve():
            raise ProjectError("--html and --out must name different files")
        write_graph_html(html_path, payload, title=args.title)
        written.append(html_path)

    if args.json:
        sys.stdout.write(text)
    elif written:
        for path in written:
            print(path)
    else:
        print(f"targets: {', '.join(graph.targets)}")
        print(f"libraries: {', '.join(libraries)}")
        print(f"nodes: {len(graph.nodes)}")
        print(f"direct edges: {len(graph.edges)}")
        if reduced is not None:
            print(f"transitive-reduction edges: {len(reduced)}")
        print(f"cyclic components: {len(cyclic)}")
        print(f"unresolved boundary dependencies: {len(graph.unresolved)}")
    return 0


def cmd_axioms(args) -> int:
    project, library = _resolve(args)
    decls = ensure_index(project, library, refresh=args.refresh, verbose=not args.json)
    matches = [d for d in decls if d.name == args.decl or d.short_name == args.decl]
    if not matches:
        print(f"no declaration named {args.decl!r}", file=sys.stderr)
        return 1
    if args.json:
        json.dump([d.to_json() for d in matches], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    for decl in matches:
        print(f"{decl.name}  ({decl.kind}, {decl.module})")
        axioms = decl.axioms if decl.axioms is not None else ("(not indexed)",)
        for axiom in axioms or ("(none)",):
            print(f"    {axiom}")
    return 0


def cmd_statement(args) -> int:
    """What a declaration's statement means: its type, unfolded through project definitions."""
    project = find_project(Path(args.project) if args.project else None)
    seeds = list(dict.fromkeys(args.decl or ()))
    if not seeds and not args.all:
        raise ProjectError("statement needs at least one declaration, or --all for a whole library")
    if seeds and args.all:
        raise ProjectError("pass declarations or --all, not both")
    boundary = (
        tuple(p for p in args.boundary.split(",") if p) if args.boundary else DEFAULT_BOUNDARY
    )

    def single_library() -> str:
        if args.lib:
            return args.lib
        libs = project.libraries()
        if len(libs) != 1:
            raise ProjectError(
                f"{project.root} builds {len(libs)} libraries ({', '.join(libs) or 'none'}); "
                f"pass --lib"
            )
        return libs[0]

    if args.all:
        library = single_library()
        modules = None
        sidecar_seeds = None
    else:
        sidecar_seeds = seeds
        modules = list(dict.fromkeys(args.root_module or ()))
        if not modules:
            try:
                modules = project.declaration_modules(seeds)
            except ProjectError:
                # A seed declared outside the project's own sources (a Lake package
                # constant, say) cannot be located by the source scan; import the
                # whole library so the elaborated environment can still resolve it.
                modules = None
        if args.lib:
            library = args.lib
        elif modules:
            library = project.library_for_module(modules[0]) or single_library()
        else:
            library = single_library()
        if modules is None:
            modules = project.modules(library)

    path, records = ensure_statement_sidecar(
        project,
        library,
        seeds=sidecar_seeds,
        modules=modules,
        boundary=boundary,
        refresh=args.refresh,
        verbose=not args.json,
    )
    table = statements_by_name(records)
    if not seeds:
        seeds = [record.name for record in records if record.role == "seed"]
    missing = [seed for seed in seeds if seed not in table or table[seed].missing]
    if missing and not args.json:
        print(
            "leanq: no statement record for "
            + ", ".join(missing)
            + " (pass the fully qualified constant name)",
            file=sys.stderr,
        )

    if args.json or args.out:
        payload = closure_payload(table, seeds, meta=load_sidecar_meta(path))
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            if not args.json:
                print(out)
        if args.json:
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")
        return 1 if missing else 0

    for seed in seeds:
        sys.stdout.write(
            render_closure_text(
                table,
                seed,
                show_boundary=not args.hide_boundary,
                show_docstrings=args.docstrings,
            )
        )
        sys.stdout.write("\n")
    return 1 if missing else 0


def cmd_libs(args) -> int:
    project = find_project(Path(args.project) if args.project else None)
    for library in project.built_roots():
        marker = " (indexed)" if index_path(project, library).exists() else ""
        print(f"{library}{marker}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leanq",
        description=(
            "Query a built Lean 4 library from the elaborator. Declaration kinds, whether a "
            "definition returns Prop, and whether it depends on sorryAx are read out of the "
            "environment, so multi-line signatures and neighbouring declarations cannot skew "
            "the answer the way a regex does."
        ),
    )
    parser.add_argument("--project", help="path inside the Lean project (default: cwd)")
    parser.add_argument("--lib", help="library root module (default: the only built one)")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(sp) -> None:
        """`--json` reads naturally after the subcommand; accept it in both positions."""
        sp.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON"
        )


    p = sub.add_parser("libs", help="list built libraries")
    add_common(p)
    p.set_defaults(func=cmd_libs)

    p = sub.add_parser("index", help="build the declaration index")
    add_common(p)
    p.add_argument("--out", help="write the JSONL here instead of <project>/.leanq/")
    p.set_defaults(func=cmd_index)

    def add_query_flags(sp, *, with_sorried: bool = True) -> None:
        add_common(sp)
        sp.add_argument("--refresh", action="store_true", help="rebuild the index first")
        sp.add_argument("--kind", choices=["def", "theorem", "axiom", "inductive", "ctor"])
        sp.add_argument("--module", help="substring match on the module name")
        sp.add_argument("--name", help="substring match on the declaration name")
        sp.add_argument("--axiom", help="substring match within the axiom closure")
        sp.add_argument("--names", action="store_true", help="print bare names")
        sp.add_argument("--uses", help="only declarations referencing this constant")
        if with_sorried:
            group = sp.add_mutually_exclusive_group()
            group.add_argument(
                "--sorried", dest="sorried", action="store_const", const=True,
                help="only declarations depending on sorryAx",
            )
            group.add_argument(
                "--no-sorried", dest="sorried", action="store_const", const=False,
                help="only declarations free of sorryAx",
            )
            sp.set_defaults(sorried=None)
        pv = sp.add_mutually_exclusive_group()
        pv.add_argument(
            "--prop-valued", dest="prop_valued", action="store_const", const=True,
            help="only declarations returning Prop (predicates)",
        )
        pv.add_argument(
            "--not-prop-valued", dest="prop_valued", action="store_const", const=False,
        )
        sp.set_defaults(prop_valued=None, is_prop=None)

    p = sub.add_parser("query", help="filter declarations")
    add_query_flags(p)
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("stubs", help="declarations that depend on sorryAx")
    add_query_flags(p, with_sorried=False)
    p.set_defaults(func=cmd_stubs)

    p = sub.add_parser("stats", help="per-module def/theorem stub counts")
    add_common(p)
    p.add_argument("--refresh", action="store_true")
    p.add_argument(
        "--depth", type=int, default=2,
        help="group modules by this many name components (0 = full module)",
    )
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser(
        "deps", help="what a declaration references (--local: only same-library helpers)"
    )
    add_common(p)
    p.add_argument("decl", help="full or short declaration name")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--names", action="store_true")
    p.add_argument(
        "--transitive", action="store_true", help="follow dependencies all the way down"
    )
    p.add_argument(
        "--local", action="store_true",
        help="only declarations from this library, followed transitively -- the "
             "'what would I have to bring along' question",
    )
    p.set_defaults(func=cmd_deps)

    p = sub.add_parser("rdeps", help="declarations that reference this one")
    add_common(p)
    p.add_argument("decl", help="full or short declaration name")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--names", action="store_true")
    p.add_argument("--module")
    p.add_argument("--kind", choices=["def", "theorem", "axiom", "inductive", "ctor"])
    p.set_defaults(func=cmd_rdeps, sorried=None, prop_valued=None, is_prop=None)

    p = sub.add_parser(
        "promotions",
        help="tagged declarations actually required by declarations under a production root",
    )
    add_common(p)
    p.add_argument(
        "--root", action="append",
        help="root module to import (repeatable; default: the library root)",
    )
    p.add_argument(
        "--tag", action="append",
        help="exact module-name component treated as experimental (default: Experimental, MathAhead)",
    )
    p.add_argument(
        "--consumer-prefix", action="append",
        help="only count boundary users in modules with this prefix (repeatable)",
    )
    p.add_argument(
        "--kind", choices=["def", "theorem", "axiom", "inductive", "ctor"],
        help="filter displayed rows by declaration kind; summary remains unfiltered",
    )
    p.add_argument(
        "--boundary-only", action="store_true",
        help="show only declarations referenced directly from an untagged module",
    )
    p.add_argument("--refresh", action="store_true", help="rebuild the root-scoped index")
    p.add_argument("--names", action="store_true", help="print bare declaration names")
    p.set_defaults(func=cmd_promotions)

    p = sub.add_parser(
        "graph-index",
        help="stage 1: build a reusable imported-environment semantic graph index",
    )
    add_common(p)
    p.add_argument(
        "target", nargs="*",
        help="optional declaration(s) for a narrow imported-environment index; omit for the reusable project graph",
    )
    p.add_argument(
        "--whole-project", action="store_true",
        help="explicitly index the project's ordinary Lake/default-target build surface (also the default when no target/root is given)",
    )
    p.add_argument(
        "--all-libraries", action="store_true",
        help="with project indexing, include every declared local lean_lib, including optional/non-default libraries",
    )
    p.add_argument(
        "--strict-all-libraries", action="store_true",
        help="with --all-libraries, fail instead of recording a declared library with no importable build root as unavailable",
    )
    p.add_argument(
        "--root-module", action="append",
        help="module root to import instead of inferring it from target declarations",
    )
    p.add_argument(
        "--include-lib", action="append",
        help="library whose declarations should be present in the semantic index (repeatable)",
    )
    p.add_argument(
        "--exclude-lib", action="append",
        help="remove a library from the inferred graph scope (repeatable)",
    )
    p.add_argument(
        "--refresh", action="store_true",
        help="rebuild every per-library graph cache before aggregating",
    )
    p.add_argument(
        "--refresh-lib", action="append",
        help="rebuild only this library's graph cache (repeatable)",
    )
    p.add_argument("--out", required=True, help="write the reusable semantic-index JSON here")
    p.set_defaults(func=cmd_graph_index)

    p = sub.add_parser(
        "graph-slice",
        help="stage 2: derive a target dependency subgraph from a saved semantic index",
    )
    add_common(p)
    p.add_argument("semantic_index", help="JSON written by `leanq graph-index`")
    p.add_argument("target", nargs="+", help="target declaration(s) to slice from the reusable graph")
    p.add_argument(
        "--transitive-reduction", action="store_true",
        help="also emit a reachability-preserving reduced edge set",
    )
    p.add_argument("--out", required=True, help="write the target graph JSON here")
    p.set_defaults(func=cmd_graph_slice)

    p = sub.add_parser(
        "graph-headlines",
        help="stage 2: derive headline dependency or consumption analysis from a saved semantic index",
    )
    add_common(p)
    p.add_argument("semantic_index", help="JSON written by `leanq graph-index`")
    p.add_argument(
        "--target", action="append",
        help="optional downstream target annotation (required only for --view consumption)",
    )
    p.add_argument(
        "--view", choices=("dependencies", "consumption"), default="dependencies",
        help="real dependency-union view (default) or compact landmark target-consumption view",
    )
    p.add_argument(
        "--include-supporting", action="store_true",
        help="also seed the dependency union from reviewed supporting declarations",
    )
    p.add_argument(
        "--census", action="append", required=True,
        help="full-source census JSON to include (repeatable)",
    )
    p.add_argument(
        "--importance", action="append",
        help="census importance to include (repeatable; default: headline)",
    )
    p.add_argument(
        "--default-claim", action="append",
        help=(
            "census claim id selected when the viewer loads (repeatable); "
            "default: every headline theorem row"
        ),
    )
    p.add_argument(
        "--landmark", action="append",
        metavar="LABEL=LIBRARY",
        help=(
            "public library seam to expose in --view consumption (repeatable, in route order)"
        ),
    )
    p.add_argument(
        "--terminal-family", action="append",
        help=(
            "downstream census family for --view consumption; repeatable. "
            "Default: infer from declarations in the selected target library"
        ),
    )
    p.add_argument("--out", required=True, help="write the headline-analysis JSON here")
    p.set_defaults(func=cmd_graph_headlines)

    p = sub.add_parser(
        "graph-html",
        help="stage 3: render a saved graph/headline JSON payload to HTML without Lean",
    )
    add_common(p)
    p.add_argument("input", help="saved leanq graph or headline-analysis JSON")
    p.add_argument(
        "--census", action="append",
        help="annotate a semantic index with census landmarks (repeatable)",
    )
    p.add_argument(
        "--importance", action="append",
        help="census importance to include (repeatable; default: headline)",
    )
    p.add_argument(
        "--target", action="append",
        help="optional downstream target annotation for census landmarks",
    )
    p.add_argument(
        "--default-claim", action="append",
        help=(
            "census claim id selected when the viewer loads (repeatable); "
            "default: every headline theorem row"
        ),
    )
    p.add_argument(
        "--boundary", choices=("none", "headline", "project"), default="none",
        help=(
            "include the Mathlib/Lean declarations the project depends on: not at all "
            "(default), for consumers in the headline dependency union, or for the whole "
            "project (very large)"
        ),
    )
    p.add_argument(
        "--foundations",
        help=(
            "CSV with `module` and `theory_id` columns grouping modules into the paper's "
            "named mathematical foundations; labels are read from "
            "formalization_basic_theories.csv beside it when present"
        ),
    )
    p.add_argument("--out", required=True, help="write the self-contained HTML here")
    p.add_argument("--title", help="override the HTML title")
    p.set_defaults(func=cmd_graph_html)

    p = sub.add_parser(
        "graph-compare-html",
        help=(
            "render a package-first generic comparison of census theorem ancestry "
            "without Lean"
        ),
    )
    add_common(p)
    p.add_argument("input", help="semantic-index JSON or saved project-explorer JSON")
    p.add_argument(
        "--census", action="append",
        help="annotate a semantic index with census landmarks (repeatable)",
    )
    p.add_argument(
        "--importance", action="append",
        help="census importance to include (repeatable; default: headline)",
    )
    p.add_argument(
        "--target", action="append",
        help="optional downstream target annotation for census landmarks",
    )
    p.add_argument(
        "--default-claim", action="append",
        help="census claim id selected when the viewer loads (repeatable)",
    )
    p.add_argument(
        "--boundary", choices=("none", "headline", "project"), default="none",
        help="embed the external Mathlib/Lean boundary: none, headline closure, or whole project",
    )
    p.add_argument(
        "--foundations",
        help="CSV with `module` and `theory_id` columns used to label mathematical foundations",
    )
    p.add_argument(
        "--family", action="append",
        help=(
            "initial active census family (repeatable). With none, the viewer starts "
            "on the union of all loaded census ancestry"
        ),
    )
    p.add_argument(
        "--overlay-family",
        help="optional census family whose ancestry is highlighted but not compared",
    )
    p.add_argument("--out", required=True, help="write the self-contained HTML here")
    p.add_argument("--title", help="override the HTML title")
    p.set_defaults(func=cmd_graph_compare_html)

    p = sub.add_parser(
        "graph",
        help="project-local elaborated dependency graph for one or more target declarations",
    )
    add_common(p)
    p.add_argument(
        "target", nargs="*",
        help="full or unambiguous short declaration name; may come from --presentation instead",
    )
    p.add_argument(
        "--include-lib", action="append",
        help="library to include as graph nodes (repeatable; default: local libraries in the target module import closure)",
    )
    p.add_argument(
        "--root-module", action="append",
        help="module to import for the graph environment (repeatable; normally inferred from target declarations)",
    )
    p.add_argument(
        "--exclude-lib", action="append",
        help="remove a library from the default graph scope (repeatable)",
    )
    p.add_argument("--refresh", action="store_true", help="rebuild graph indexes first")
    p.add_argument(
        "--transitive-reduction", action="store_true",
        help="also emit a reachability-preserving reduced edge set",
    )
    p.add_argument(
        "--include-unresolved", action="store_true",
        help="include names outside the indexed project-local graph, e.g. Mathlib dependencies",
    )
    p.add_argument("--out", help="write the graph JSON payload to this path")
    p.add_argument(
        "--html", metavar="PATH",
        help="write a self-contained interactive HTML dependency viewer",
    )
    p.add_argument(
        "--presentation", metavar="JSON",
        help="curated exact-name presentation spec; may also provide graph targets",
    )
    p.add_argument(
        "--headline", action="append",
        help="add a declaration to the initial headline presentation (repeatable)",
    )
    p.add_argument(
        "--strict-presentation", action="store_true",
        help="fail if any curated headline is absent from the exact target dependency closure",
    )
    p.add_argument("--title", help="override the viewer/presentation title")
    p.add_argument("--subtitle", help="override the viewer/presentation subtitle")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser(
        "statement",
        help=(
            "what a declaration's statement means: its type and signature, unfolded through "
            "project definitions and structure fields down to the Mathlib boundary"
        ),
    )
    add_common(p)
    p.add_argument("decl", nargs="*", help="fully qualified declaration name(s)")
    p.add_argument(
        "--all", action="store_true",
        help="build the whole-library sidecar instead (every public declaration is a seed; slow)",
    )
    p.add_argument("--refresh", action="store_true", help="rebuild the sidecar first")
    p.add_argument(
        "--root-module", action="append",
        help="import exactly these modules instead of locating the seeds by source scan",
    )
    p.add_argument(
        "--boundary",
        help=(
            "comma-separated module prefixes that are emitted but never unfolded "
            f"(default: {','.join(DEFAULT_BOUNDARY)})"
        ),
    )
    p.add_argument("--out", help="write the closure payload JSON here")
    p.add_argument("--docstrings", action="store_true", help="show docstrings in the tree")
    p.add_argument(
        "--hide-boundary", action="store_true",
        help="omit boundary constants from the tree (the summary still counts them)",
    )
    p.set_defaults(func=cmd_statement)

    p = sub.add_parser("axioms", help="axiom closure of one declaration")
    add_common(p)
    p.add_argument("decl", help="full or short declaration name")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_axioms)

    return parser


@profile
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ProjectError as exc:
        print(f"leanq: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

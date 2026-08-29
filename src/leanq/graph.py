"""Semantic declaration dependency graphs built from leanq indexes.

The graph direction is dependency -> consumer: an edge ``A -> B`` means the
elaborated declaration ``B`` directly references ``A``.  This direction makes a
proof story read naturally from foundations toward paper-facing conclusions.

This module deliberately has no third-party dependencies.  The graphs used for
presentation are small target-focused closures, and the standard-library
implementations here keep ``leanq`` installable without a resolve step.
"""

from __future__ import annotations

import re
from collections import Counter, deque
from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Sequence

from .index import Decl
from .project import ProjectError

Edge = tuple[str, str]


@dataclass(frozen=True)
class DependencyGraph:
    """A declaration graph restricted to one or more target ancestors."""

    nodes: Mapping[str, Decl]
    edges: frozenset[Edge]
    targets: tuple[str, ...]
    unresolved: tuple[tuple[str, str], ...] = ()

    def to_json(self, *, reduced_edges: Iterable[Edge] | None = None) -> dict:
        """Stable JSON payload intended for visualization/front-end consumers."""
        nodes = sorted(self.nodes.values(), key=lambda d: (d.module, d.name))
        edges = sorted(self.edges)
        payload = {
            "schemaVersion": 1,
            "edgeDirection": "dependency-to-consumer",
            "targets": list(self.targets),
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "nodes": [
                {
                    "id": decl.name,
                    **decl.to_json(),
                    "target": decl.name in self.targets,
                }
                for decl in nodes
            ],
            "edges": [
                {"source": source, "target": target, "direct": True}
                for source, target in edges
            ],
            "unresolvedDependencyCount": len(self.unresolved),
            "unresolvedDependencyPrefixes": dict(
                sorted(
                    Counter(_name_prefix(dep) for _, dep in self.unresolved).items()
                )
            ),
        }
        if reduced_edges is not None:
            reduced = sorted(set(reduced_edges))
            payload["reducedEdgeCount"] = len(reduced)
            payload["reducedEdges"] = [
                {"source": source, "target": target}
                for source, target in reduced
            ]
        return payload


def _name_prefix(name: str) -> str:
    return name.split(".", 1)[0]


def resolve_decl_name(decls: Iterable[Decl], query: str) -> str:
    """Resolve a full name or an unambiguous short name."""
    rows = list(decls)
    exact = [decl.name for decl in rows if decl.name == query]
    if exact:
        return exact[0]
    short = sorted({decl.name for decl in rows if decl.short_name == query})
    if not short:
        raise ProjectError(f"no declaration named {query!r} in the graph indexes")
    if len(short) > 1:
        shown = ", ".join(short[:8])
        extra = len(short) - 8
        if extra:
            shown += f", ... (+{extra})"
        raise ProjectError(f"ambiguous declaration name {query!r}: {shown}")
    return short[0]


def _merge_optional_field(name: str, left, right, *, decl_name: str):
    """Merge optional semantic metadata, rejecting real disagreements."""
    if left is None:
        return right
    if right is None:
        return left
    if left != right:
        raise ProjectError(
            f"conflicting graph records for {decl_name}: semantic field "
            f"{name} differs ({left!r} vs {right!r})"
        )
    return left


def _merge_decl_records(left: Decl, right: Decl) -> Decl:
    """Reconcile two records for the same Lean environment constant.

    Lean module headers can list the same generated declaration under more than
    one importing module.  The declaration name is the environment identity, so
    differing module provenance alone is not a semantic conflict.  We accept
    such duplicates when their elaborated kind/dependencies/metadata agree and
    retain deterministic provenance for display.  Actual semantic disagreement
    remains an error.
    """
    if left.name != right.name:
        raise AssertionError("cannot merge declarations with different names")
    if left.kind != right.kind:
        raise ProjectError(
            f"conflicting graph records for {left.name}: semantic field kind "
            f"differs ({left.kind!r} vs {right.kind!r})"
        )
    if set(left.deps) != set(right.deps):
        left_only = sorted(set(left.deps) - set(right.deps))
        right_only = sorted(set(right.deps) - set(left.deps))
        raise ProjectError(
            f"conflicting graph records for {left.name}: direct dependencies differ "
            f"(left-only={left_only[:5]!r}, right-only={right_only[:5]!r})"
        )
    if left.internal != right.internal:
        raise ProjectError(
            f"conflicting graph records for {left.name}: semantic field internal "
            f"differs ({left.internal!r} vs {right.internal!r})"
        )

    is_prop = _merge_optional_field(
        "isProp", left.is_prop, right.is_prop, decl_name=left.name
    )
    prop_valued = _merge_optional_field(
        "propValued", left.prop_valued, right.prop_valued, decl_name=left.name
    )
    sorried = _merge_optional_field(
        "sorried", left.sorried, right.sorried, decl_name=left.name
    )

    if left.axioms is None:
        axioms = right.axioms
    elif right.axioms is None:
        axioms = left.axioms
    elif set(left.axioms) != set(right.axioms):
        raise ProjectError(
            f"conflicting graph records for {left.name}: semantic field axioms differs"
        )
    else:
        axioms = tuple(sorted(set(left.axioms)))

    # `module` and `line` are provenance/display metadata, not declaration
    # identity.  Pick a stable module independent of index/group order and keep
    # the line associated with that provenance when one is available.
    left_key = (
        left.module.count("."), len(left.module), left.module, left.line or -1
    )
    right_key = (
        right.module.count("."), len(right.module), right.module, right.line or -1
    )
    if left_key <= right_key:
        module = left.module
        line = left.line if left.line is not None else right.line
    else:
        module = right.module
        line = right.line if right.line is not None else left.line

    libraries = sorted({lib for lib in (left.library, right.library) if lib})
    library = libraries[0] if libraries else None
    return Decl(
        name=left.name,
        module=module,
        kind=left.kind,
        is_prop=is_prop,
        prop_valued=prop_valued,
        sorried=sorried,
        line=line,
        axioms=axioms,
        deps=tuple(sorted(set(left.deps))),
        library=library,
        internal=left.internal,
    )


def merge_declarations(groups: Iterable[Iterable[Decl]]) -> dict[str, Decl]:
    """Merge graph indexes by Lean declaration identity.

    Duplicate module-header provenance is reconciled; semantic conflicts still
    fail loudly so the graph cannot hide incompatible declaration records.
    """
    table: dict[str, Decl] = {}
    for group in groups:
        for decl in group:
            previous = table.get(decl.name)
            if previous is None:
                table[decl.name] = decl
            else:
                table[decl.name] = _merge_decl_records(previous, decl)
    return table


def environment_dependency_graph(decls: Iterable[Decl]) -> DependencyGraph:
    """Return every indexed declaration and project-local direct edge.

    This is the reusable stage-1 graph artifact.  Unlike
    :func:`target_dependency_graph`, it deliberately does not take an ancestor
    closure: any target present in the imported Lean environment can be sliced
    out later without invoking Lean again.
    """
    rows = list(decls)
    table = {decl.name: decl for decl in rows}
    edges: set[Edge] = set()
    unresolved: set[tuple[str, str]] = set()
    for consumer, decl in table.items():
        for dependency in decl.deps:
            if dependency == consumer:
                continue
            resolved = _resolve_dependency_name(table, decl, dependency)
            if resolved is not None:
                edges.add((resolved, consumer))
            else:
                unresolved.add((consumer, dependency))
    return DependencyGraph(
        nodes=table,
        edges=frozenset(edges),
        targets=(),
        unresolved=tuple(sorted(unresolved)),
    )


_GENERATED_SUFFIX = re.compile(r"\.(?:eq_\d+|eq_def|_eq_\d+)$")


def _reattribute_generated_equations(decls: list[Decl]) -> list[Decl]:
    """Give a generated equation lemma the library/module of the definition it unfolds.

    Lean attributes ``X.eq_1`` to whichever module first forced it, which need not
    be the module that declares ``X``. Across a multi-library index this can make an
    upstream package appear to depend on a downstream package. The edges are real;
    only the generated lemma's package attribution is an artifact.
    """
    table = {decl.name: decl for decl in decls}
    result: list[Decl] = []
    for decl in decls:
        match = _GENERATED_SUFFIX.search(decl.name)
        parent = table.get(decl.name[: match.start()]) if match else None
        if parent is not None and parent.library != decl.library:
            decl = replace(decl, library=parent.library, module=parent.module)
        result.append(decl)
    return result


def declarations_from_graph_payload(payload: Mapping) -> list[Decl]:
    """Rehydrate declaration rows from a saved leanq graph/index payload."""
    rows = payload.get("nodes")
    if not isinstance(rows, list):
        raise ProjectError("graph payload has no nodes list")
    decls: list[Decl] = []
    for row in rows:
        if not isinstance(row, dict) or "name" not in row:
            raise ProjectError("graph payload contains a malformed declaration node")
        decls.append(Decl.from_json(row))
    return _reattribute_generated_equations(decls)


def target_dependency_graph(
    decls: Iterable[Decl], targets: Sequence[str]
) -> DependencyGraph:
    """Return the project-local dependency closure of ``targets``.

    Dependencies absent from the supplied indexes are recorded as unresolved
    boundary names rather than invented as nodes.  In a project-local graph these
    are normally Mathlib, Lean, or an intentionally omitted library.
    """
    rows = list(decls)
    table = {decl.name: decl for decl in rows}
    resolved_targets = tuple(resolve_decl_name(rows, target) for target in targets)

    keep: set[str] = set(resolved_targets)
    edges: set[Edge] = set()
    unresolved: set[tuple[str, str]] = set()
    queue = deque(resolved_targets)
    while queue:
        consumer = queue.popleft()
        decl = table[consumer]
        for dependency in decl.deps:
            if dependency == consumer:
                continue
            dependency_name = _resolve_dependency_name(table, decl, dependency)
            if dependency_name is None:
                unresolved.add((consumer, dependency))
                continue
            edges.add((dependency_name, consumer))
            if dependency_name not in keep:
                keep.add(dependency_name)
                queue.append(dependency_name)

    # A dependency can be discovered before its own closure is scanned, so the
    # edge set above already contains every direct edge whose consumer is kept.
    nodes = {name: table[name] for name in keep}
    return DependencyGraph(
        nodes=nodes,
        edges=frozenset(edges),
        targets=resolved_targets,
        unresolved=tuple(sorted(unresolved)),
    )


def _resolve_dependency_name(
    table: Mapping[str, Decl], consumer: Decl, dependency: str
) -> str | None:
    """Resolve an environment dependency to an indexed declaration identity.

    Lean's used-constant traversal normally yields fully-qualified names.  Some
    elaborated declarations nevertheless retain a namespace-relative name in
    their value (notably a theorem referring to a sibling declaration).  A
    project graph indexes declaration identities by their full environment
    names, so treating that spelling as unresolved silently cuts a real edge.

    Resolve exact names first, then interpret the spelling relative to the
    consumer's enclosing namespaces from nearest to farthest.  Anything else
    remains an unresolved environment boundary; dependency edges are never
    inferred merely from a matching final name component.
    """
    if dependency in table:
        return dependency

    namespace = consumer.name.split(".")[:-1]
    for size in range(len(namespace), 0, -1):
        candidate = ".".join((*namespace[:size], dependency))
        if candidate in table:
            return candidate

    # A missing name is an external/boundary constant, not a license to bind its
    # final component to an unrelated local declaration.  In particular,
    # resolving ``OfNat.ofNat`` (or bare ``id``) to a project declaration with
    # the same short name manufactures large false dependency cones.
    return None


def _adjacency(nodes: Iterable[str], edges: Iterable[Edge]) -> dict[str, set[str]]:
    adj = {node: set() for node in nodes}
    for source, target in edges:
        if source in adj and target in adj:
            adj[source].add(target)
    return adj


def strongly_connected_components(
    nodes: Iterable[str], edges: Iterable[Edge]
) -> list[tuple[str, ...]]:
    """Deterministic iterative Kosaraju SCCs (safe for deep proof chains)."""
    node_list = sorted(set(nodes))
    adj = _adjacency(node_list, edges)
    rev = {node: set() for node in node_list}
    for source, targets in adj.items():
        for target in targets:
            rev[target].add(source)

    seen: set[str] = set()
    finish: list[str] = []
    for root in node_list:
        if root in seen:
            continue
        seen.add(root)
        stack: list[tuple[str, bool]] = [(root, False)]
        while stack:
            node, expanded = stack.pop()
            if expanded:
                finish.append(node)
                continue
            stack.append((node, True))
            for nxt in sorted(adj[node], reverse=True):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append((nxt, False))

    assigned: set[str] = set()
    components: list[tuple[str, ...]] = []
    for root in reversed(finish):
        if root in assigned:
            continue
        component: list[str] = []
        stack = [root]
        assigned.add(root)
        while stack:
            node = stack.pop()
            component.append(node)
            for nxt in sorted(rev[node], reverse=True):
                if nxt not in assigned:
                    assigned.add(nxt)
                    stack.append(nxt)
        components.append(tuple(sorted(component)))
    return sorted(components, key=lambda comp: comp[0])


def _topological_order(nodes: Iterable[int], edges: Iterable[tuple[int, int]]) -> list[int]:
    node_list = sorted(set(nodes))
    indegree = {node: 0 for node in node_list}
    adjacency = {node: set() for node in node_list}
    for source, target in edges:
        if target not in adjacency[source]:
            adjacency[source].add(target)
            indegree[target] += 1
    ready = deque(sorted(node for node, degree in indegree.items() if degree == 0))
    order: list[int] = []
    while ready:
        node = ready.popleft()
        order.append(node)
        for target in sorted(adjacency[node]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if len(order) != len(node_list):
        raise ProjectError("internal error: SCC condensation graph contains a cycle")
    return order


def _dag_transitive_reduction(
    nodes: Iterable[int], edges: Iterable[tuple[int, int]]
) -> set[tuple[int, int]]:
    """Unique transitive reduction of a DAG using descendant sets."""
    node_set = set(nodes)
    edge_set = set(edges)
    adjacency = _adjacency(node_set, edge_set)
    order = _topological_order(node_set, edge_set)
    reachable: dict[int, set[int]] = {node: set() for node in node_set}
    for node in reversed(order):
        for target in adjacency[node]:
            reachable[node].add(target)
            reachable[node].update(reachable[target])

    reduced: set[tuple[int, int]] = set()
    for source in order:
        direct = adjacency[source]
        redundant: set[int] = set()
        for via in direct:
            redundant.update(reachable[via] & direct)
        for target in direct - redundant:
            reduced.add((source, target))
    return reduced


def transitive_reduction(nodes: Iterable[str], edges: Iterable[Edge]) -> set[Edge]:
    """Reachability-preserving reduction, robust to rare dependency cycles.

    On a DAG this is the ordinary unique transitive reduction.  If generated
    constants form a cycle, SCCs are condensed first; all direct intra-component
    edges are retained and one deterministic direct edge represents each retained
    condensation edge.
    """
    node_set = set(nodes)
    edge_set = {(a, b) for a, b in edges if a in node_set and b in node_set and a != b}
    components = strongly_connected_components(node_set, edge_set)
    comp_of: dict[str, int] = {}
    for idx, component in enumerate(components):
        for node in component:
            comp_of[node] = idx

    comp_edges: set[tuple[int, int]] = set()
    representatives: dict[tuple[int, int], Edge] = {}
    intra: set[Edge] = set()
    for edge in sorted(edge_set):
        source, target = edge
        a, b = comp_of[source], comp_of[target]
        if a == b:
            intra.add(edge)
            continue
        pair = (a, b)
        comp_edges.add(pair)
        representatives.setdefault(pair, edge)

    reduced_comp = _dag_transitive_reduction(range(len(components)), comp_edges)
    return intra | {representatives[pair] for pair in reduced_comp}


def shortest_path(
    nodes: Iterable[str], edges: Iterable[Edge], source: str, target: str
) -> tuple[str, ...] | None:
    """A deterministic shortest witness path, inclusive of both endpoints."""
    adj = _adjacency(nodes, edges)
    queue = deque([source])
    previous: dict[str, str | None] = {source: None}
    while queue:
        node = queue.popleft()
        if node == target:
            break
        for nxt in sorted(adj.get(node, ())):
            if nxt not in previous:
                previous[nxt] = node
                queue.append(nxt)
    if target not in previous:
        return None
    path = []
    node: str | None = target
    while node is not None:
        path.append(node)
        node = previous[node]
    return tuple(reversed(path))


def projected_reduction(
    graph: DependencyGraph, keep: Iterable[str]
) -> list[dict]:
    """Project onto selected nodes while preserving dependency reachability.

    Each returned edge includes a shortest witness path through omitted support
    declarations.  Taking a transitive reduction after projection gives the
    compact, auditable graph wanted by presentation tooling.
    """
    selected = sorted(set(keep) & set(graph.nodes))
    reachability_edges: set[Edge] = set()
    witnesses: dict[Edge, tuple[str, ...]] = {}
    for source in selected:
        for target in selected:
            if source == target:
                continue
            path = shortest_path(graph.nodes, graph.edges, source, target)
            if path is not None:
                edge = (source, target)
                reachability_edges.add(edge)
                witnesses[edge] = path
    reduced = transitive_reduction(selected, reachability_edges)
    return [
        {
            "source": source,
            "target": target,
            "witness": list(witnesses[(source, target)]),
        }
        for source, target in sorted(reduced)
    ]

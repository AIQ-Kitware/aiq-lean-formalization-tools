"""Classify declarations that cross out of experimental/tagged modules.

This module is intentionally pure Python over an already elaborated ``leanq``
index.  Lean answers the semantic question "which constants does this declaration
actually use?"; this layer only classifies those dependency edges by module path.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from ._profile import profile
from .index import Decl


DEFAULT_TAGS = ("Experimental", "MathAhead")


def module_is_tagged(module: str, tags: Iterable[str] = DEFAULT_TAGS) -> bool:
    """Whether a module has one of ``tags`` as an exact dotted-name component."""
    components = set(module.split("."))
    return any(tag in components for tag in tags)


@dataclass(frozen=True)
class PromotionEntry:
    """One tagged declaration needed by an untagged declaration closure."""

    decl: Decl
    role: str  # ``boundary`` (directly used outside) or ``support`` (tagged helper)
    direct_consumers: tuple[str, ...] = ()

    def to_json(self) -> dict:
        row = self.decl.to_json()
        row.update(
            {
                "role": self.role,
                "directConsumers": list(self.direct_consumers),
            }
        )
        return row


@dataclass(frozen=True)
class PromotionReport:
    """Promotion boundary for one elaborated root scope."""

    tags: tuple[str, ...]
    scope_declarations: int
    tagged_reachable: tuple[Decl, ...]
    entries: tuple[PromotionEntry, ...]

    @property
    def needed(self) -> tuple[Decl, ...]:
        return tuple(entry.decl for entry in self.entries)

    def count_kind(self, kind: str, *, role: str | None = None) -> int:
        return sum(
            entry.decl.kind == kind and (role is None or entry.role == role)
            for entry in self.entries
        )


@profile
def promotion_report(
    decls: Iterable[Decl],
    *,
    tags: Iterable[str] = DEFAULT_TAGS,
    consumer_prefixes: Iterable[str] = (),
) -> PromotionReport:
    """Find tagged declarations that non-tagged declarations actually depend on.

    ``decls`` should normally be a *root-scoped* index, e.g. the declarations
    visible after importing one library root rather than every module Lake happened
    to build. A tagged declaration is a direct ``boundary`` crossing when an
    untagged declaration references it.  Tagged declarations needed transitively
    by those boundary declarations are ``support``.

    This is deliberately stricter than "the module is imported": an aggregate can
    import experimental material that no production declaration actually uses.
    Conversely, following the elaborated dependency graph catches theorem use hidden
    behind notation, wrappers, or generated proof terms that source grep can miss.
    """
    tags_tuple = tuple(dict.fromkeys(tags))
    prefixes = tuple(dict.fromkeys(consumer_prefixes))
    decl_list = list(decls)
    tagged = {
        decl.name: decl
        for decl in decl_list
        if module_is_tagged(decl.module, tags_tuple)
    }

    direct_consumers: dict[str, set[str]] = defaultdict(set)
    for consumer in decl_list:
        # We already classified every tagged declaration above; avoid splitting every
        # consumer module name a second time on large indexes.
        if consumer.name in tagged:
            continue
        if prefixes and not any(consumer.module.startswith(prefix) for prefix in prefixes):
            continue
        for dep in consumer.deps:
            if dep in tagged:
                direct_consumers[dep].add(consumer.name)

    needed = set(direct_consumers)
    frontier = list(needed)
    while frontier:
        name = frontier.pop()
        decl = tagged[name]
        for dep in decl.deps:
            if dep not in tagged or dep in needed:
                continue
            needed.add(dep)
            frontier.append(dep)

    entries = []
    for name in sorted(needed, key=lambda n: (tagged[n].module, n)):
        consumers = tuple(sorted(direct_consumers.get(name, ())))
        entries.append(
            PromotionEntry(
                decl=tagged[name],
                role="boundary" if consumers else "support",
                direct_consumers=consumers,
            )
        )

    return PromotionReport(
        tags=tags_tuple,
        scope_declarations=len(decl_list),
        tagged_reachable=tuple(sorted(tagged.values(), key=lambda d: (d.module, d.name))),
        entries=tuple(entries),
    )

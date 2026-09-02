"""What the server has finished preparing, and what it is still preparing.

A static page has one state: written or not. A server has several, and the
expensive ones are invisible from the outside -- scanning the Lean sources takes
about ten seconds, parsing a saved dependency graph another second and a half,
and indexing every ledger for cross-references longer still. A UI that does not
know this either blocks on the first click or shows a spinner that cannot say
what it is waiting for.

So readiness is explicit. Each subsystem is a `Stage` that moves
pending -> working -> ready (or failed, with the reason kept), and the whole set
is published over ``/api/ready``. The shell shows what is still coming and lets
you use everything that has arrived.

Warming runs in the background at startup in dependency order, so the first
click is usually free rather than paying for the scan itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Stage:
    name: str
    label: str
    #: What is unavailable until this is ready, for the UI to explain itself.
    provides: str
    state: str = "pending"  # pending | working | ready | failed
    detail: str = ""
    seconds: float = 0.0
    started: float = 0.0

    def as_json(self) -> dict[str, Any]:
        out = {
            "name": self.name,
            "label": self.label,
            "provides": self.provides,
            "state": self.state,
            "seconds": round(self.seconds, 2),
        }
        if self.detail:
            out["detail"] = self.detail
        return out


class Readiness:
    """The set of stages, and the running of them."""

    def __init__(self) -> None:
        self.stages: dict[str, Stage] = {}
        self.order: list[str] = []

    def declare(self, name: str, label: str, provides: str) -> Stage:
        stage = Stage(name=name, label=label, provides=provides)
        self.stages[name] = stage
        self.order.append(name)
        return stage

    def run(self, name: str, work: Callable[[], Any], *, describe: Callable[[Any], str] | None = None) -> Any:
        """Run one stage, recording how it went either way."""
        stage = self.stages[name]
        stage.state, stage.started = "working", time.perf_counter()
        try:
            value = work()
        except Exception as ex:  # a stage that fails must not take the server with it
            stage.state = "failed"
            stage.seconds = time.perf_counter() - stage.started
            stage.detail = f"{type(ex).__name__}: {ex}"[:300]
            return None
        stage.seconds = time.perf_counter() - stage.started
        stage.state = "ready"
        if describe:
            try:
                stage.detail = describe(value)
            except Exception:
                pass
        return value

    # -- reporting ---------------------------------------------------------

    def as_json(self) -> dict[str, Any]:
        stages = [self.stages[n].as_json() for n in self.order]
        done = sum(1 for s in stages if s["state"] in ("ready", "failed"))
        working = next((s for s in stages if s["state"] == "working"), None)
        return {
            "ready": done == len(stages),
            "done": done,
            "total": len(stages),
            "working": working["label"] if working else None,
            "stages": stages,
        }

    def is_ready(self, name: str) -> bool:
        stage = self.stages.get(name)
        return bool(stage and stage.state == "ready")

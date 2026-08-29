"""Starter document templates for new formalization projects."""
from __future__ import annotations

from typing import Any


def census_template(title: str, *, family: str = "source-census") -> dict[str, Any]:
    if family not in {"source-census", "source-completion-census", "source-semantic-alignment"}:
        raise ValueError(f"unknown census family {family!r}")
    data: dict[str, Any] = {
        "schema_version": 1,
        "draft": True,
        "primary_source": {"citation": title},
        "how_to_use": (
            "Track one stable row per source result. Keep implementation state, compiler "
            "verification, and semantic alignment as separate evidence axes."
        ),
        "status_definitions": {
            "implemented": "A source-facing Lean result exists.",
            "adapted": "A documented source repair or modeling adaptation is implemented.",
            "refuted": "The printed claim is covered by a machine-checked refutation or counterexample.",
            "partial": "Some required formal content exists, but the source result is not fully represented.",
            "planned": "The source result is tracked but not implemented.",
            "out_of_scope": "Tracked source material that is not a formalization obligation.",
        },
        "verification_definitions": {
            "proved_in_build": "Every cited declaration resolves in the current build.",
            "partially_in_build": "Some cited declarations resolve in the current build.",
            "compiler_pending": "Declarations are present but compiler verification is pending.",
            "absent": "No cited Lean declaration resolves.",
            "not_applicable": "Compiler verification does not apply to this row.",
        },
        "importance_definitions": {
            "headline": "Central claim for the concise external review surface.",
            "major": "Important named theorem or consequence.",
            "supporting": "Substantive supporting result.",
            "technical": "Definition, proof ingredient, numerical work, or other completeness item.",
        },
        "gaps": {},
        "items": [],
    }
    if family == "source-semantic-alignment":
        data["census_kind"] = "source_semantic_alignment"
        data["paper"] = data.pop("primary_source")
        data["semantic_alignment_definitions"] = {
            "exact": "Same mathematical hypotheses and conclusion, modulo notation.",
            "equivalent_encoding": "Equivalent claim represented differently in Lean.",
            "by_composition": "Recovered from a chain of compiled declarations.",
            "generalized": "Lean proves a stronger or more general result.",
            "specialized": "Lean proves a restricted source case.",
            "stronger_hypotheses": "Lean assumes more than the source.",
            "source_repair": "Lean formalizes a documented repair of the printed source.",
            "missing": "No corresponding Lean result is currently represented.",
            "out_of_scope": "Tracked for context rather than as a theorem obligation.",
        }
    if family == "source-completion-census":
        data["completion_certification_definitions"] = {
            "accepted": "Independent completion review accepts this row at the declared scope.",
            "reopened_source": "Source specification or transcription needs review.",
            "reopened_math": "Mathematical coverage needs review.",
            "reopened_mapping": "Source-to-Lean mapping needs review.",
            "not_applicable": "Completion certification does not apply to this row.",
        }
    return data


def semantic_review_template(title: str, *, companion_census: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": 1,
        "draft": True,
        "review_kind": "source_semantic_review",
        "paper": {"citation": title},
        "purpose": "Clause-by-clause review of source claims against their Lean realizations.",
        "relation_definitions": {
            "exact": "The Lean clause expresses the source clause at the reviewed scope.",
            "equivalent_encoding": "The same mathematical condition is represented differently.",
            "generalized": "The Lean clause is stronger or more general.",
            "specialized": "The Lean clause covers a restricted source case.",
            "stronger_hypotheses": "The Lean result assumes more than the source clause.",
            "source_repair": "The Lean clause implements a documented source repair.",
            "missing": "The source clause currently has no Lean realization.",
        },
        "rows": [],
    }
    if companion_census:
        data["companion_census"] = companion_census
    return data


def formalization_manifest_template(name: str, *, sources: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "version": "v1",
        "project": {
            "name": name,
            "description": "Source-faithful Lean formalization with maintained census and semantic-review artifacts.",
        },
        "sources": [
            {"title": title, "type": "source", "relationship": "formalizes"}
            for title in sources
        ],
        "status": {
            "scope": "Record claimed source coverage independently from compiler verification and semantic alignment.",
            "sorry_count": 0,
            "axioms": [],
        },
    }


def grounding_policy_template() -> dict[str, Any]:
    return {
        "required_files": [],
        "required_declarations": {},
        "forbid": {
            "include": ["**/*.lean", "*.lean"],
            "admissions": False,
            "axioms": False,
            "patterns": [],
        },
    }

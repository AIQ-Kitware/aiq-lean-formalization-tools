from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import yaml
from typing import Mapping, Any, Sequence

from . import __version__
from .alignment import build_alignment_packet
from .common import unique_in_order
from .aggregates import generate_aggregates
from .audits import (
    admission_report,
    declaration_drift,
    proof_length_census,
    source_audit_summary,
    undocumented_public,
    write_declaration_snapshot,
)
from .checklist import update_checklists
from .census import load_census
from .common import Path, atomic_write_text, load_json, parse_scalar
from .coverage import load_coverage_bundle, load_result_inventory, load_source_atom_inventory
from .certification import CertificationPlan, archive_certification_bundle, build_certification_bundle
from .errors import FormalizationToolsError
from .foundations import FoundationMap, check_foundation_map
from .gates import GateSuiteConfig, discover_gates, run_gate_suite
from .grounding import check_grounding_policy, load_grounding_policy
from .history import history_summary, load_git_history, render_history_html
from .import_graph import SourceImportGraph
from .hygiene import conflict_markers, orphan_build_modules, remove_orphan_build_modules
from .import_policy import ImportPolicy, check_import_policy
from .baseline import Baseline
from .lean_source import declaration_source_texts, module_in_scope, scan_lean_project
from .literature import load_literature
from .manifest import load_manifest
from .module_export import ModuleExportPolicy, export_modules
from .module_plan import ModulePlanPolicy, check_module_plan
from .module_migration import migrate_module_file
from .module_coverage import ModuleCoveragePolicy, check_module_coverage
from .namespace_policy import check_namespace_policy, load_namespace_policy
from .provenance import provenance_inventory
from .ratchet import evaluate_ratchets, load_ratchet_policy
from .roadmap import compare_roadmap
from .semantic_review import load_semantic_review
from .signatures import SignaturePolicy, compare_signatures
from .staging import StagingPolicy, check_staging
from .source_candidates import (
    dead_definition_candidates,
    definitional_escape_candidates,
    duplicate_candidates,
    largest_declarations,
    name_candidates,
    review_profiles,
)
from .symbol_census import symbol_census
from .templates import census_template, formalization_manifest_template, grounding_policy_template, semantic_review_template
from .workspace import FormalizationWorkspace


def _dump(data: Any) -> None:
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def _print_findings(findings, *, json_mode: bool = False) -> int:
    errors = [f for f in findings if f.level == "error"]
    if json_mode:
        _dump({"ok": not errors, "findings": [f.to_json() for f in findings]})
    else:
        for f in findings:
            prefix = f"{f.location}: " if f.location else ""
            print(f"{f.level.upper():7s} {prefix}[{f.code}] {f.message}")
        print(f"{len(errors)} error(s), {sum(f.level == 'warning' for f in findings)} warning(s)")
    return 1 if errors else 0


def _sets(values: Sequence[str]) -> list[tuple[str, Any]]:
    out = []
    for text in values:
        if "=" not in text:
            raise FormalizationToolsError(f"--set expects FIELD=VALUE, got {text!r}")
        field, value = text.split("=", 1)
        if not field:
            raise FormalizationToolsError("--set field cannot be empty")
        out.append((field, parse_scalar(value)))
    return out


def _pairs(values: Sequence[str], *, option: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for text in values:
        if "=" not in text:
            raise FormalizationToolsError(f"{option} expects LABEL=VALUE, got {text!r}")
        label, value = text.split("=", 1)
        if not label.strip() or not value.strip():
            raise FormalizationToolsError(f"{option} expects non-empty LABEL=VALUE")
        out.append((label.strip(), value.strip()))
    return out


def cmd_census_init(args) -> int:
    path = Path(args.path).expanduser()
    if path.exists() and not args.force:
        raise FormalizationToolsError(f"refusing to overwrite existing file: {path}")
    from .common import atomic_write_json
    atomic_write_json(path, census_template(args.title, family=args.family))
    print(path)
    return 0


def cmd_census_show(args) -> int:
    row = load_census(args.path, root=args.root).row(args.id)
    if args.json:
        _dump(row)
    else:
        print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def cmd_review_init(args) -> int:
    path = Path(args.path).expanduser()
    if path.exists() and not args.force:
        raise FormalizationToolsError(f"refusing to overwrite existing file: {path}")
    from .common import atomic_write_json
    atomic_write_json(path, semantic_review_template(args.title, companion_census=args.companion_census))
    print(path)
    return 0


def cmd_review_show(args) -> int:
    row = load_semantic_review(args.path, root=args.root).row(args.id)
    if args.json:
        _dump(row)
    else:
        print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def cmd_census_validate(args) -> int:
    doc = load_census(args.path, root=args.root)
    findings = doc.validate(check_source_locations=not args.no_source_locations)
    if args.static_declarations:
        findings.extend(doc.static_declaration_findings())
    return _print_findings(findings, json_mode=args.json)


def cmd_census_summary(args) -> int:
    doc = load_census(args.path, root=args.root)
    data = doc.summary()
    if args.json:
        _dump(data)
    else:
        print(f"{data['title']}\nfamily: {data['family']}\nitems: {data['items']}\nunique Lean declarations: {data['unique_lean_declarations']}")
        for field in ("status", "verification", "importance", "semantic_alignment"):
            if data[field]:
                print(f"\n{field.replace('_', ' ')}")
                for key, value in data[field].items():
                    print(f"  {key}: {value}")
    return 0


def _emit(text: str, out: str | None, check: bool = False) -> int:
    """Write generated text, or verify a checked-in copy is current.

    A generated file committed to Git and never verified goes stale silently,
    and then reads as a maintained document.  ``--check`` is what makes the copy
    a claim the suite can falsify.
    """
    if not out:
        sys.stdout.write(text)
        return 0
    target = Path(out)
    if check:
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if current == text:
            print(f"{out}: up to date")
            return 0
        print(f"{out}: " + ("stale" if current is not None else "missing"))
        print("  Regenerate it: rerun this command without --check.")
        return 1
    atomic_write_text(target, text)
    print(out)
    return 0


def cmd_census_render(args) -> int:
    doc = load_census(args.path, root=args.root)
    text = doc.render_markdown()
    return _emit(text, args.out, getattr(args, "check", False))


def cmd_serve(args) -> int:
    from .server import serve

    root = Path(args.root).expanduser().resolve() if args.root else Path.cwd()
    return serve(root, host=args.host, port=args.port, title=args.title,
                 private_sources=args.private_sources, include_private=args.include_private)


def cmd_census_html(args) -> int:
    doc = load_census(args.path, root=args.root)
    out = Path(args.out) if args.out else doc.path.with_suffix(".html")
    atomic_write_text(out, doc.render_html())
    print(out)
    return 0


def cmd_census_patch(args) -> int:
    doc = load_census(args.path, root=args.root)
    doc.patch_item(args.id, _sets(args.set), args.delete)
    findings = doc.validate(check_source_locations=not args.no_source_locations)
    errors = [f for f in findings if f.level == "error"]
    if errors and not args.force:
        return _print_findings(findings, json_mode=False)
    doc.write()
    print(doc.path)
    return 0


def cmd_census_add(args) -> int:
    doc = load_census(args.path, root=args.root)
    if args.from_json == "-":
        row = json.load(sys.stdin)
    else:
        row = load_json(Path(args.from_json))
    if not isinstance(row, dict):
        raise FormalizationToolsError("new census item must be a JSON object")
    doc.append_item(row)
    findings = doc.validate(check_source_locations=not args.no_source_locations)
    if any(f.level == "error" for f in findings) and not args.force:
        return _print_findings(findings)
    doc.write()
    print(doc.path)
    return 0


def cmd_census_probe(args) -> int:
    doc = load_census(args.path, root=args.root)
    probe = doc.probe(imports=args.imports, timeout=args.timeout)
    if args.write:
        changed = doc.apply_probe(probe)
        if changed:
            doc.write()
    else:
        changed = 0
    data = probe.to_json()
    data["verification_rows_changed"] = changed
    if args.json:
        _dump(data)
    else:
        print(f"resolved {len(probe.resolved)}/{len(probe.results)} cited declaration(s)")
        print("imports: " + ", ".join(probe.imports))
        if probe.unresolved:
            print("unresolved:")
            for name in probe.unresolved:
                module = probe.private_declarations.get(name)
                if module:
                    print(f"  {name}  [declared PRIVATE in {module}: proved but not citable, "
                          "so it is not evidence for a source item]")
                else:
                    print(f"  {name}  [no declaration of this name exists]")
        if args.write:
            print(f"updated verification on {changed} row(s)")
    return 1 if probe.unresolved else 0


def cmd_review_validate(args) -> int:
    return _print_findings(load_semantic_review(args.path, root=args.root).validate(), json_mode=args.json)


def cmd_review_summary(args) -> int:
    data = load_semantic_review(args.path, root=args.root).summary()
    if args.json:
        _dump(data)
    else:
        print(f"{data['title']}\nrows: {data['rows']}\nunique Lean declarations: {data['unique_lean_declarations']}")
        if data["relations"]:
            print("\nrelations")
            for key, value in data["relations"].items():
                print(f"  {key}: {value}")
    return 0


def cmd_review_render(args) -> int:
    doc = load_semantic_review(args.path, root=args.root)
    text = doc.render_markdown()
    return _emit(text, args.out, getattr(args, "check", False))


def cmd_review_html(args) -> int:
    doc = load_semantic_review(args.path, root=args.root)
    out = Path(args.out) if args.out else doc.path.with_suffix(".html")
    atomic_write_text(out, doc.render_html())
    print(out)
    return 0


def cmd_review_patch(args) -> int:
    doc = load_semantic_review(args.path, root=args.root)
    doc.patch_row(args.id, _sets(args.set), args.delete)
    findings = doc.validate()
    if any(f.level == "error" for f in findings) and not args.force:
        return _print_findings(findings)
    doc.write()
    print(doc.path)
    return 0


def cmd_review_add(args) -> int:
    doc = load_semantic_review(args.path, root=args.root)
    if args.from_json == "-":
        row = json.load(sys.stdin)
    else:
        row = load_json(Path(args.from_json))
    if not isinstance(row, dict):
        raise FormalizationToolsError("new semantic-review row must be a JSON object")
    doc.append_row(row)
    findings = doc.validate()
    if any(f.level == "error" for f in findings) and not args.force:
        return _print_findings(findings)
    doc.write()
    print(doc.path)
    return 0


def _workspace(args) -> FormalizationWorkspace:
    return FormalizationWorkspace.discover(args.root)


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value[:80] or "source"


def cmd_coverage_validate(args) -> int:
    bundle = load_coverage_bundle(args.path, root=args.root)
    return _print_findings(
        bundle.validate(static_declarations=args.static_declarations),
        json_mode=args.json,
    )


def cmd_coverage_summary(args) -> int:
    data = load_coverage_bundle(args.path, root=args.root).summary()
    if args.json:
        _dump(data)
    else:
        print(f"results: {data['results']}")
        print(f"completion obligations: {data['completion_obligations']}")
        print(f"unique Lean declarations: {data['unique_lean_declarations']}")
        if data.get("source_atoms"):
            print(f"source atoms: {data['source_atoms']['atoms']}")
        for field in ("dispositions", "verification", "semantic_certification", "semantic_alignment"):
            values = data.get(field) or {}
            if values:
                print(f"\n{field.replace('_', ' ')}")
                for key, value in values.items():
                    print(f"  {key}: {value}")
    return 0


def cmd_coverage_show(args) -> int:
    bundle = load_coverage_bundle(args.path, root=args.root)
    if args.atom:
        if bundle.atoms is None:
            raise FormalizationToolsError("result inventory does not link an available source-atom inventory")
        row = bundle.atoms.atom(args.atom)
    else:
        row = bundle.results.row(args.id)
    print(json.dumps(row, indent=2, ensure_ascii=False))
    return 0


def cmd_coverage_render(args) -> int:
    text = load_coverage_bundle(args.path, root=args.root).render_markdown()
    return _emit(text, args.out, getattr(args, "check", False))


def cmd_coverage_html(args) -> int:
    bundle = load_coverage_bundle(args.path, root=args.root)
    out = Path(args.out) if args.out else bundle.results.path.with_suffix(".html")
    atomic_write_text(out, bundle.render_html())
    print(out)
    return 0


def cmd_coverage_patch(args) -> int:
    bundle = load_coverage_bundle(args.path, root=args.root)
    if args.atom:
        if bundle.atoms is None:
            raise FormalizationToolsError("result inventory does not link an available source-atom inventory")
        bundle.atoms.patch_atom(args.atom, _sets(args.set), args.delete)
        findings = bundle.atoms.validate()
        if any(f.level == "error" for f in findings) and not args.force:
            return _print_findings(findings)
        bundle.atoms.write()
        print(bundle.atoms.path)
    else:
        bundle.results.patch_item(args.id, _sets(args.set), args.delete)
        findings = bundle.validate()
        if any(f.level == "error" for f in findings) and not args.force:
            return _print_findings(findings)
        bundle.results.write()
        print(bundle.results.path)
    return 0


def cmd_workspace_init(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    dev = root / "dev"
    dev.mkdir(parents=True, exist_ok=True)
    sources = list(args.source or [])
    files: list[tuple[Path, str]] = []
    manifest = formalization_manifest_template(args.name, sources=sources)
    files.append((root / "formalization.yaml", yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)))
    files.append((dev / "grounding-policy.yaml", yaml.safe_dump(grounding_policy_template(), sort_keys=False, allow_unicode=True)))
    from .common import atomic_write_json
    json_files: list[tuple[Path, Any]] = []
    for source_title in sources:
        slug = _slug(source_title)
        census_name = f"{slug}-source-census.json"
        review_name = f"{slug}-semantic-review.json"
        json_files.append((dev / census_name, census_template(source_title)))
        json_files.append((dev / review_name, semantic_review_template(source_title, companion_census=census_name)))

    targets = [path for path, _ in files] + [path for path, _ in json_files]
    existing = [path for path in targets if path.exists()]
    if existing and not args.force:
        shown = "\n  ".join(str(path) for path in existing)
        raise FormalizationToolsError(f"refusing to overwrite existing workspace files:\n  {shown}")
    for path, payload in files:
        atomic_write_text(path, payload)
    for path, payload in json_files:
        atomic_write_json(path, payload)
    for path in targets:
        print(path)
    return 0


def cmd_workspace_status(args) -> int:
    data = _workspace(args).overview(include_source_audit=args.source_audit)
    if args.json:
        _dump(data)
    else:
        print(data.get("manifest", {}).get("project_name") if isinstance(data.get("manifest"), dict) else data["root"])
        print(f"censuses: {data['census_count']}  result rows: {data['result_rows']}  semantic reviews: {data['semantic_review_count']}")
        print(f"unique cited declarations: {data['unique_cited_declarations']}  cross-document reused: {len(data['cross_document_reuse'])}")
        for field in ("status", "verification", "importance", "semantic_alignment"):
            if data[field]:
                print(f"\n{field.replace('_', ' ')}")
                for key, value in data[field].items():
                    print(f"  {key}: {value}")
        if data.get("source_audit"):
            print("\nsource audit")
            for key, value in data["source_audit"].items():
                if key != "root":
                    print(f"  {key}: {value}")
        if data.get("leanq_graph"):
            print("\nleanq graph")
            for key, value in data["leanq_graph"].items():
                print(f"  {key}: {value}")
    return 0


def cmd_workspace_validate(args) -> int:
    findings = _workspace(args).validate(source_locations=not args.no_source_locations, static_declarations=args.static_declarations)
    return _print_findings(findings, json_mode=args.json)


def cmd_workspace_html(args) -> int:
    ws = _workspace(args)
    out = Path(args.out) if args.out else ws.root / "build" / "formalization-tools" / "workspace.html"
    atomic_write_text(out, ws.render_html(include_source_audit=args.source_audit))
    print(out)
    return 0


def cmd_source_scan(args) -> int:
    index = scan_lean_project(args.root)
    data = source_audit_summary(index)
    if args.json:
        data["declarations_data"] = [row.to_json(index.root) for row in index.declarations]
        _dump(data)
    else:
        for key, value in data.items():
            print(f"{key}: {value}")
    return 0


def _listed(partition, all_keys, *, baseline_used: bool) -> list:
    """What to print: only the unaccepted findings once a baseline is in use.

    Reprinting hundreds of accepted findings on every green run is how a gate's
    output stops being read.
    """
    if baseline_used:
        return list(partition.new)
    return list(all_keys)


def _report_baseline(partition, *, label: str, describe) -> int:
    """Print a baselined structural check and return its exit status."""
    for key in partition.new:
        print(f"NEW      {describe(key)}")
    for key in partition.stale:
        print(f"STALE    baseline entry no longer occurs: {key}")
    print(
        f"{label}: {len(partition.accepted) + len(partition.new)} finding(s), "
        f"{len(partition.accepted)} accepted by baseline, "
        f"{len(partition.new)} new, {len(partition.stale)} stale"
    )
    if partition.stale:
        print("  Remove the stale entries: a baseline naming a finding that no longer")
        print("  occurs silently pre-accepts whatever next takes that name.")
    return 0 if partition.ok else 1


def cmd_source_duplicates(args) -> int:
    index = scan_lean_project(args.root).restricted(include=args.prefix, exclude=args.exclude_prefix)
    found = index.duplicate_public_names()
    baseline = Baseline.load(args.baseline)
    partition = baseline.partition(found)
    if args.write_baseline:
        target = baseline.write(args.write_baseline, sorted(found))
        print(f"baseline written: {len(found)} accepted -> {target}")
        return 0
    if args.json:
        _dump({
            "findings": {name: [row.to_json(index.root) for row in rows] for name, rows in found.items()},
            "baseline": partition.to_json(),
        })
        return 0 if not args.check or partition.ok else 1
    for name in _listed(partition, sorted(found), baseline_used=bool(args.baseline)):
        state = "NEW" if name in partition.new else "accepted"
        print(f"DUPLICATE {name} [{state}]")
        for row in found[name]:
            print(f"  {row.path.relative_to(index.root)}:{row.line} ({row.module})")
    status = _report_baseline(
        partition,
        label="duplicate public qualified names",
        describe=lambda key: f"{key} is declared in {len(found[key])} modules",
    )
    if partition.new:
        print("  A name declared in two modules compiles only while nothing imports both.")
    return status if args.check else 0


def cmd_source_admissions(args) -> int:
    index = scan_lean_project(args.root)
    rows = admission_report(index)
    tainted = [row for row in rows if row["admission_closure"]]
    if args.json:
        _dump(rows)
    else:
        print(f"modules: {len(rows)}  own admissions: {len(index.admitted_modules)}  admission closure: {len(tainted)}")
        for row in tainted:
            print(f"{row['module']}: {', '.join(row['admission_closure'])}")
    return 0


def _docstring_key(root, row) -> str:
    return f"{row.path.relative_to(root).as_posix()}:{row.name}"


def cmd_source_docstrings(args) -> int:
    index = scan_lean_project(args.root)
    rows = undocumented_public(index, roots=args.prefix, exclude=args.exclude_prefix)
    keys = {_docstring_key(index.root, row): row for row in rows}
    baseline = Baseline.load(args.baseline)
    partition = baseline.partition(keys)
    if args.write_baseline:
        target = baseline.write(args.write_baseline, sorted(keys))
        print(f"baseline written: {len(keys)} tolerated -> {target}")
        return 0
    if args.json:
        _dump({
            "findings": [row.to_json(index.root) for row in rows],
            "baseline": partition.to_json(),
        })
        return 0 if not args.check or partition.ok else 1
    for key in _listed(partition, sorted(keys), baseline_used=bool(args.baseline)):
        row = keys[key]
        print(f"{row.path.relative_to(index.root)}:{row.line} {row.kind} {row.name}")
    status = _report_baseline(
        partition,
        label="undocumented public declarations",
        describe=lambda key: key,
    )
    return status if args.check else 0


def cmd_source_proof_length(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    rows = proof_length_census(root, roots=args.library, minimum=args.min, scaffold_definition=args.scaffold_definition)
    if args.json:
        _dump([row.to_json(root) for row in rows])
    else:
        print(f"{len(rows)} proof(s) with at least {args.min} body lines")
        for row in rows:
            rel = row.path.relative_to(root)
            extra = ""
            if args.extractable and row.extraction_costs:
                import statistics
                extra = f"  extraction median={statistics.median(row.extraction_costs):.1f} max={max(row.extraction_costs)}"
            print(f"{row.body_lines:4d} lines  {row.scaffold_percent:3d}% scaffold  {rel}:{row.name}{extra}")
    return 0


def cmd_source_snapshot(args) -> int:
    index = scan_lean_project(args.root)
    out = Path(args.out)
    write_declaration_snapshot(index, out)
    print(out)
    return 0


def cmd_source_drift(args) -> int:
    index = scan_lean_project(args.root)
    baseline = load_json(Path(args.baseline))
    data = declaration_drift(index, baseline)
    if args.json:
        _dump(data)
    else:
        for field in ("removed", "added"):
            print(f"{field}: {len(data[field])}")
            for name in data[field][:100]:
                print(f"  {name}")
        print(f"moved: {len(data['moved'])}")
        for row in data["moved"][:100]:
            print(f"  {row['name']}: {row['from']} -> {row['to']}")
        if data["rename_hints"]:
            print("rename hints:")
            for row in data["rename_hints"][:100]:
                print(f"  {row['from']} -> {row['to']}")
    return 1 if args.check and (data["removed"] or data["moved"]) else 0

def cmd_source_aggregates(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    header = Path(args.header_file).read_text(encoding="utf-8") if args.header_file else ""
    rows = generate_aggregates(
        root,
        args.base,
        own_library=args.library,
        aggregate_name=args.aggregate_name,
        skip_dirs=args.skip_dir,
        preserve_foreign_reexports=not args.no_preserve_foreign,
        root_import=args.root_import,
        header=header,
        check=args.check,
        respect_cycles=not args.ignore_import_cycles,
    )
    stale = [row for row in rows if row.changed]
    dangling = [(row, name) for row in rows for name in row.dangling_reexports]
    unplaceable = [name for row in rows for name in row.unplaceable_modules]
    if args.json:
        _dump({
            "aggregates": [row.to_json(root) for row in rows],
            "changed": len(stale),
            "dangling_reexports": [
                {"aggregate": row.module, "module": name} for row, name in dangling
            ],
            "unplaceable_modules": unplaceable,
        })
    else:
        verb = "stale" if args.check else "regenerated"
        for row in stale:
            print(f"{verb}: {row.module}")
        for row, name in dangling:
            print(f"dangling re-export: {row.module} -> {name}")
        for name in unplaceable:
            print(f"unplaceable: {name} depends on every aggregate that could hold it")
        print(f"aggregates: {len(rows)} checked, {len(stale)} {verb}")
    return 1 if (unplaceable or (args.check and (stale or dangling))) else 0


def cmd_source_conflicts(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    rows = conflict_markers(root, tracked_only=not args.all_files)
    if args.json:
        _dump([row.to_json(root) for row in rows])
    else:
        for row in rows:
            print(f"{row.path.relative_to(root)}:{row.line}: {row.text}")
        print(f"{len(rows)} merge-conflict marker(s)")
    return 1 if args.check and rows else 0


def cmd_source_orphan_artifacts(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    modules = orphan_build_modules(root, libraries=args.library)
    removed = []
    if args.fix and modules:
        removed = remove_orphan_build_modules(root, modules)
    if args.json:
        _dump({
            "modules": [m.as_posix() for m in modules],
            "removed_files": [p.relative_to(root).as_posix() for p in removed],
        })
    else:
        for module in modules:
            print("orphan: " + ".".join(module.parts))
        print(f"{len(modules)} orphan built module(s)")
        if args.fix:
            print(f"removed {len(removed)} artifact file(s)")
    return 1 if args.check and modules and not args.fix else 0


def cmd_source_declaration(args) -> int:
    index = scan_lean_project(args.root)
    payload: dict[str, list[dict[str, Any]]] = {}
    missing: list[str] = []
    for name in args.names:
        rows = declaration_source_texts(index, name)
        payload[name] = [row.to_json(index.root) for row in rows]
        if not rows:
            missing.append(name)
    if args.json:
        _dump({"declarations": payload, "missing": missing})
    else:
        for name in args.names:
            rows = declaration_source_texts(index, name)
            print(f"## {name}")
            if not rows:
                print("not found")
                continue
            for row in rows:
                rel = row.declaration.path.relative_to(index.root)
                print(f"{rel}:{row.declaration.line}")
                print(row.render())
                print()
    return 1 if args.check and missing else 0


def cmd_source_private_shadows(args) -> int:
    index = scan_lean_project(args.root)
    rows = [
        row for row in index.private_shadows_imported_public()
        if module_in_scope(row["module"], args.prefix, args.exclude_prefix)
    ]
    keys = {f"{row['module']}:{row['name']}": row for row in rows}
    baseline = Baseline.load(args.baseline)
    partition = baseline.partition(keys)
    if args.write_baseline:
        target = baseline.write(args.write_baseline, sorted(keys))
        print(f"baseline written: {len(keys)} accepted -> {target}")
        return 0
    if args.json:
        _dump({
            "findings": [{
                "module": row["module"],
                "name": row["name"],
                "declaration": row["declaration"].to_json(index.root),
                "providers": row["providers"],
            } for row in rows],
            "baseline": partition.to_json(),
        })
        return 0 if not args.check or partition.ok else 1
    for key in _listed(partition, sorted(keys), baseline_used=bool(args.baseline)):
        row = keys[key]
        decl = row["declaration"]
        print(f"{decl.path.relative_to(index.root)}:{decl.line}: private `{row['name']}` is public in {', '.join(row['providers'])}")
    status = _report_baseline(
        partition,
        label="private declarations shadowing imported public names",
        describe=lambda key: key,
    )
    if partition.new:
        print("  If the copy exists because the imported declaration does not apply --")
        print("  a universe, an instance, a variable order -- fix that rather than")
        print("  restating it.  A one-line presentation wrapper belongs in the baseline.")
    return status if args.check else 0



def cmd_source_similar(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    rows = duplicate_candidates(
        root,
        roots=args.library,
        definitions=args.definitions,
        minimum_chars=args.min_chars,
        include_forwarders=args.include_forwarders,
        tracked_only=not args.all_files,
    )
    if args.json:
        _dump([row.to_json(root) for row in rows[: args.top if args.top else None]])
    else:
        what = "definition bodies" if args.definitions else "theorem statements"
        print(f"{len(rows)} normalized {what} candidate group(s) spanning multiple files")
        for row in rows[: args.top if args.top else None]:
            tag = " [forwarder]" if row.forwarding_alias else ""
            print(f"\n--- {len(row.declarations)} declarations{tag}")
            print("    " + row.normalized[:180])
            for decl in row.declarations:
                print(f"  {decl.path.relative_to(root)}:{decl.line}  {decl.name}")
    return 1 if args.check and rows else 0


def cmd_source_large(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    rows = largest_declarations(root, roots=args.library, tracked_only=not args.all_files)
    by_statement = sorted(rows, key=lambda row: (-(row.statement.count("\n") + 1), -row.body_line_count, row.path.as_posix()))[:args.top]
    by_body = sorted(rows, key=lambda row: (-row.body_line_count, -(row.statement.count("\n") + 1), row.path.as_posix()))[:args.top]
    if args.json:
        _dump({
            "longest_statements": [row.to_json(root) for row in by_statement],
            "longest_bodies": [row.to_json(root) for row in by_body],
        })
    else:
        print("longest statements")
        for row in by_statement:
            print(f"  stmt {row.statement.count(chr(10)) + 1:4d}  body {row.body_line_count:5d}  {row.path.relative_to(root)}:{row.line}  {row.name}")
        print("\nlongest bodies")
        for row in by_body:
            print(f"  body {row.body_line_count:5d}  stmt {row.statement.count(chr(10)) + 1:4d}  {row.path.relative_to(root)}:{row.line}  {row.name}")
    return 0


def cmd_source_dead(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    rows = dead_definition_candidates(root, roots=args.library, tracked_only=not args.all_files)
    shown = rows[: args.top if args.top else None]
    if args.json:
        _dump([row.to_json(root) for row in shown])
    else:
        for row in shown:
            decl = row.declaration
            print(f"{decl.path.relative_to(root)}:{decl.line} {decl.name}  occurrences={row.occurrences} definitions={row.definition_count}")
        print(f"{len(rows)} unused-definition candidate(s)")
    return 1 if args.check and rows else 0


def cmd_source_names(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    rows = name_candidates(root, roots=args.library, pattern=args.pattern, tracked_only=not args.all_files)
    shown = rows[: args.top if args.top else None]
    if args.json:
        _dump([row.to_json(root) for row in shown])
    else:
        for row in shown:
            decl = row.declaration
            print(f"{decl.path.relative_to(root)}:{decl.line} {decl.name}  matched={row.matched!r}")
        print(f"{len(rows)} naming-review candidate(s)")
    return 1 if args.check and rows else 0


def cmd_source_definitional_escapes(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    rows = definitional_escape_candidates(root, roots=args.library, tracked_only=not args.all_files)
    if args.json:
        _dump([row.to_json(root) for row in rows])
    else:
        for row in rows:
            decl = row.declaration
            print(f"{decl.path.relative_to(root)}:{decl.line} {decl.kind} {decl.name}")
        print(f"{len(rows)} definition body escape(s)")
    return 1 if args.check and rows else 0



def cmd_source_profile(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    rows = review_profiles(
        root,
        roots=args.library,
        minimum_duplicate_chars=args.min_chars,
        tracked_only=not args.all_files,
    )
    if args.json:
        _dump([row.to_json(root) for row in rows])
    else:
        print(f"{'lines':>7} {'decl':>5} {'stmt':>5} {'body':>5} {'dup':>4} {'dead':>4} {'name':>4} {'defn':>4}  file")
        for row in rows:
            ndeclarations = sum(row.declaration_counts.values())
            print(
                f"{row.lines:7d} {ndeclarations:5d} {row.max_statement_lines:5d} {row.max_body_lines:5d} "
                f"{len(row.duplicate_candidates):4d} {len(row.dead_candidates):4d} "
                f"{len(row.name_candidates):4d} {len(row.definitional_escapes):4d}  {row.path.relative_to(root)}"
            )
            if args.details:
                for label, names in (
                    ("DUP", row.duplicate_candidates),
                    ("DEAD", row.dead_candidates),
                    ("NAME", row.name_candidates),
                    ("DEFN", row.definitional_escapes),
                ):
                    if names:
                        print(f"         {label}: {', '.join(names)}")
    return 0

def cmd_source_symbol_census(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    kwargs = {"pattern": args.expression} if args.regex else {"symbol": args.expression}
    report = symbol_census(
        root,
        include=args.include or ("**/*.lean",),
        exclude=args.exclude,
        remove_imports=not args.keep_imports,
        remove_commands=not args.keep_commands,
        **kwargs,
    )
    if args.json:
        _dump(report.to_json(root))
    else:
        print(f"raw mentions: {report.raw_total} across {sum(row.raw > 0 for row in report.files)} file(s)")
        print(f"code mentions: {report.code_total} across {sum(row.code > 0 for row in report.files)} file(s)")
        print(f"comment/import/command overcount: {report.noise_total}")
        if args.verbose:
            for row in report.files:
                print(f"  code {row.code:4d} raw {row.raw:4d}  {row.path.relative_to(root)}")
    return 0


def cmd_source_checklist(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    result = update_checklists(
        root,
        file_path=args.file,
        group_path=args.groups,
        include=args.include or ("*",),
        exclude=args.exclude or (".git/**", ".lake/**", "build/**", "vendor/**", "external/**"),
        group_depth=args.group_depth,
        split_nonlean_by_kind=not args.no_kind_split,
        write=not args.progress,
    )
    data = result.to_json()
    if args.json:
        _dump(data)
    else:
        print(f"files: {result.files_done}/{result.files_total} reviewed ({result.lines_done}/{result.lines_total} lines)")
        print(f"groups: {result.groups_done}/{result.groups_total} reviewed")
        print(f"groups ready for holistic review: {len(result.ready_groups)}")
        for group in result.ready_groups:
            print(f"  {group}")
        if not args.progress:
            print(f"wrote {args.file}")
            print(f"wrote {args.groups}")
    return 0



def cmd_source_module_coverage(args) -> int:
    policy = ModuleCoveragePolicy.load(args.policy)
    report = check_module_coverage(policy, root=args.root)
    errors = [finding for finding in report.findings if finding.level == "error"]
    if args.json:
        _dump(report.to_json())
    else:
        print(
            f"module coverage: {len(report.scope_modules)} scoped, "
            f"{len(report.covered)} reached, {len(report.uncovered)} uncovered, "
            f"{len(report.explicitly_excluded)} named exclusions, "
            f"{len(report.prefix_excluded)} subtree exclusions, "
            f"{len(report.inherited_excluded)} inherited exclusions"
        )
        for finding in report.findings:
            prefix = f"{finding.location}: " if finding.location else ""
            print(f"{finding.level.upper():7s} {prefix}[{finding.code}] {finding.message}")
        print("Source import reachability does not certify Lean elaboration or successful compilation.")
    return 1 if errors else 0



def cmd_source_roadmap(args) -> int:
    report = compare_roadmap(
        args.roadmap_root,
        args.root or ".",
        suggested_glob=args.suggested_glob,
        libraries=args.library,
        preferred_prefixes=args.prefer,
    )
    topics = report.topics
    if args.topic:
        topics = [topic for topic in topics if topic.topic == args.topic or Path(topic.topic).name == args.topic]
        if not topics:
            raise FormalizationToolsError(f"no roadmap topic matches {args.topic!r}")
    if args.json:
        data = report.to_json()
        data["topics"] = [topic.to_json() for topic in topics]
        data["total"] = sum(topic.total for topic in topics)
        data["delivered"] = sum(topic.delivered for topic in topics)
        _dump(data)
        return 0
    total = sum(topic.total for topic in topics)
    delivered = sum(topic.delivered for topic in topics)
    for topic in topics:
        pct = 100.0 * topic.delivered / topic.total if topic.total else 0.0
        print(f"{topic.topic}: {topic.delivered}/{topic.total} ({pct:.1f}%)")
        if args.missing:
            for name in topic.missing:
                print(f"  outstanding: {name}")
        if args.map:
            for name, path in sorted(topic.mapping.items()):
                suffix = " [AMBIGUOUS]" if name in topic.ambiguous else ""
                print(f"  {name} -> {path}{suffix}")
        if args.ambiguous:
            for name, paths in sorted(topic.ambiguous.items()):
                print(f"  ambiguous: {name}")
                for path in paths:
                    print(f"    {path}")
    pct = 100.0 * delivered / total if total else 0.0
    print(f"roadmap delivery: {delivered}/{total} ({pct:.1f}%)")
    print("Declaration-name matches do not establish statement equivalence or roadmap completion.")
    return 0


def cmd_source_grounding(args) -> int:
    policy = load_grounding_policy(args.policy)
    findings = check_grounding_policy(policy, root=args.root)
    return _print_findings(findings, json_mode=args.json)


def cmd_source_import_closure(args) -> int:
    graph = SourceImportGraph.discover(args.root)
    targets = _pairs(args.target, option="--target")
    data = graph.compare_closures(targets)
    if args.html:
        atomic_write_text(Path(args.html), graph.render_closure_html(targets))
        print(args.html)
    elif args.json:
        _dump(data)
    else:
        for row in data["targets"]:
            families = ", ".join(f"{key}={value}" for key, value in row["families"].items())
            print(f"{row['label']}: {row['local_module_count']} local modules [{families}]")
            if args.unresolved and row["unresolved_imports"]:
                for missing in row["unresolved_imports"]:
                    print(f"  unresolved {missing['source']} -> {missing['module']}")
        if data["overlaps"]:
            print("\noverlaps")
            for row in data["overlaps"]:
                print(f"  {row['left']} / {row['right']}: {row['count']}")
    return 0


def cmd_source_provenance(args) -> int:
    markers = dict(_pairs(args.marker, option="--marker")) if args.marker else {}
    data = provenance_inventory(
        args.root,
        include=args.include or ("**/*.lean", "*.lean"),
        heading=args.heading,
        markers=markers,
    )
    if args.json:
        _dump(data)
    else:
        print(f"{data['block_count']} `{args.heading}` block(s) across {data['files_with_blocks']}/{data['scanned_files']} scanned file(s)")
        for name, count in data["marker_counts"].items():
            print(f"  {name}: {count}")
        if args.details:
            for row in data["blocks"]:
                print(f"\n{row['file']}:{row['line']}\n  {row['text']}")
    return 1 if args.require and data["files_with_blocks"] < data["scanned_files"] else 0


def cmd_source_module_migrate(args) -> int:
    rows = [migrate_module_file(path, write=args.write) for path in args.paths]
    changed = [row for row in rows if row.changed]
    if args.json:
        _dump([{
            "path": str(row.path),
            "changed": row.changed,
            "already_module": row.already_module,
        } for row in rows])
    else:
        for row in rows:
            status = "converted" if args.write and row.changed else "needs-conversion" if row.changed else "already-module"
            print(f"{status}: {row.path}")
        print(f"{len(changed)} file(s) need conversion")
    return 1 if args.check and changed and not args.write else 0


def cmd_history_commits(args) -> int:
    commits = load_git_history(args.root, rev=args.rev, pathspecs=args.path)
    if args.json:
        _dump([commit.to_json() for commit in commits])
    else:
        for commit in commits:
            coauthors = ", ".join(name for name, _ in commit.coauthors)
            suffix = f" [{coauthors}]" if coauthors else ""
            print(f"{commit.commit[:12]} {commit.timestamp} {commit.subject}{suffix}")
    return 0


def cmd_history_summary(args) -> int:
    commits = load_git_history(args.root, rev=args.rev, pathspecs=args.path)
    data = history_summary(commits)
    if args.json:
        _dump(data)
    else:
        for key in ("commit_count", "first_timestamp", "last_timestamp", "additions", "deletions", "lean_additions", "lean_deletions", "unique_files_touched"):
            print(f"{key}: {data[key]}")
        if data["authors"]:
            print("authors:")
            for name, count in data["authors"].items():
                print(f"  {name}: {count}")
        if data["coauthors"]:
            print("coauthors:")
            for name, count in data["coauthors"].items():
                print(f"  {name}: {count}")
    return 0


def cmd_history_html(args) -> int:
    commits = load_git_history(args.root, rev=args.rev, pathspecs=args.path)
    out = Path(args.out)
    atomic_write_text(out, render_history_html(commits, title=args.title))
    print(out)
    return 0

def cmd_gates_list(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    config = GateSuiteConfig.load(args.config)
    rows = [gate for gate in discover_gates(root, config) if args.filter in gate.name]
    if args.json:
        _dump([gate.to_json(root) for gate in rows])
    else:
        for gate in rows:
            slow = " [slow]" if gate.slow else ""
            print(f"{gate.name:<40} {gate.kind}{slow}")
        print(f"{len(rows)} gate(s)")
    return 0


def cmd_gates_run(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    config = GateSuiteConfig.load(args.config)
    results, skipped = run_gate_suite(
        root,
        config=config,
        filter_text=args.filter,
        fast=args.fast,
        timeout=args.timeout,
        python=args.python or sys.executable,
    )
    failed = [row for row in results if row.status == "failed"]
    unavailable = [row for row in results if row.status == "unavailable"]
    advisories = [row for row in results if row.status == "advisory"]
    if args.json:
        _dump({
            "ok": not failed,
            "results": [row.to_json(root, tail_lines=args.tail) for row in results],
            "skipped": [gate.to_json(root) for gate in skipped],
        })
    else:
        for row in results:
            print(f"{row.gate.name:<40} {row.status.upper():11s} {row.elapsed:7.2f}s")
        if skipped:
            print("\nskipped slow gates: " + ", ".join(gate.name for gate in skipped))
        for row in [*unavailable, *advisories, *failed]:
            print(f"\n===== {row.gate.name} [{row.status}]")
            for line in row.output.strip().splitlines()[-args.tail:]:
                print("  " + line)
        print(f"\ngates: {len(results) - len(failed) - len(unavailable)} passed/advisory, {len(failed)} failed, {len(unavailable)} unavailable, {len(skipped)} skipped")
    return 1 if failed else 0


def cmd_ratchet_check(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    rules = load_ratchet_policy(args.policy)
    rows = evaluate_ratchets(root, rules)
    failed = [row for row in rows if not row.ok]
    if args.json:
        _dump({"ok": not failed, "rules": [row.to_json() for row in rows]})
    else:
        for row in rows:
            print(f"{row.rule.id}: {row.value} {row.rule.count}, maximum {row.rule.maximum} [{row.status}]")
            if args.list_files and row.matching_files:
                for rel in row.matching_files:
                    print(f"  {rel}")
        print(f"{len(failed)} ratchet violation(s)")
    return 1 if failed else 0

def cmd_namespace_check(args) -> int:
    root = Path(args.root or ".").expanduser().resolve()
    policy = load_namespace_policy(args.policy)
    rows = check_namespace_policy(root, policy)
    if args.json:
        _dump([row.to_json(root) for row in rows])
    else:
        for row in rows:
            print(f"[{row.rule}] {row.path.relative_to(root)}: {row.namespace}: {row.detail}")
        print(f"{len(rows)} namespace-policy violation(s)")
    return 1 if rows else 0


def cmd_imports_check(args) -> int:
    index = scan_lean_project(args.root)
    policy = ImportPolicy.load(args.policy)
    rows = check_import_policy(index, policy)
    if args.json:
        _dump([row.to_json() for row in rows])
    else:
        for row in rows:
            print(f"[{row.rule}] {row.module}: {row.detail}")
            if len(row.path) > 1:
                print("  path: " + " -> ".join(row.path))
        print(f"{len(rows)} import-policy violation(s)")
    return 1 if rows else 0




def _source_library(args):
    """The source documents an alignment command reads, unless asked not to."""
    from .source_model import SourceLibrary, load_private_config

    if getattr(args, "no_sources", False):
        return None
    private = load_private_config(getattr(args, "private_sources", None))
    return SourceLibrary.discover(getattr(args, "root", None), private=private)


def cmd_alignment_render(args) -> int:
    packet = build_alignment_packet(
        args.census,
        root=args.root,
        importance=args.importance,
        probe=args.probe,
        imports=args.imports,
        timeout=args.timeout,
        statements=args.statements,
        sidecar=args.sidecar,
        library=args.lib,
        refresh=args.refresh,
        sources=_source_library(args),
        include_private=getattr(args, "include_private", False),
        rows=getattr(args, "row", None) or (),
    )
    text = packet.render_markdown()
    status = _emit(text, args.out, getattr(args, "check", False))
    unresolved = [row for row in packet.probes.values() if not row.resolved]
    return status or (1 if unresolved else 0)


def cmd_alignment_html(args) -> int:
    from .alignment import alignment_payload, load_graph_table, render_alignment_html

    packet = build_alignment_packet(
        args.census,
        root=args.root,
        importance=args.importance,
        statements=args.statements,
        sidecar=args.sidecar,
        library=args.lib,
        refresh=args.refresh,
        sources=_source_library(args),
        include_private=getattr(args, "include_private", False),
        rows=getattr(args, "row", None) or (),
    )
    graph = load_graph_table(args.graph) if args.graph else None
    payload = alignment_payload(packet, graph=graph, title=args.title)
    if args.json:
        text = json.dumps(payload, indent=2) + "\n"
    else:
        text = render_alignment_html(payload)
    return _emit(text, args.out, getattr(args, "check", False))


def _load_pin_document(path: str, root: str | None):
    """A census (items) or a standalone review (rows), told apart by shape."""
    from .statement_pins import census_pin_targets, review_pin_targets
    from .source_pins import census_source_targets, review_source_targets

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("rows"), list) and "items" not in raw:
        from .semantic_review import load_semantic_review

        doc = load_semantic_review(path, root=root)
        return doc, review_pin_targets, review_source_targets
    doc = load_census(path, root=root)
    return doc, census_pin_targets, census_source_targets


def _statement_targets_and_records(args, doc, target_fn):
    from .correspondence import cited_declarations
    from .statement_pins import statement_records

    targets = target_fn(doc, row_ids=args.id or ())
    if not targets:
        raise SystemExit("no reviewed rows with declarations to pin in " + args.document)
    seeds = [name for target in targets for name in target.declarations]
    for target in targets:
        # Clause targets and correspondence declarations need fresh statement
        # records for localized drift checks even when they are not themselves
        # part of the row's pin set.
        seeds.extend(cited_declarations(target.container))
        seeds.extend(
            str(p.get("declaration")) for p in target.pins if isinstance(p, Mapping)
        )
    records, meta = statement_records(
        doc.root, unique_in_order(seeds), sidecar=args.sidecar, library=args.lib,
        refresh=getattr(args, "refresh", False),
    )
    return targets, records, meta


def cmd_alignment_pin(args) -> int:
    """Record what the review accepted: the elaborated types, and the passages.

    Both halves are written in one pass so a row cannot end up pinned on one
    side and unpinned on the other, which is exactly the state that makes a
    later drift report ambiguous.
    """
    from .source_pins import pin_source_targets
    from .statement_pins import pin_targets

    doc, target_fn, source_fn = _load_pin_document(args.document, args.root)
    findings: list = []
    statements = sources = 0
    if not args.source_only:
        targets, records, meta = _statement_targets_and_records(args, doc, target_fn)
        statements, found = pin_targets(
            targets, records, toolchain=str(meta.get("toolchain", "")), note=args.note or "",
        )
        findings += found
    if not args.statements_only:
        library = _source_library(args)
        if library is None:
            print("source pins skipped: --no-sources")
        else:
            src_targets = source_fn(doc, row_ids=args.id or ())
            sources, found = pin_source_targets(src_targets, library, note=args.note or "")
            findings += found
    for finding in findings:
        print(finding)
    doc.write()
    print(f"pinned {statements} statement(s) and {sources} source passage(s) in {doc.path}")
    return 1 if any(f.level == "error" for f in findings) else 0


def cmd_alignment_check(args) -> int:
    """Has either side of an accepted review moved since it was accepted?"""
    from .correspondence import validate_correspondence, validate_lean_targets
    from .source_pins import check_source_targets, resolve_target
    from .statement_pins import check_pins

    doc, target_fn, source_fn = _load_pin_document(args.document, args.root)
    findings: list = []
    pinned = rows = 0
    if not args.source_only:
        targets, records, _ = _statement_targets_and_records(args, doc, target_fn)
        findings += check_pins(targets, records)
        for target in targets:
            findings += validate_lean_targets(
                target.container, statements=records, location=target.location,
            )
        pinned = sum(len(t.pins) for t in targets)
        rows = len(targets)
    if not args.statements_only:
        library = _source_library(args)
        if library is None:
            print("source checks skipped: --no-sources")
        else:
            src_targets = source_fn(doc, row_ids=args.id or ())
            findings += check_source_targets(src_targets, library)
            # With the documents in hand, a quoted excerpt can be checked
            # against the passage it claims to come from.  Schema validation
            # cannot do this: it does not read the source documents.
            for target in src_targets:
                resolved = resolve_target(target, library)
                findings += validate_correspondence(
                    target.container,
                    location=target.location,
                    fragment_text={k: v.plain for k, v in resolved.items()},
                    relations=doc.data.get("relation_definitions"),
                    check_status=False,
                )
    for finding in findings:
        print(finding)
    errors = [f for f in findings if f.level == "error"]
    warnings = [f for f in findings if f.level == "warning"]
    print(
        f"{pinned} statement pin(s) on {rows} row(s): "
        f"{len(errors)} drifted or gone, {len(warnings)} warning(s)"
    )
    return 1 if errors else 0


def cmd_alignment_adopt_source(args) -> int:
    """Migrate a legacy ``source_locator`` into an explicit primary fragment.

    Existing reviews carry one locator and no fragment list, and go on working:
    the browser reads that locator as an implicit primary fragment.  This turns
    it into a declared one, which is what makes it pinnable and what lets a row
    add the inherited passages beside it.  Hundreds of repetitive edits are not
    a reviewer's job.
    """
    doc, _, source_fn = _load_pin_document(args.document, args.root)
    rows = doc.items if hasattr(doc, "items") else doc.rows
    wanted = set(args.id or ())
    changed = []
    for row in rows:
        rid = str(row.get("id", ""))
        if wanted and rid not in wanted:
            continue
        container = row.get("semantic_review") if isinstance(row.get("semantic_review"), dict) else row
        if container.get("source_fragments"):
            continue
        locator = container.get("source_locator") or row.get("source_locator")
        if not locator:
            continue
        container["source_fragments"] = [
            {"id": "source", "role": "primary", "locator": locator}
        ]
        changed.append(rid)
    if args.dry_run:
        print(f"would adopt a primary source fragment on {len(changed)} row(s): " + ", ".join(changed))
        return 0
    if changed:
        doc.write()
    print(f"adopted a primary source fragment on {len(changed)} row(s) in {doc.path}")
    return 0


def cmd_manifest_validate(args) -> int:
    return _print_findings(load_manifest(args.path).validate(), json_mode=args.json)


def cmd_manifest_summary(args) -> int:
    data = load_manifest(args.path).summary()
    if args.json:
        _dump(data)
    else:
        for key, value in data.items():
            print(f"{key}: {value}")
    return 0



def cmd_signatures_check(args) -> int:
    policy = SignaturePolicy.load(args.policy)
    report = compare_signatures(
        policy,
        root=args.root,
        timeout=args.timeout,
        build=not args.no_build,
    )
    if args.json:
        _dump(report.to_json())
    else:
        for row in report.comparisons:
            print(f"{row.status:<5} {row.pair}: {row.declaration}")
            for finding in row.findings:
                print(f"  {finding.code}: {finding.message}")
        for finding in report.findings:
            if not any(finding in row.findings for row in report.comparisons):
                print(f"{finding.level.upper():7s} [{finding.code}] {finding.message}")
        print(f"{len(report.comparisons)} comparison(s); {'PASS' if report.ok else 'FAIL'}")
    return 0 if report.ok else 1


def _foundation_report(args):
    fmap = FoundationMap.load(args.path)
    return check_foundation_map(
        fmap,
        root=args.root,
        lean_probe=getattr(args, "lean_probe", False),
        timeout=getattr(args, "timeout", 600),
    )


def cmd_foundations_validate(args) -> int:
    report = _foundation_report(args)
    if args.json:
        _dump(report.to_json())
    else:
        summary = report.summary()
        print(f"{summary['title']}: {summary['nodes']} node(s), {summary['source_present']} source-present")
        return _print_findings(report.findings)
    return 0 if report.ok else 1


def cmd_foundations_render(args) -> int:
    report = _foundation_report(args)
    text = report.render_markdown()
    status = _emit(text, args.out, getattr(args, "check", False))
    return status or (0 if report.ok else 1)


def cmd_foundations_html(args) -> int:
    report = _foundation_report(args)
    out = Path(args.out) if args.out else Path(args.path).with_suffix('.html')
    atomic_write_text(out, report.render_html())
    print(out)
    return 0 if report.ok else 1


def cmd_source_staging(args) -> int:
    policy = StagingPolicy.load(args.policy)
    report = check_staging(
        policy,
        root=args.root,
        compile=args.compile,
        timeout=args.timeout,
    )
    if args.json:
        _dump(report.to_json())
    else:
        for module, record in zip(report.normalized_modules, report.policy.records):
            print(f"{record.status:<10} {module}")
        return _print_findings(report.findings)
    return 0 if report.ok else 1


def cmd_source_export(args) -> int:
    policy = ModuleExportPolicy.load(args.policy)
    report = export_modules(
        policy,
        source_root=args.source_root or args.root or '.',
        target_root=args.target_root,
        cluster=args.cluster,
        write=args.write,
    )
    if args.json:
        _dump(report.to_json())
    else:
        for item in report.items:
            print(f"{item.status:<10} {item.source_module} -> {item.target_module}")
        return _print_findings(report.findings)
    return 0 if report.ok else 1


def cmd_source_module_plan(args) -> int:
    policy = ModulePlanPolicy.load(args.policy)
    report = check_module_plan(policy, root=args.root)
    if args.render:
        text = report.render_markdown()
        status = _emit(text, args.out, getattr(args, "check", False))
        return status or (0 if report.ok else 1)
    elif args.json:
        _dump(report.to_json())
    else:
        print(f"{policy.library}: {report.module_count} module(s), {len(report.topics)} topic(s), {len(report.rungs)} rung(s)")
        for topic in report.topics:
            needs = ", ".join(topic.prerequisites) or "-"
            print(f"{topic.id:<8} {len(topic.modules):>3} module(s)  needs: {needs}")
        if report.rungs:
            for rung in report.rungs:
                print(f"rung {rung.id:<8} +{len(rung.new_modules):>3} module(s)  closed={rung.closed_slice}")
        return _print_findings(report.findings)
    return 0 if report.ok else 1


def cmd_certify_build(args) -> int:
    plan = CertificationPlan.load(args.plan)
    report = build_certification_bundle(
        plan,
        root=args.root,
        output_dir=args.out,
        overwrite=args.overwrite,
    )
    archive = None
    if args.archive:
        archive = archive_certification_bundle(report.output_dir, args.archive_path)
    if args.json:
        payload = report.to_json()
        payload['archive'] = str(archive) if archive else None
        _dump(payload)
    else:
        print(report.output_dir)
        if archive:
            print(archive)
        for finding in report.findings:
            print(f"{finding.level.upper():7s} [{finding.code}] {finding.message}")
        print('PASS' if report.ok else 'FAIL')
    return 0 if report.ok else 1


def cmd_literature_validate(args) -> int:
    return _print_findings(load_literature(args.path, root=args.root).validate(), json_mode=args.json)


def cmd_literature_summary(args) -> int:
    data = load_literature(args.path, root=args.root).summary()
    if args.json:
        _dump(data)
    else:
        print(f"{data['title']}\nworks: {data['works']}")
        for field in ('groups', 'priorities', 'distilled_status', 'bibliographic_status'):
            if data[field]:
                print(f"\n{field.replace('_', ' ')}")
                for key, value in data[field].items():
                    print(f"  {key}: {value}")
    return 0


def cmd_literature_show(args) -> int:
    _dump(load_literature(args.path, root=args.root).row(args.id))
    return 0


def cmd_literature_render(args) -> int:
    doc = load_literature(args.path, root=args.root)
    text = doc.render_markdown()
    return _emit(text, args.out, getattr(args, "check", False))


def cmd_literature_latex(args) -> int:
    doc = load_literature(args.path, root=args.root)
    text = doc.render_latex()
    return _emit(text, args.out, getattr(args, "check", False))


def cmd_literature_html(args) -> int:
    doc = load_literature(args.path, root=args.root)
    out = Path(args.out) if args.out else Path(args.path).with_suffix('.html')
    atomic_write_text(out, doc.render_html())
    print(out)
    return 0


def cmd_literature_patch(args) -> int:
    doc = load_literature(args.path, root=args.root)
    doc.patch_work(args.id, _sets(args.set), args.delete)
    findings = doc.validate()
    if any(f.level == 'error' for f in findings) and not args.force:
        return _print_findings(findings)
    doc.write()
    print(doc.path)
    return 0


def cmd_literature_add(args) -> int:
    doc = load_literature(args.path, root=args.root)
    row = load_json(args.from_json)
    if not isinstance(row, dict):
        raise FormalizationToolsError('--from-json must contain one work object')
    doc.append_work(args.id, row)
    findings = doc.validate()
    if any(f.level == 'error' for f in findings) and not args.force:
        return _print_findings(findings)
    doc.write()
    print(doc.path)
    return 0

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aiq-lean", description="Reusable Lean formalization census, audit, and visualization tools")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sv = sub.add_parser("serve", help="serve every viewer from one app, with cross-ledger search and annotation")
    sv.add_argument("--root", help="repository root (default: cwd)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8800)
    sv.add_argument("--title", default="Formalization workspace")
    sv.add_argument("--private-sources",
                    help="JSON file, outside the repository, declaring local-only source documents "
                         "(also read from AIQ_PRIVATE_SOURCES)")
    sv.add_argument("--include-private", action="store_true",
                    help="LOCAL ONLY: render private source excerpts in this browser session. "
                         "Nothing is written to disk, but do not screen-share or export the page")
    sv.set_defaults(func=cmd_serve)


    census = sub.add_parser("census", help="validate, edit, probe, and render source censuses")
    cs = census.add_subparsers(dest="census_command", required=True)
    c = cs.add_parser("init", help="create a starter census document"); c.add_argument("path"); c.add_argument("--title", required=True); c.add_argument("--family", choices=("source-census", "source-completion-census", "source-semantic-alignment"), default="source-census"); c.add_argument("--force", action="store_true"); c.set_defaults(func=cmd_census_init)
    c = cs.add_parser("show", help="show one census row by stable id"); c.add_argument("path"); c.add_argument("--root"); c.add_argument("--id", required=True); c.add_argument("--json", action="store_true"); c.set_defaults(func=cmd_census_show)
    c = cs.add_parser("validate")
    c.add_argument("path"); c.add_argument("--root"); c.add_argument("--no-source-locations", action="store_true"); c.add_argument("--static-declarations", action="store_true"); c.add_argument("--json", action="store_true"); c.set_defaults(func=cmd_census_validate)
    c = cs.add_parser("summary"); c.add_argument("path"); c.add_argument("--root"); c.add_argument("--json", action="store_true"); c.set_defaults(func=cmd_census_summary)
    c = cs.add_parser("render"); c.add_argument("path"); c.add_argument("--root"); c.add_argument("-o", "--out"); c.add_argument("--check", action="store_true", help="verify the file at --out is current instead of writing it"); c.set_defaults(func=cmd_census_render)
    c = cs.add_parser("html"); c.add_argument("path"); c.add_argument("--root"); c.add_argument("-o", "--out"); c.set_defaults(func=cmd_census_html)
    c = cs.add_parser("patch"); c.add_argument("path"); c.add_argument("--root"); c.add_argument("--id", required=True); c.add_argument("--set", action="append", default=[]); c.add_argument("--delete", action="append", default=[]); c.add_argument("--no-source-locations", action="store_true"); c.add_argument("--force", action="store_true"); c.set_defaults(func=cmd_census_patch)
    c = cs.add_parser("add"); c.add_argument("path"); c.add_argument("--root"); c.add_argument("--from-json", required=True); c.add_argument("--no-source-locations", action="store_true"); c.add_argument("--force", action="store_true"); c.set_defaults(func=cmd_census_add)
    c = cs.add_parser("probe"); c.add_argument("path"); c.add_argument("--root"); c.add_argument("--import", dest="imports", action="append", default=[]); c.add_argument("--timeout", type=int, default=3600); c.add_argument("--write", action="store_true"); c.add_argument("--json", action="store_true"); c.set_defaults(func=cmd_census_probe)

    review = sub.add_parser("review", help="work with clause-by-clause semantic reviews")
    rs = review.add_subparsers(dest="review_command", required=True)
    r = rs.add_parser("init", help="create a starter semantic-review document"); r.add_argument("path"); r.add_argument("--title", required=True); r.add_argument("--companion-census"); r.add_argument("--force", action="store_true"); r.set_defaults(func=cmd_review_init)
    r = rs.add_parser("show", help="show one semantic-review row by stable id"); r.add_argument("path"); r.add_argument("--root"); r.add_argument("--id", required=True); r.add_argument("--json", action="store_true"); r.set_defaults(func=cmd_review_show)
    r = rs.add_parser("validate"); r.add_argument("path"); r.add_argument("--root"); r.add_argument("--json", action="store_true"); r.set_defaults(func=cmd_review_validate)
    r = rs.add_parser("summary"); r.add_argument("path"); r.add_argument("--root"); r.add_argument("--json", action="store_true"); r.set_defaults(func=cmd_review_summary)
    r = rs.add_parser("render"); r.add_argument("path"); r.add_argument("--root"); r.add_argument("-o", "--out"); r.add_argument("--check", action="store_true", help="verify the file at --out is current instead of writing it"); r.set_defaults(func=cmd_review_render)
    r = rs.add_parser("html"); r.add_argument("path"); r.add_argument("--root"); r.add_argument("-o", "--out"); r.set_defaults(func=cmd_review_html)
    r = rs.add_parser("patch"); r.add_argument("path"); r.add_argument("--root"); r.add_argument("--id", required=True); r.add_argument("--set", action="append", default=[]); r.add_argument("--delete", action="append", default=[]); r.add_argument("--force", action="store_true"); r.set_defaults(func=cmd_review_patch)
    r = rs.add_parser("add"); r.add_argument("path"); r.add_argument("--root"); r.add_argument("--from-json", required=True); r.add_argument("--force", action="store_true"); r.set_defaults(func=cmd_review_add)

    coverage = sub.add_parser("coverage", help="validate and inspect result/source-fidelity coverage inventories")
    cvs = coverage.add_subparsers(dest="coverage_command", required=True)
    cv = cvs.add_parser("validate"); cv.add_argument("path"); cv.add_argument("--root"); cv.add_argument("--static-declarations", action="store_true"); cv.add_argument("--json", action="store_true"); cv.set_defaults(func=cmd_coverage_validate)
    cv = cvs.add_parser("summary"); cv.add_argument("path"); cv.add_argument("--root"); cv.add_argument("--json", action="store_true"); cv.set_defaults(func=cmd_coverage_summary)
    cv = cvs.add_parser("show"); cv.add_argument("path"); cv.add_argument("--root"); group=cv.add_mutually_exclusive_group(required=True); group.add_argument("--id"); group.add_argument("--atom"); cv.set_defaults(func=cmd_coverage_show)
    cv = cvs.add_parser("render"); cv.add_argument("path"); cv.add_argument("--root"); cv.add_argument("-o", "--out"); cv.add_argument("--check", action="store_true", help="verify the file at --out is current instead of writing it"); cv.set_defaults(func=cmd_coverage_render)
    cv = cvs.add_parser("html"); cv.add_argument("path"); cv.add_argument("--root"); cv.add_argument("-o", "--out"); cv.set_defaults(func=cmd_coverage_html)
    cv = cvs.add_parser("patch"); cv.add_argument("path"); cv.add_argument("--root"); group=cv.add_mutually_exclusive_group(required=True); group.add_argument("--id"); group.add_argument("--atom"); cv.add_argument("--set", action="append", default=[]); cv.add_argument("--delete", action="append", default=[]); cv.add_argument("--force", action="store_true"); cv.set_defaults(func=cmd_coverage_patch)

    workspace = sub.add_parser("workspace", help="aggregate the whole formalization effort")
    ws = workspace.add_subparsers(dest="workspace_command", required=True)
    w = ws.add_parser("init", help="create starter manifest, grounding policy, and source ledgers"); w.add_argument("--root"); w.add_argument("--name", required=True); w.add_argument("--source", action="append", default=[]); w.add_argument("--force", action="store_true"); w.set_defaults(func=cmd_workspace_init)
    w = ws.add_parser("status"); w.add_argument("--root"); w.add_argument("--source-audit", action="store_true"); w.add_argument("--json", action="store_true"); w.set_defaults(func=cmd_workspace_status)
    w = ws.add_parser("validate"); w.add_argument("--root"); w.add_argument("--no-source-locations", action="store_true"); w.add_argument("--static-declarations", action="store_true"); w.add_argument("--json", action="store_true"); w.set_defaults(func=cmd_workspace_validate)
    w = ws.add_parser("html"); w.add_argument("--root"); w.add_argument("--source-audit", action="store_true"); w.add_argument("-o", "--out"); w.set_defaults(func=cmd_workspace_html)

    source = sub.add_parser("source", help="Python-only Lean source audits")
    ss = source.add_subparsers(dest="source_command", required=True)
    s = ss.add_parser("scan"); s.add_argument("--root"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_scan)
    s = ss.add_parser("duplicates"); s.add_argument("--root"); s.add_argument("--prefix", action="append", default=[], help="restrict to modules under this prefix (repeatable)"); s.add_argument("--exclude-prefix", action="append", default=[], help="skip modules under this prefix (repeatable)"); s.add_argument("--baseline", help="JSON/YAML of accepted findings with reasons"); s.add_argument("--write-baseline", metavar="PATH"); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_duplicates)
    s = ss.add_parser("admissions"); s.add_argument("--root"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_admissions)
    s = ss.add_parser("docstrings"); s.add_argument("--root"); s.add_argument("--prefix", action="append", default=[]); s.add_argument("--exclude-prefix", action="append", default=[]); s.add_argument("--baseline", help="JSON/YAML of tolerated undocumented declarations"); s.add_argument("--write-baseline", metavar="PATH"); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_docstrings)
    s = ss.add_parser("proof-length"); s.add_argument("--root"); s.add_argument("--library", action="append", default=[]); s.add_argument("--min", type=int, default=50); s.add_argument("--scaffold-definition", choices=("published", "have-term", "have-all", "have-and-cases"), default="published"); s.add_argument("--extractable", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_proof_length)
    s = ss.add_parser("snapshot"); s.add_argument("--root"); s.add_argument("-o", "--out", required=True); s.set_defaults(func=cmd_source_snapshot)
    s = ss.add_parser("drift"); s.add_argument("baseline"); s.add_argument("--root"); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_drift)
    s = ss.add_parser("aggregates", help="generate or check recursive import-only aggregate modules")
    s.add_argument("--root"); s.add_argument("--base", required=True); s.add_argument("--library"); s.add_argument("--aggregate-name", default="All.lean"); s.add_argument("--skip-dir", action="append", default=[]); s.add_argument("--no-preserve-foreign", action="store_true"); s.add_argument("--ignore-import-cycles", action="store_true", help="place every module in its own directory aggregate even when that is a Lake build cycle"); s.add_argument("--root-import"); s.add_argument("--header-file"); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_aggregates)
    s = ss.add_parser("conflicts", help="find exact Git merge-conflict markers")
    s.add_argument("--root"); s.add_argument("--all-files", action="store_true", help="scan all files instead of Git-tracked files"); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_conflicts)
    s = ss.add_parser("orphan-artifacts", help="find Lake build products whose Lean source is gone")
    s.add_argument("--root"); s.add_argument("--library", action="append", default=[]); s.add_argument("--fix", action="store_true"); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_orphan_artifacts)
    s = ss.add_parser("declaration", help="show human-written Lean declaration headers and relevant ambient binders")
    s.add_argument("names", nargs="+"); s.add_argument("--root"); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_declaration)
    s = ss.add_parser("private-shadows", help="find private declarations shadowing public imports")
    s.add_argument("--root"); s.add_argument("--prefix", action="append", default=[], help="restrict to modules under this prefix (repeatable)"); s.add_argument("--exclude-prefix", action="append", default=[], help="skip modules under this prefix (repeatable)"); s.add_argument("--baseline", help="JSON/YAML of accepted findings with reasons"); s.add_argument("--write-baseline", metavar="PATH", help="record the current findings as accepted"); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_private_shadows)
    s = ss.add_parser("similar", help="find normalized theorem-statement or definition-body duplicate candidates")
    s.add_argument("--root"); s.add_argument("--library", action="append", default=[]); s.add_argument("--definitions", action="store_true"); s.add_argument("--min-chars", type=int, default=60); s.add_argument("--include-forwarders", action="store_true"); s.add_argument("--all-files", action="store_true"); s.add_argument("--top", type=int, default=25); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_similar)
    s = ss.add_parser("large", help="rank declarations by statement and body length")
    s.add_argument("--root"); s.add_argument("--library", action="append", default=[]); s.add_argument("--all-files", action="store_true"); s.add_argument("--top", type=int, default=25); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_large)
    s = ss.add_parser("dead", help="find definition candidates with no extra short-name occurrence")
    s.add_argument("--root"); s.add_argument("--library", action="append", default=[]); s.add_argument("--all-files", action="store_true"); s.add_argument("--top", type=int, default=100); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_dead)
    s = ss.add_parser("names", help="find declarations matching provisional or opinion-bearing naming patterns")
    s.add_argument("--root"); s.add_argument("--library", action="append", default=[]); s.add_argument("--pattern"); s.add_argument("--all-files", action="store_true"); s.add_argument("--top", type=int, default=100); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_names)
    s = ss.add_parser("definitional-escapes", help="separate sorry/admit-defined terms from proof-only admissions")
    s.add_argument("--root"); s.add_argument("--library", action="append", default=[]); s.add_argument("--all-files", action="store_true"); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_definitional_escapes)
    s = ss.add_parser("profile", help="summarize per-file review signals for a large Lean tree")
    s.add_argument("--root"); s.add_argument("--library", action="append", default=[]); s.add_argument("--min-chars", type=int, default=60); s.add_argument("--all-files", action="store_true"); s.add_argument("--details", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_profile)
    s = ss.add_parser("symbol-census", help="compare raw mentions with comments/imports/commands removed")
    s.add_argument("expression"); s.add_argument("--regex", action="store_true"); s.add_argument("--root"); s.add_argument("--include", action="append", default=[]); s.add_argument("--exclude", action="append", default=[]); s.add_argument("--keep-imports", action="store_true"); s.add_argument("--keep-commands", action="store_true"); s.add_argument("--verbose", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_symbol_census)
    s = ss.add_parser("checklist", help="generate file and group review checklists while preserving checked marks")
    s.add_argument("--root"); s.add_argument("--file", default="dev/audit/FILE-CHECKLIST.md"); s.add_argument("--groups", default="dev/audit/GROUP-CHECKLIST.md"); s.add_argument("--include", action="append", default=[]); s.add_argument("--exclude", action="append", default=[]); s.add_argument("--group-depth", type=int, default=2); s.add_argument("--no-kind-split", action="store_true"); s.add_argument("--progress", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_checklist)

    s = ss.add_parser("module-coverage", help="require every module in a source scope to be root-reachable or explicitly excluded")
    s.add_argument("policy"); s.add_argument("--root"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_module_coverage)
    s = ss.add_parser("roadmap", help="compare suggested Lean declaration names with delivered libraries")
    s.add_argument("--roadmap-root", required=True); s.add_argument("--root"); s.add_argument("--suggested-glob", default="**/Suggested.lean"); s.add_argument("--library", action="append", default=[]); s.add_argument("--prefer", action="append", default=[]); s.add_argument("--topic"); s.add_argument("--missing", action="store_true"); s.add_argument("--map", action="store_true"); s.add_argument("--ambiguous", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_roadmap)

    s = ss.add_parser("grounding", help="check a policy-driven structural grounding contract")
    s.add_argument("policy"); s.add_argument("--root"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_grounding)
    s = ss.add_parser("import-closure", help="compare conservative source-level local import closures")
    s.add_argument("--root"); s.add_argument("--target", action="append", required=True, help="LABEL=Lean.Module"); s.add_argument("--unresolved", action="store_true"); s.add_argument("--html"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_import_closure)
    s = ss.add_parser("provenance", help="inventory recorded ## Provenance blocks in Lean module docs")
    s.add_argument("--root"); s.add_argument("--include", action="append", default=[]); s.add_argument("--heading", default="Provenance"); s.add_argument("--marker", action="append", default=[], help="LABEL=regex"); s.add_argument("--details", action="store_true"); s.add_argument("--require", action="store_true", help="fail when a scanned file has no matching block"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_provenance)
    s = ss.add_parser("module-migrate", help="convert Lean files to module/public-import style")
    s.add_argument("paths", nargs="+"); s.add_argument("--write", action="store_true"); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_module_migrate)
    s = ss.add_parser("staging", help="validate a configurable staging-module registry")
    s.add_argument("policy"); s.add_argument("--root"); s.add_argument("--compile", action="store_true"); s.add_argument("--timeout", type=int, default=600); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_staging)
    s = ss.add_parser("export", help="check or write a manifest-driven Lean module promotion")
    s.add_argument("policy"); s.add_argument("--root"); s.add_argument("--source-root"); s.add_argument("--target-root", required=True); s.add_argument("--cluster"); s.add_argument("--write", action="store_true"); s.add_argument("--json", action="store_true"); s.set_defaults(func=cmd_source_export)
    s = ss.add_parser("module-plan", help="validate an ordered module-topic partition and optional dependency-closed submission ladder")
    s.add_argument("policy"); s.add_argument("--root"); s.add_argument("--json", action="store_true"); s.add_argument("--render", action="store_true", help="render the report as Markdown"); s.add_argument("-o", "--out", help="write Markdown when --render is used"); s.add_argument("--check", action="store_true", help="verify the file at --out is current instead of writing it"); s.set_defaults(func=cmd_source_module_plan)

    signatures = sub.add_parser("signatures", help="compare exact Lean declaration interfaces across module pairs")
    sigs = signatures.add_subparsers(dest="signatures_command", required=True)
    sg = sigs.add_parser("check"); sg.add_argument("policy"); sg.add_argument("--root"); sg.add_argument("--no-build", action="store_true"); sg.add_argument("--timeout", type=int, default=600); sg.add_argument("--json", action="store_true"); sg.set_defaults(func=cmd_signatures_check)

    foundations = sub.add_parser("foundations", help="validate and visualize recursive foundation campaigns")
    fss = foundations.add_subparsers(dest="foundations_command", required=True)
    for name, func in (("validate", cmd_foundations_validate), ("render", cmd_foundations_render), ("html", cmd_foundations_html)):
        f = fss.add_parser(name); f.add_argument("path"); f.add_argument("--root"); f.add_argument("--lean-probe", action="store_true"); f.add_argument("--timeout", type=int, default=600); f.add_argument("--json", action="store_true") if name == "validate" else None; f.add_argument("-o", "--out") if name in {"render", "html"} else None; f.add_argument("--check", action="store_true", help="verify the file at --out is current instead of writing it") if name == "render" else None; f.set_defaults(func=func)

    literature = sub.add_parser("literature", help="validate, edit, and render literature/source inventories")
    lss = literature.add_subparsers(dest="literature_command", required=True)
    l = lss.add_parser("validate"); l.add_argument("path"); l.add_argument("--root"); l.add_argument("--json", action="store_true"); l.set_defaults(func=cmd_literature_validate)
    l = lss.add_parser("summary"); l.add_argument("path"); l.add_argument("--root"); l.add_argument("--json", action="store_true"); l.set_defaults(func=cmd_literature_summary)
    l = lss.add_parser("show"); l.add_argument("path"); l.add_argument("--root"); l.add_argument("--id", required=True); l.set_defaults(func=cmd_literature_show)
    l = lss.add_parser("render"); l.add_argument("path"); l.add_argument("--root"); l.add_argument("-o", "--out"); l.add_argument("--check", action="store_true", help="verify the file at --out is current instead of writing it"); l.set_defaults(func=cmd_literature_render)
    l = lss.add_parser("latex"); l.add_argument("path"); l.add_argument("--root"); l.add_argument("-o", "--out"); l.add_argument("--check", action="store_true", help="verify the file at --out is current instead of writing it"); l.set_defaults(func=cmd_literature_latex)
    l = lss.add_parser("html"); l.add_argument("path"); l.add_argument("--root"); l.add_argument("-o", "--out"); l.set_defaults(func=cmd_literature_html)
    l = lss.add_parser("patch"); l.add_argument("path"); l.add_argument("--root"); l.add_argument("--id", required=True); l.add_argument("--set", action="append", default=[]); l.add_argument("--delete", action="append", default=[]); l.add_argument("--force", action="store_true"); l.set_defaults(func=cmd_literature_patch)
    l = lss.add_parser("add"); l.add_argument("path"); l.add_argument("--root"); l.add_argument("--id", required=True); l.add_argument("--from-json", required=True); l.add_argument("--force", action="store_true"); l.set_defaults(func=cmd_literature_add)

    certify = sub.add_parser("certify", help="build reproducible evidence bundles from a declarative plan")
    cts = certify.add_subparsers(dest="certify_command", required=True)
    ct = cts.add_parser("build"); ct.add_argument("plan"); ct.add_argument("--root"); ct.add_argument("--out", required=True); ct.add_argument("--overwrite", action="store_true"); ct.add_argument("--archive", action="store_true"); ct.add_argument("--archive-path"); ct.add_argument("--json", action="store_true"); ct.set_defaults(func=cmd_certify_build)

    imports = sub.add_parser("imports", help="check configurable Lean module layering policies")
    ims = imports.add_subparsers(dest="imports_command", required=True)
    i = ims.add_parser("check"); i.add_argument("policy"); i.add_argument("--root"); i.add_argument("--json", action="store_true"); i.set_defaults(func=cmd_imports_check)

    namespace = sub.add_parser("namespaces", help="check configurable Lean namespace placement policies")
    nss = namespace.add_subparsers(dest="namespace_command", required=True)
    n = nss.add_parser("check"); n.add_argument("policy"); n.add_argument("--root"); n.add_argument("--json", action="store_true"); n.set_defaults(func=cmd_namespace_check)

    gates = sub.add_parser("gates", help="discover and run scripts/check_*.py with consistent strictness")
    gs = gates.add_subparsers(dest="gates_command", required=True)
    g = gs.add_parser("list"); g.add_argument("--root"); g.add_argument("--config"); g.add_argument("-k", "--filter", default=""); g.add_argument("--json", action="store_true"); g.set_defaults(func=cmd_gates_list)
    g = gs.add_parser("run"); g.add_argument("--root"); g.add_argument("--config"); g.add_argument("-k", "--filter", default=""); g.add_argument("--fast", action="store_true"); g.add_argument("--timeout", type=int, default=3600); g.add_argument("--python"); g.add_argument("--tail", type=int, default=12); g.add_argument("--json", action="store_true"); g.set_defaults(func=cmd_gates_run)

    ratchet = sub.add_parser("ratchet", help="check configurable regex-count source ratchets")
    rts = ratchet.add_subparsers(dest="ratchet_command", required=True)
    rt = rts.add_parser("check"); rt.add_argument("policy"); rt.add_argument("--root"); rt.add_argument("--list-files", action="store_true"); rt.add_argument("--json", action="store_true"); rt.set_defaults(func=cmd_ratchet_check)

    alignment = sub.add_parser("alignment", help="build curated source-to-Lean review packets")
    als = alignment.add_subparsers(dest="alignment_command", required=True)

    def add_source_args(sp) -> None:
        sp.add_argument("--no-sources", action="store_true",
                        help="do not resolve source locators; show only the review's own restatement")
        sp.add_argument("--private-sources",
                        help="JSON file, outside the repository, declaring local-only source documents "
                             "(also read from AIQ_PRIVATE_SOURCES)")
        sp.add_argument("--include-private", action="store_true",
                        help="LOCAL ONLY: embed private source text in the output. Never use this for a "
                             "file that is committed, published, or shared")

    def add_row_arg(sp) -> None:
        sp.add_argument("--row", action="append",
                        help="review exactly this census row id, whatever its importance (repeatable)")

    a = als.add_parser("render")
    a.add_argument("census", nargs="+")
    a.add_argument("--root")
    a.add_argument("--importance", choices=("headline", "major", "supporting", "technical"), default="headline")
    a.add_argument("--probe", action="store_true")
    a.add_argument("--import", dest="imports", action="append", default=[])
    a.add_argument("--timeout", type=int, default=3600)
    a.add_argument("-o", "--out")
    a.add_argument("--check", action="store_true", help="verify the file at --out is current instead of writing it")
    a.add_argument("--statements", action="store_true", help="add elaborated signatures, statement closures and pin status from a leanq statement sidecar (invokes Lean unless --sidecar is given)")
    a.add_argument("--sidecar", help="read statement records from this leanq sidecar JSONL instead of building one")
    a.add_argument("--lib", help="library label for the statement sidecar")
    a.add_argument("--refresh", action="store_true", help="rebuild the statement sidecar first")
    add_source_args(a)
    add_row_arg(a)
    a.set_defaults(func=cmd_alignment_render)

    a = als.add_parser("html", help="one self-contained review page: source statement, clauses, elaborated signatures, pin status, statement closures, and proof dependencies")
    a.add_argument("census", nargs="+")
    a.add_argument("--root")
    a.add_argument("--importance", choices=("headline", "major", "supporting", "technical"), default="headline")
    a.add_argument("--statements", action="store_true", help="build/refresh a leanq statement sidecar (invokes Lean)")
    a.add_argument("--sidecar", help="read statement records from this leanq sidecar JSONL")
    a.add_argument("--lib", help="library label for the statement sidecar")
    a.add_argument("--refresh", action="store_true")
    a.add_argument("--graph", help="a saved `leanq graph-index` JSON for the proof-dependency panel")
    a.add_argument("--title", default="Semantic alignment review")
    a.add_argument("--json", action="store_true", help="write the page payload instead of HTML")
    a.add_argument("-o", "--out")
    a.add_argument("--check", action="store_true")
    add_source_args(a)
    add_row_arg(a)
    a.set_defaults(func=cmd_alignment_html)

    def add_pin_args(sp) -> None:
        sp.add_argument("document", help="a source census with embedded reviews, or a standalone semantic review")
        sp.add_argument("--root")
        sp.add_argument("--id", action="append", help="restrict to these row ids (repeatable)")
        sp.add_argument("--sidecar", help="read statement records from this leanq sidecar JSONL instead of invoking Lean")
        sp.add_argument("--lib", help="library label for the statement sidecar")
        sp.add_argument("--refresh", action="store_true", help="rebuild the statement sidecar first")

    a = als.add_parser("pin", help="record the elaborated-type hashes of each reviewed declaration, and the content hash of each source passage it cites")
    add_pin_args(a)
    add_source_args(a)
    a.add_argument("--note", help="free-text note stored with every pin written")
    a.add_argument("--statements-only", action="store_true", help="do not touch source pins")
    a.add_argument("--source-only", action="store_true", help="pin source passages only; do not invoke Lean")
    a.set_defaults(func=cmd_alignment_pin)
    a = als.add_parser("check", help="fail when a pinned elaborated type, or a pinned source passage, differs from what the review accepted")
    add_pin_args(a)
    add_source_args(a)
    a.add_argument("--statements-only", action="store_true")
    a.add_argument("--source-only", action="store_true", help="check source passages only; do not invoke Lean")
    a.set_defaults(func=cmd_alignment_check)
    a = als.add_parser("adopt-source", help="turn a row's legacy single source_locator into an explicit primary source fragment")
    a.add_argument("document")
    a.add_argument("--root")
    a.add_argument("--id", action="append")
    a.add_argument("--dry-run", action="store_true")
    a.set_defaults(func=cmd_alignment_adopt_source)

    history = sub.add_parser("history", help="summarize auditable Git and co-author provenance")
    hs = history.add_subparsers(dest="history_command", required=True)
    h = hs.add_parser("summary"); h.add_argument("--root"); h.add_argument("--rev", default="HEAD"); h.add_argument("--path", action="append", default=[]); h.add_argument("--json", action="store_true"); h.set_defaults(func=cmd_history_summary)
    h = hs.add_parser("commits"); h.add_argument("--root"); h.add_argument("--rev", default="HEAD"); h.add_argument("--path", action="append", default=[]); h.add_argument("--json", action="store_true"); h.set_defaults(func=cmd_history_commits)
    h = hs.add_parser("html"); h.add_argument("--root"); h.add_argument("--rev", default="HEAD"); h.add_argument("--path", action="append", default=[]); h.add_argument("--title", default="Formalization history"); h.add_argument("-o", "--out", required=True); h.set_defaults(func=cmd_history_html)

    manifest = sub.add_parser("manifest", help="inspect formalization.yaml")
    ms = manifest.add_subparsers(dest="manifest_command", required=True)
    m = ms.add_parser("validate"); m.add_argument("path", nargs="?", default="formalization.yaml"); m.add_argument("--json", action="store_true"); m.set_defaults(func=cmd_manifest_validate)
    m = ms.add_parser("summary"); m.add_argument("path", nargs="?", default="formalization.yaml"); m.add_argument("--json", action="store_true"); m.set_defaults(func=cmd_manifest_summary)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FormalizationToolsError as ex:
        print(f"aiq-lean: {ex}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

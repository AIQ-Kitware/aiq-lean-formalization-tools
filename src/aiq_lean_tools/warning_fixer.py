#!/usr/bin/env python3
"""Apply a conservative subset of Lean linter/deprecation suggestions.

The script works from an existing build log or can run a build command itself.
By default it only prints a diff. Pass --write to modify files.

Supported mechanical warning classes:
- proposition-style haveI/letI -> have/let;
- deprecated constant replacement when Lean gives an exact replacement;
- direct unnecessary `simpa` -> `simp` suggestions;
- unused simp arguments;
- unnecessary <;> sequencing -> ;;
- tactics explicitly reported as doing nothing;
- unused section variables via `omit ... in`, placed before declaration docstrings.

Everything else is reported as skipped. In particular, proof-search/tactic suggestions
such as ring_nf and abel_nf, `simpa using ...` rewrites, and declaration-kind
suggestions are not applied automatically.
"""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable


WARNING_RE = re.compile(r"^warning: (?P<file>.+?):(?P<line>\d+):(?P<col>\d+): (?P<message>.*)$")
DEPRECATED_RE = re.compile(r"`(?P<old>[^`]+)` has been deprecated: Use `(?P<new>[^`]+)` instead")
UNUSED_SECTION_PREFIX = "automatically included section variable(s) unused in theorem"
UNUSED_SIMP_PREFIX = "This simp argument is unused:"
SEQ_FOCUS_PREFIX = "Used `tac1 <;> tac2` where `(tac1; tac2)` would suffice"
UNNECESSARY_SIMPA_PREFIX = "try 'simp' instead of 'simpa'"


@dataclasses.dataclass(frozen=True)
class Diagnostic:
    file: str
    line: int
    col: int
    message: str
    body: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Edit:
    start: int
    end: int
    replacement: str
    kind: str
    detail: str


@dataclasses.dataclass
class FilePlan:
    path: Path
    original: str
    edits: list[Edit] = dataclasses.field(default_factory=list)

    def add(self, edit: Edit) -> None:
        self.edits.append(edit)

    def render(self) -> str:
        edits = sorted(self.edits, key=lambda e: (e.start, e.end))
        merged: list[Edit] = []
        for edit in edits:
            if (
                merged
                and merged[-1].replacement == ""
                and edit.replacement == ""
                and edit.start <= merged[-1].end
            ):
                prev = merged[-1]
                merged[-1] = Edit(
                    prev.start,
                    max(prev.end, edit.end),
                    "",
                    prev.kind,
                    f"{prev.detail}, {edit.detail}",
                )
            else:
                merged.append(edit)

        text = self.original
        last_start = len(text) + 1
        for edit in reversed(merged):
            if edit.end > last_start:
                raise ValueError(f"overlapping edits in {self.path}: {edit}")
            text = text[: edit.start] + edit.replacement + text[edit.end :]
            last_start = edit.start
        return text


@dataclasses.dataclass(frozen=True)
class Result:
    diagnostic: Diagnostic
    status: str
    kind: str
    detail: str


def parse_diagnostics(text: str) -> list[Diagnostic]:
    lines = text.splitlines()
    out: list[Diagnostic] = []
    i = 0
    while i < len(lines):
        m = WARNING_RE.match(lines[i])
        if not m:
            i += 1
            continue
        body: list[str] = []
        j = i + 1
        while j < len(lines) and not WARNING_RE.match(lines[j]):
            # Stop at another top-level diagnostic. Build progress lines are harmless
            # inside the body and are ignored by the individual parsers.
            if re.match(r"^(error|info): .+?:\d+:\d+:", lines[j]):
                break
            body.append(lines[j])
            j += 1
        out.append(
            Diagnostic(
                file=m.group("file"),
                line=int(m.group("line")),
                col=int(m.group("col")),
                message=m.group("message"),
                body=tuple(body),
            )
        )
        i = j
    return out


def line_offsets(text: str) -> tuple[list[str], list[int]]:
    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    pos = 0
    for line in lines:
        starts.append(pos)
        pos += len(line)
    return lines, starts


def line_span(text: str, line_no: int) -> tuple[int, int, str] | None:
    lines, starts = line_offsets(text)
    idx = line_no - 1
    if idx < 0 or idx >= len(lines):
        return None
    return starts[idx], starts[idx] + len(lines[idx]), lines[idx]


def find_near(line: str, token: str, col: int) -> int | None:
    positions = [m.start() for m in re.finditer(re.escape(token), line)]
    if not positions:
        return None
    return min(positions, key=lambda p: abs(p - col))


def body_text(d: Diagnostic) -> str:
    return "\n".join((d.message, *d.body))


def parse_unused_simp_arg(d: Diagnostic) -> str | None:
    if not d.message.startswith(UNUSED_SIMP_PREFIX):
        return None
    for line in d.body:
        s = line.strip()
        if not s:
            continue
        if s.startswith("Hint:") or s.startswith("Note:") or s.startswith("["):
            return None
        return s
    return None


def parse_unused_section_vars(d: Diagnostic) -> str | None:
    if not d.message.startswith(UNUSED_SECTION_PREFIX):
        return None
    pieces: list[str] = []
    for line in d.body:
        s = line.strip()
        if not s:
            continue
        if s.startswith("consider restructuring"):
            break
        pieces.append(s)
    if not pieces:
        return None
    joined = " ".join(pieces)
    # Guard against accidentally swallowing build progress or the suggested text.
    if "consider restructuring" in joined or "omit " in joined or joined.startswith(("Note:", "warning:")):
        return None
    return joined


def declaration_insertion_offset(text: str, line_no: int) -> int | None:
    lines, starts = line_offsets(text)
    idx = line_no - 1
    if idx < 0 or idx >= len(lines):
        return None

    insertion = idx

    # Attributes belong after `omit ... in`, so walk above any attribute block.
    while insertion > 0 and lines[insertion - 1].lstrip().startswith("@["):
        insertion -= 1

    # A declaration docstring is attached to the following command. The repository
    # convention requires command modifiers to be placed before the docstring.
    if insertion > 0 and lines[insertion - 1].rstrip().endswith("-/"):
        j = insertion - 1
        while j >= 0:
            if "/--" in lines[j]:
                insertion = j
                break
            j -= 1
        else:
            return None

    return starts[insertion]


def edit_style_instance(plan: FilePlan, d: Diagnostic) -> Edit | None:
    text = body_text(d)
    if "The goal is a proposition, so `have` is preferred over `haveI`." in text:
        old, new = "haveI", "have"
    elif "The goal is a proposition, so `let` is preferred over `letI`." in text:
        old, new = "letI", "let"
    else:
        return None
    span = line_span(plan.original, d.line)
    if span is None:
        return None
    start, _, line = span
    p = find_near(line, old, d.col)
    if p is None:
        return None
    return Edit(start + p, start + p + len(old), new, "style-instance", f"{old} -> {new}")


def edit_deprecated(plan: FilePlan, d: Diagnostic) -> Edit | None:
    m = DEPRECATED_RE.search(d.message)
    if not m:
        return None
    old, new = m.group("old"), m.group("new")
    span = line_span(plan.original, d.line)
    if span is None:
        return None
    start, _, line = span
    p = find_near(line, old, d.col)
    if p is None:
        return None
    return Edit(start + p, start + p + len(old), new, "deprecated", f"{old} -> {new}")


def edit_seq_focus(plan: FilePlan, d: Diagnostic) -> Edit | None:
    if not d.message.startswith(SEQ_FOCUS_PREFIX):
        return None
    span = line_span(plan.original, d.line)
    if span is None:
        return None
    start, _, line = span
    p = find_near(line, "<;>", d.col)
    if p is None:
        return None
    return Edit(start + p, start + p + 3, ";", "seq-focus", "<;> -> ;")



def edit_unnecessary_simpa(plan: FilePlan, d: Diagnostic) -> Edit | None:
    if not d.message.startswith(UNNECESSARY_SIMPA_PREFIX):
        return None
    span = line_span(plan.original, d.line)
    if span is None:
        return None
    start, _, line = span
    # The linter emits this diagnostic only when the same tactic arguments can be
    # used with `simp`. Keep this editor deliberately narrow: `simpa using ...`
    # has a different shape and is left for a human.
    if " using " in line:
        return None
    p = find_near(line, "simpa", d.col)
    if p is None:
        return None
    return Edit(start + p, start + p + len("simpa"), "simp", "unnecessary-simpa", "simpa -> simp")

def edit_unused_simp(plan: FilePlan, d: Diagnostic) -> Edit | None:
    arg = parse_unused_simp_arg(d)
    if arg is None:
        return None
    span = line_span(plan.original, d.line)
    if span is None:
        return None
    line_start, _, line = span
    p = find_near(line, arg, d.col)
    if p is None:
        return None
    arg_start = line_start + p
    arg_end = arg_start + len(arg)
    text = plan.original

    q = arg_start - 1
    while q >= 0 and text[q].isspace():
        q -= 1
    r = arg_end
    while r < len(text) and text[r].isspace():
        r += 1

    if q >= 0 and text[q] == ",":
        return Edit(q, arg_end, "", "unused-simp", arg)
    if r < len(text) and text[r] == ",":
        return Edit(arg_start, r + 1, "", "unused-simp", arg)
    if q >= 0 and r < len(text) and text[q] == "[" and text[r] == "]":
        return Edit(arg_start, arg_end, "", "unused-simp", arg)
    return None


def tactic_command_span(text: str, line_no: int) -> tuple[int, int] | None:
    lines, starts = line_offsets(text)
    idx = line_no - 1
    if idx < 0 or idx >= len(lines):
        return None
    first = lines[idx]
    indent = len(first) - len(first.lstrip())
    end_idx = idx + 1
    while end_idx < len(lines):
        nxt = lines[end_idx]
        if not nxt.strip():
            break
        nxt_indent = len(nxt) - len(nxt.lstrip())
        if nxt_indent <= indent:
            break
        end_idx += 1
    start = starts[idx]
    end = starts[end_idx] if end_idx < len(starts) else len(text)
    return start, end


def edit_noop_tactic(plan: FilePlan, d: Diagnostic) -> Edit | None:
    msg = body_text(d)
    if "tactic does nothing" not in msg:
        return None
    span = line_span(plan.original, d.line)
    if span is None:
        return None
    _, _, line = span
    stripped = line.strip()
    if stripped.startswith("change "):
        cmd = tactic_command_span(plan.original, d.line)
        if cmd is None:
            return None
        return Edit(cmd[0], cmd[1], "", "noop-tactic", "change")
    if stripped == "push_cast" or stripped.startswith("push_cast at "):
        return Edit(span[0], span[1], "", "noop-tactic", "push_cast")
    if "'ring' tactic does nothing" in msg:
        p = find_near(line, "ring", d.col)
        if p is not None:
            prefix = line[:p]
            m = re.search(r"\s*<;>\s*$", prefix)
            if m:
                start = span[0] + m.start()
                return Edit(start, span[0] + p + len("ring"), "", "noop-tactic", "ring")
    return None


def edit_unused_section(plan: FilePlan, d: Diagnostic) -> Edit | None:
    vars_text = parse_unused_section_vars(d)
    if vars_text is None:
        return None
    off = declaration_insertion_offset(plan.original, d.line)
    if off is None:
        return None
    wanted = f"omit {vars_text} in"
    before_text = plan.original[:off]
    before_lines = before_text.splitlines(keepends=True)
    if before_lines:
        previous = before_lines[-1]
        previous_stripped = previous.strip()
        if previous_stripped == wanted:
            return None
        m = re.fullmatch(r"omit\s+(.+?)\s+in", previous_stripped)
        if m:
            previous_start = off - len(previous)
            indent = previous[: len(previous) - len(previous.lstrip())]
            newline = "\n" if previous.endswith("\n") else ""
            combined = f"{indent}omit {m.group(1)} {vars_text} in{newline}"
            return Edit(previous_start, off, combined, "unused-section", vars_text)
    return Edit(off, off, wanted + "\n", "unused-section", vars_text)


EDITORS = (
    edit_style_instance,
    edit_deprecated,
    edit_unnecessary_simpa,
    edit_unused_simp,
    edit_seq_focus,
    edit_noop_tactic,
    edit_unused_section,
)


def plan_changes(root: Path, diagnostics: Iterable[Diagnostic]) -> tuple[dict[Path, FilePlan], list[Result]]:
    plans: dict[Path, FilePlan] = {}
    results: list[Result] = []
    seen_edits: set[tuple[Path, int, int, str]] = set()

    for d in diagnostics:
        rel = Path(d.file)
        path = (rel if rel.is_absolute() else root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            results.append(Result(d, "skipped", "outside-root", str(path)))
            continue
        if not path.is_file():
            results.append(Result(d, "skipped", "missing-file", str(path)))
            continue
        plan = plans.get(path)
        if plan is None:
            plan = FilePlan(path=path, original=path.read_text())
            plans[path] = plan

        edit = None
        for editor in EDITORS:
            edit = editor(plan, d)
            if edit is not None:
                break
        if edit is None:
            results.append(Result(d, "skipped", "unsupported", d.message.splitlines()[0]))
            continue

        key = (path, edit.start, edit.end, edit.replacement)
        if key in seen_edits:
            results.append(Result(d, "skipped", "duplicate", edit.detail))
            continue
        seen_edits.add(key)
        plan.add(edit)
        results.append(Result(d, "planned", edit.kind, edit.detail))

    plans = {path: plan for path, plan in plans.items() if plan.edits}
    return plans, results


def unified_diff(root: Path, plan: FilePlan, new_text: str) -> str:
    try:
        rel = plan.path.relative_to(root)
    except ValueError:
        rel = plan.path
    return "".join(
        difflib.unified_diff(
            plan.original.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )


def run_command(command: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    chunks: list[str] = []
    for line in proc.stdout:
        sys.stdout.write(line)
        chunks.append(line)
    return proc.wait(), "".join(chunks)


def summarize(results: list[Result]) -> None:
    planned: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for r in results:
        target = planned if r.status == "planned" else skipped
        target[r.kind] = target.get(r.kind, 0) + 1
    if planned:
        print("\nPlanned fixes:")
        for kind, count in sorted(planned.items()):
            print(f"  {kind:16} {count}")
    if skipped:
        print("Skipped warnings:")
        for kind, count in sorted(skipped.items()):
            print(f"  {kind:16} {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", nargs="?", type=Path, help="Lean/lake build log to parse")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root (default: cwd)")
    parser.add_argument("--write", action="store_true", help="write planned edits; default is dry-run")
    parser.add_argument(
        "--run",
        nargs=argparse.REMAINDER,
        help="run a build command instead of reading LOG; put this option last, e.g. --run lake build",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    if bool(args.log) == bool(args.run):
        parser.error("provide exactly one of LOG or --run COMMAND ...")

    if args.run:
        command = args.run
        if not command:
            parser.error("--run requires a command")
        print("Running:", shlex.join(command))
        rc, log_text = run_command(command, root)
        if rc != 0:
            print(f"Initial command failed with exit code {rc}; no edits applied.", file=sys.stderr)
            return rc
    else:
        assert args.log is not None
        log_text = args.log.read_text(errors="replace")
        command = None

    diagnostics = parse_diagnostics(log_text)
    plans, results = plan_changes(root, diagnostics)
    rendered: dict[Path, str] = {}

    for path in sorted(plans):
        plan = plans[path]
        try:
            new_text = plan.render()
        except ValueError as ex:
            print(str(ex), file=sys.stderr)
            return 2
        if new_text != plan.original:
            rendered[path] = new_text
            print(unified_diff(root, plan, new_text), end="")

    summarize(results)
    print(f"Files changed: {len(rendered)}")

    if not args.write:
        print("Dry run only. Re-run with --write to apply these edits.")
        return 0

    originals = {path: plans[path].original for path in rendered}
    for path, new_text in rendered.items():
        path.write_text(new_text)

    if command is not None and rendered:
        print("\nVerifying with:", shlex.join(command))
        rc, _ = run_command(command, root)
        if rc != 0:
            print("Verification failed; restoring files changed by this script.", file=sys.stderr)
            for path, old_text in originals.items():
                path.write_text(old_text)
            return rc
        print("Verification succeeded.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

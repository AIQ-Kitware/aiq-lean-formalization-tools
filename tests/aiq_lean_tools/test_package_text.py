"""The package must be importable, on any interpreter.

A docstring in `lean_source` was written with the escape text `\\ud835\\udd5c` --
the UTF-16 surrogate pair for a mathematical double-struck k. Python decodes
that to two lone surrogates, which are a legal `str` and an illegal UTF-8
encoding, so importing the module raised `UnicodeEncodeError: surrogates not
allowed` on 3.13 while compiling clean on 3.12.

Nothing caught it. Reading the file and encoding it succeeds, because the file
holds the six characters of an escape sequence and not the surrogates
themselves; they exist only after Python decodes the literal. So the check has
to look at what the compiler produced, which is what this does.
"""

from __future__ import annotations

import pathlib


def _surrogates(code, seen: set[int] | None = None) -> list[str]:
    seen = set() if seen is None else seen
    if id(code) in seen:
        return []
    seen.add(id(code))
    out: list[str] = []
    for const in code.co_consts:
        if isinstance(const, str):
            if any(0xD800 <= ord(ch) <= 0xDFFF for ch in const):
                out.append(f"{code.co_name}: {const[:70]!r}")
        elif hasattr(const, "co_consts"):
            out += _surrogates(const, seen)
    return out


def test_no_module_carries_a_lone_surrogate():
    root = pathlib.Path(__file__).resolve().parents[2] / "src"
    assert root.is_dir(), root
    findings = []
    for path in sorted(root.rglob("*.py")):
        code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
        findings += [f"{path.relative_to(root)} -> {hit}" for hit in _surrogates(code)]
    assert not findings, "surrogates make a module unimportable:\n" + "\n".join(findings)

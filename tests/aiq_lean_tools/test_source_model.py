"""Resolving, parsing and hashing literature source passages.

Every fixture here is synthetic.  A test must never carry publisher text, and
the private-source tests in particular use invented prose so that running the
suite cannot put a lawful local transcription into the repository.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiq_lean_tools.errors import ValidationError
from aiq_lean_tools.source_model import (
    PRIVATE,
    PUBLIC,
    SourceDocument,
    SourceLibrary,
    SourceLocator,
    content_sha256,
    load_private_config,
    parse_macros,
    parse_markdown,
    parse_tex,
    private_documents,
)

TEX = r"""
\input{preamble}
\begin{document}

% XX-CERT-CLAIM-BEGIN T-1
\subsection*{T-1: the gap theorem}
\sourceanchor{Section 2, first theorem.}
% XX-CERT-SOURCE-BEGIN
Assume the ordered gap $\spec(A)\subset[\beta,\alpha]$ and $\delta>0$.  Then for
every unitary-invariant norm,
\[
 \delta\norm{\sin\Theta}\le\norm{R}.
 \tag{2.1}
\]
The bound is sharp.
% XX-CERT-SOURCE-END
% XX-CERT-CLAIM-END T-1

% XX-CERT-CLAIM-BEGIN T-2
\subsection*{T-2: the standing convention}
% XX-CERT-SOURCE-BEGIN
From here on (3.5) is assumed as well, except where the contrary is stated.
% XX-CERT-SOURCE-END
% XX-CERT-CLAIM-END T-2
\end{document}
"""

PREAMBLE = r"""
\newcommand{\spec}{\operatorname{spec}}
\newcommand{\norm}[1]{\left\lVert #1\right\rVert}
\newcommand{\Hsp}{\mathcal{H}}
"""


@pytest.fixture
def tex_document(tmp_path: Path) -> SourceDocument:
    (tmp_path / "preamble.tex").write_text(PREAMBLE, encoding="utf-8")
    path = tmp_path / "paper.tex"
    path.write_text(TEX, encoding="utf-8")
    return SourceDocument(
        id="Paper1970", path=path, citation="A. Author, A paper, 1970.",
        title="A paper", format="tex", marker_prefix="XX-CERT",
        macro_files=("preamble.tex",),
    )


def test_markers_index_only_the_source_passage(tex_document: SourceDocument):
    assert tex_document.markers() == ["T-1", "T-2"]
    fragment = tex_document.resolve(SourceLocator(marker="T-1"))
    # The heading and the anchor are navigation, not the source passage.
    assert "the gap theorem" not in fragment.text
    assert "sourceanchor" not in fragment.text
    assert "Assume the ordered gap" in fragment.text
    assert "CERT" not in fragment.plain


def test_display_math_and_equation_tags(tex_document: SourceDocument):
    fragment = tex_document.resolve(SourceLocator(marker="T-1"))
    displays = [b for b in fragment.blocks if b.kind == "display"]
    assert len(displays) == 1
    assert displays[0].tag == "2.1"
    assert r"\tag" not in displays[0].text
    assert fragment.equations == ("2.1",)


def test_inline_math_keeps_the_space_before_it(tex_document: SourceDocument):
    fragment = tex_document.resolve(SourceLocator(marker="T-1"))
    first = fragment.blocks[0]
    kinds = [s.kind for s in first.spans]
    assert "math" in kinds
    joined = "".join(s.text for s in first.spans)
    assert "gap \\spec" in joined, joined


def test_hash_ignores_trailing_whitespace_but_not_words(tmp_path: Path):
    a = content_sha256("one two\nthree  ")
    assert a == content_sha256("one two\nthree")
    assert a != content_sha256("one two\nfour")


def test_macros_are_read_for_the_renderer(tex_document: SourceDocument):
    macros = tex_document.macros()
    assert macros["\\spec"] == r"\operatorname{spec}"
    assert macros["\\norm"] == r"\left\lVert #1\right\rVert"


def test_parse_macros_ignores_self_reference():
    # A recursive definition would not terminate in the renderer.
    assert parse_macros(r"\newcommand{\loop}{\loop x}") == {}


def test_line_range_locator_on_markdown(tmp_path: Path):
    path = tmp_path / "transcription.md"
    path.write_text("# Head\n\nfirst\n\nThe bound is $\\delta \\le 1$ here.\n\n$$x^2$$\n", encoding="utf-8")
    doc = SourceDocument(id="T", path=path, format="markdown")
    fragment = doc.resolve(SourceLocator(file="transcription.md", lines=(5, 7)))
    kinds = [b.kind for b in fragment.blocks]
    assert "display" in kinds
    assert any(s.kind == "math" for b in fragment.blocks for s in b.spans)


def test_unresolvable_locator_returns_none(tex_document: SourceDocument):
    assert tex_document.resolve(SourceLocator(marker="nope")) is None
    assert tex_document.resolve(SourceLocator()) is None


def test_locator_round_trip_and_label():
    loc = SourceLocator.parse({
        "document": "P", "marker": "T-1", "section": "8", "result": "Theorem 8.2",
        "page": "31", "equations": ["3.5"],
    })
    assert loc.key == "P:T-1"
    assert "Theorem 8.2" in loc.label() and "Section 8" in loc.label() and "3.5" in loc.label()
    assert SourceLocator.parse(loc.as_json()).key == loc.key


def test_private_fragment_text_is_not_serialized(tmp_path: Path):
    path = tmp_path / "outside" / "local.md"
    path.parent.mkdir()
    path.write_text("Invented local prose that must not travel.\n", encoding="utf-8")
    doc = SourceDocument(id="local", path=path, format="markdown", visibility=PRIVATE)
    fragment = doc.resolve(SourceLocator(file="local.md", lines=(1, 1)))
    public = json.dumps(fragment.as_json())
    assert "must not travel" not in public
    assert "withheld" in public
    assert fragment.sha256 in public          # provenance survives
    assert "blocks" not in json.loads(public)
    local = json.dumps(fragment.as_json(include_private=True))
    assert "must not travel" in local


def test_private_document_may_not_live_in_the_repository(tmp_path: Path):
    inside = tmp_path / "prose" / "secret.md"
    inside.parent.mkdir()
    inside.write_text("x", encoding="utf-8")
    config = tmp_path / "private.json"
    config.write_text(json.dumps({"documents": {"s": {"path": str(inside)}}}), encoding="utf-8")
    with pytest.raises(ValidationError, match="outside the checkout"):
        SourceLibrary.discover(tmp_path, private=load_private_config(config))


def test_private_config_is_optional_and_explicit(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("AIQ_PRIVATE_SOURCES", raising=False)
    assert load_private_config() == {}
    with pytest.raises(ValidationError, match="does not exist"):
        load_private_config(tmp_path / "missing.json")


def _literature(tmp_path: Path) -> Path:
    (tmp_path / "prose").mkdir(exist_ok=True)
    (tmp_path / "prose" / "preamble.tex").write_text(PREAMBLE, encoding="utf-8")
    (tmp_path / "prose" / "paper.tex").write_text(TEX, encoding="utf-8")
    manifest = tmp_path / "prose" / "literature.json"
    manifest.write_text(json.dumps({
        "version": 1,
        "reconstruction": {"note_root": "."},
        "works": {
            "Paper1970": {
                "title": "A paper", "authors": ["A. Author"], "year": 1970, "kind": "paper",
                "target_note": "paper.tex",
                "source_document": {"marker_prefix": "XX-CERT", "format": "tex"},
            }
        },
    }), encoding="utf-8")
    return manifest


def test_library_is_built_from_the_literature_manifest(tmp_path: Path):
    _literature(tmp_path)
    library = SourceLibrary.discover(tmp_path)
    assert "Paper1970" in library.documents
    doc = library.documents["Paper1970"]
    assert doc.visibility == PUBLIC
    fragment = library.resolve({"document": "Paper1970", "marker": "T-2"})
    assert "except where the contrary is stated" in fragment.plain
    # Paths in output are repository-relative, never absolute.
    assert fragment.locator.file == "prose/paper.tex"
    assert library.macros()["\\Hsp"] == r"\mathcal{H}"


def test_library_reads_a_locator_no_manifest_declares(tmp_path: Path):
    _literature(tmp_path)
    (tmp_path / "notes.md").write_text("line one\nline two\n", encoding="utf-8")
    library = SourceLibrary.discover(tmp_path)
    fragment = library.resolve({"file": "notes.md", "lines": [1, 2]})
    assert "line two" in fragment.plain


def test_parse_tex_and_markdown_agree_on_display_math():
    assert [b.kind for b in parse_tex(r"text\n\[x\]")][-1] == "display"
    assert [b.kind for b in parse_markdown("text\n\n$$x$$")][-1] == "display"


def test_private_containment_is_decided_on_the_resolved_path(tmp_path: Path):
    """Containment is a fact about where a path leads, not how it is spelled.

    A relative path, a `..` segment or a symlink all defeat the rule when it is
    applied to the spelling, and the rule is the only thing keeping private
    material out of a checkout.
    """
    inside = tmp_path / "prose"
    inside.mkdir()
    (inside / "secret.md").write_text("local prose\n", encoding="utf-8")

    def config(path):
        return {"documents": {"Local": {"path": str(path), "format": "markdown"}}}

    with pytest.raises(ValidationError):
        private_documents(config(tmp_path / "sub" / ".." / "prose" / "secret.md"), root=tmp_path)

    link = tmp_path.parent / f"{tmp_path.name}-link.md"
    link.symlink_to(inside / "secret.md")
    try:
        with pytest.raises(ValidationError):
            private_documents(config(link), root=tmp_path)
    finally:
        link.unlink()

    outside = tmp_path.parent / f"{tmp_path.name}-elsewhere.md"
    outside.write_text("elsewhere\n", encoding="utf-8")
    assert private_documents(config(outside), root=tmp_path)[0].id == "Local"

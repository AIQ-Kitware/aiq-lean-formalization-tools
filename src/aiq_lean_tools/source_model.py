"""Literature source documents, and the fragments a semantic review points at.

A semantic review is a claim that a Lean declaration says what a paper says.
Until now the paper half of that claim was a hand-written paraphrase stored in
the review itself: a list of strings under ``source_statement``.  Nothing
connected it to the checked-in source document, nothing rendered its
mathematics, and nothing noticed when the source document was edited underneath
the review that accepted it.

This module supplies the missing half.

* A :class:`SourceDocument` is a checked-in file that carries source material --
  a marked-up TeX reconstruction, a Markdown transcription, plain text.
* A :class:`SourceLocator` is a stable coordinate into one: a named marker, or a
  line range, together with the human-facing section/result/equation labels.
  It stores *coordinates*, never a copy of the prose.
* A :class:`SourceFragment` is what a locator resolves to: the text, its
  mathematics parsed into blocks a browser can render, and a hash of exactly
  what was read.

Public reconstruction versus private source
-------------------------------------------
A repository may lawfully hold a distributable reconstruction of a paper while a
transcription or publisher PDF stays local.  Providers therefore carry a
``visibility``.  Only ``public`` documents are reachable by default;
a private provider has to be configured out of tree and asked for explicitly,
and :meth:`SourceFragment.as_json` refuses to serialize private text unless the
caller says so.  That keeps private prose out of checked-in JSON, generated
artifacts, and test fixtures, where it must never appear.

Nothing here is specific to one paper.  Davis--Kahan resolves markers in a TeX
reconstruction; Yu--Wang--Samworth, Acharyya, Helm and Quench resolve line
ranges in Markdown transcriptions; both go through the same model.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from .common import Finding, Path
from .errors import ValidationError

#: A document whose text may be embedded in distributable output.
PUBLIC = "public_reconstruction"
#: A document that must stay on the machine it was configured on.
PRIVATE = "private_source"

#: What a fragment is to the review that cites it.
FRAGMENT_ROLES = {
    "primary": "The printed passage the reviewed result states.",
    "standing_assumption": "A condition imposed earlier in the source that this result inherits.",
    "definition": "A definition established elsewhere that the statement uses.",
    "convention": "A convention that changes how the printed statement is read.",
    "context": "Adjacent source material a reviewer needs in order to judge the passage.",
}


def content_sha256(text: str) -> str:
    """A hash of source text that survives line-ending and trailing-space churn.

    Anything that changes the words, the symbols or the line structure moves it;
    a stray trailing space does not, because a review should not be invalidated
    by an editor's whitespace.
    """
    normal = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n"))
    return hashlib.sha256(normal.strip().encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Locators


@dataclass(frozen=True)
class SourceLocator:
    """Stable coordinates into a source document.

    Two resolution forms are supported because both are already in use: a named
    marker (the ``DK-CERT-CLAIM-BEGIN <id>`` blocks of a TeX reconstruction) and
    a line range (the form ``source_locator`` already carries on census rows
    citing Markdown transcriptions).  The remaining fields are what a reader
    needs in order to find the passage in the printed paper, and are never used
    for resolution.
    """

    document: str = ""
    marker: str = ""
    file: str = ""
    lines: tuple[int, int] | None = None
    section: str = ""
    result: str = ""
    page: str = ""
    equations: tuple[str, ...] = ()
    anchor: str = ""

    @classmethod
    def parse(cls, value: Any, *, document: str = "") -> "SourceLocator":
        if isinstance(value, SourceLocator):
            return value if not document or value.document else cls(**{**value.as_dict(), "document": document})
        if isinstance(value, str):
            # A bare string is a marker when it looks like one, else an anchor.
            text = value.strip()
            if re.fullmatch(r"[A-Za-z0-9._-]+", text):
                return cls(document=document, marker=text)
            return cls(document=document, anchor=text)
        if not isinstance(value, Mapping):
            raise ValidationError("source locator must be an object, a marker string, or a line range")
        lines = value.get("lines")
        pair: tuple[int, int] | None = None
        if isinstance(lines, Sequence) and not isinstance(lines, (str, bytes)) and len(lines) == 2:
            try:
                pair = (int(lines[0]), int(lines[1]))
            except (TypeError, ValueError):
                pair = None
        equations = value.get("equations") or value.get("equation_ids") or ()
        if isinstance(equations, str):
            equations = [equations]
        return cls(
            document=str(value.get("document") or document or ""),
            marker=str(value.get("marker") or ""),
            file=str(value.get("file") or ""),
            lines=pair,
            section=str(value.get("section") or ""),
            result=str(value.get("result") or ""),
            page=str(value.get("page") or ""),
            equations=tuple(str(x) for x in equations),
            anchor=str(value.get("anchor") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "document": self.document, "marker": self.marker, "file": self.file,
            "lines": list(self.lines) if self.lines else None, "section": self.section,
            "result": self.result, "page": self.page, "equations": list(self.equations),
            "anchor": self.anchor,
        }

    def as_json(self) -> dict[str, Any]:
        return {k: v for k, v in self.as_dict().items() if v not in ("", None, [])}

    @property
    def resolvable(self) -> bool:
        return bool(self.marker or (self.file and self.lines))

    @property
    def key(self) -> str:
        """A short stable identity, for anchors and de-duplication."""
        if self.marker:
            return f"{self.document}:{self.marker}" if self.document else self.marker
        if self.file and self.lines:
            return f"{self.file}:{self.lines[0]}-{self.lines[1]}"
        return self.anchor or self.result or self.document

    def label(self) -> str:
        """How a reader is told where this came from."""
        bits = [x for x in (self.result, f"Section {self.section}" if self.section else "", f"p. {self.page}" if self.page else "") if x]
        if self.equations:
            bits.append("eq. " + ", ".join(self.equations))
        if not bits and self.marker:
            bits.append(self.marker)
        if not bits and self.file and self.lines:
            bits.append(f"{self.file}:{self.lines[0]}–{self.lines[1]}")
        return " · ".join(bits)


# ---------------------------------------------------------------------------
# Parsed source content


@dataclass(frozen=True)
class SourceSpan:
    """A run of one kind inside a paragraph: prose, inline math, code, emphasis."""

    kind: str
    text: str

    def as_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "text": self.text}


@dataclass(frozen=True)
class SourceBlock:
    """One paragraph, display equation, heading, or list item."""

    kind: str
    spans: tuple[SourceSpan, ...] = ()
    text: str = ""
    tag: str = ""

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.spans:
            out["spans"] = [s.as_json() for s in self.spans]
        if self.text:
            out["text"] = self.text
        if self.tag:
            out["tag"] = self.tag
        return out


@dataclass(frozen=True)
class SourceFragment:
    """A resolved passage: what it says, where it came from, and its hash."""

    id: str
    document: str
    citation: str
    locator: SourceLocator
    visibility: str
    text: str
    blocks: tuple[SourceBlock, ...]
    sha256: str
    title: str = ""
    role: str = "primary"
    note: str = ""

    @property
    def private(self) -> bool:
        return self.visibility == PRIVATE

    @property
    def plain(self) -> str:
        """The passage as a reader sees it, with mathematics left as TeX.

        This, not the raw file text, is what a quoted excerpt is checked
        against.  A reviewer quotes what the passage *says* -- "except where the
        contrary is stated" -- not the LaTeX that produced it, and the browser
        highlights the quote in the rendered prose, so both sides have to be
        comparing the same string.
        """
        parts: list[str] = []
        for block in self.blocks:
            if block.kind == "display":
                parts.append(block.text)
            else:
                parts.append("".join(span.text for span in block.spans))
        return "\n".join(parts)

    @property
    def equations(self) -> tuple[str, ...]:
        return tuple(b.tag for b in self.blocks if b.tag)

    def as_json(self, *, include_private: bool = False) -> dict[str, Any]:
        """The fragment as the browser sees it.

        A private fragment keeps its identity, locator and hash -- a reviewer
        must be able to see that private provenance *exists* -- and drops its
        text unless the caller has explicitly opted into a local-only render.
        """
        out: dict[str, Any] = {
            "id": self.id,
            "document": self.document,
            "citation": self.citation,
            "locator": self.locator.as_json(),
            "label": self.locator.label(),
            "visibility": self.visibility,
            "sha256": self.sha256,
            "role": self.role,
            "title": self.title,
        }
        if self.note:
            out["note"] = self.note
        if self.equations:
            out["equations"] = list(self.equations)
        if self.private and not include_private:
            out["withheld"] = (
                "Private source text is not serialized. Run the browser with the private "
                "provider enabled to read it locally."
            )
            return out
        out["blocks"] = [b.as_json() for b in self.blocks]
        return out


# ---------------------------------------------------------------------------
# TeX and Markdown parsing

_DISPLAY = re.compile(r"\\\[(.+?)\\\]", re.S)
_ENVS = ("equation", "equation*", "align", "align*", "gather", "gather*", "multline", "multline*")
_ENV_RE = re.compile(r"\\begin\{(" + "|".join(re.escape(e) for e in _ENVS) + r")\}(.+?)\\end\{\1\}", re.S)
_TAG = re.compile(r"\\tag\{([^}]*)\}")
_HEADING = re.compile(r"\\(?:sub)*section\*?\{(.*)\}", re.S)
_ANCHOR = re.compile(r"\\sourceanchor\{(.*?)\}", re.S)
_INLINE = re.compile(r"(?<!\\)\$(.+?)(?<!\\)\$", re.S)
_COMMENT = re.compile(r"(?<!\\)%.*")
_SIMPLE_MACROS = (
    (re.compile(r"\\emph\{(.*?)\}", re.S), "emph"),
    (re.compile(r"\\textbf\{(.*?)\}", re.S), "strong"),
    (re.compile(r"\\code\{(.*?)\}", re.S), "code"),
    (re.compile(r"\\texttt\{(.*?)\}", re.S), "code"),
)
_HREF = re.compile(r"\\href\{[^}]*\}\{(.*?)\}", re.S)
_ITEM = re.compile(r"^\\item(?:\[(.*?)\])?\s*", re.S)


def _clean_prose(text: str) -> str:
    """TeX spacing and dashes, as a reader would see them."""
    text = _HREF.sub(r"\1", text)
    text = text.replace("~", "\u00a0").replace("---", "\u2014").replace("--", "\u2013")
    text = text.replace("``", "\u201c").replace("''", "\u201d")
    text = re.sub(r"\\(?:par|noindent|smallskip|medskip|bigskip|,|;|!)\b", " ", text)
    text = re.sub(r"\\[%&#_$]", lambda m: m.group(0)[1], text)
    # Whitespace is collapsed but boundary spaces are kept: a prose run ends
    # where an inline formula begins, and dropping that space runs the words
    # into the mathematics ("the $\cos$factors").
    return re.sub(r"\s+", " ", text)


def _spans(text: str) -> tuple[SourceSpan, ...]:
    """Split a paragraph into prose, inline math, and marked-up runs."""
    out: list[SourceSpan] = []

    def emit_prose(chunk: str) -> None:
        for pattern, kind in _SIMPLE_MACROS:
            m = pattern.search(chunk)
            if m:
                emit_prose(chunk[: m.start()])
                out.append(SourceSpan(kind, _clean_prose(m.group(1)).strip()))
                emit_prose(chunk[m.end():])
                return
        value = _clean_prose(chunk)
        if value.strip() or (value and out):
            out.append(SourceSpan("text", value))

    pos = 0
    for m in _INLINE.finditer(text):
        emit_prose(text[pos:m.start()])
        out.append(SourceSpan("math", m.group(1).strip()))
        pos = m.end()
    emit_prose(text[pos:])
    while out and out[0].kind == "text" and not out[0].text.strip():
        out.pop(0)
    while out and out[-1].kind == "text" and not out[-1].text.strip():
        out.pop()
    if out and out[0].kind == "text":
        out[0] = SourceSpan("text", out[0].text.lstrip())
    if out and out[-1].kind == "text":
        out[-1] = SourceSpan("text", out[-1].text.rstrip())
    return tuple(out)


def _display_block(body: str) -> SourceBlock:
    tag = ""
    m = _TAG.search(body)
    if m:
        tag = m.group(1).strip()
        body = _TAG.sub("", body)
    return SourceBlock("display", text=body.strip(), tag=tag)


def parse_tex(text: str) -> tuple[SourceBlock, ...]:
    """Blocks of a LaTeX passage: headings, anchors, paragraphs, display math.

    Comment lines are dropped -- in a marked-up reconstruction they are the audit
    markers themselves, which are structure rather than source mathematics.
    """
    lines = [_COMMENT.sub("", line) for line in text.split("\n")]
    body = "\n".join(lines)
    blocks: list[SourceBlock] = []

    # Cut out the displays first so paragraph splitting cannot break one in half.
    pieces: list[tuple[str, str]] = []
    pos = 0
    for m in sorted(
        list(_DISPLAY.finditer(body)) + list(_ENV_RE.finditer(body)), key=lambda m: m.start()
    ):
        if m.start() < pos:
            continue
        pieces.append(("prose", body[pos:m.start()]))
        pieces.append(("display", m.group(1) if m.re is _DISPLAY else m.group(2)))
        pos = m.end()
    pieces.append(("prose", body[pos:]))

    for kind, chunk in pieces:
        if kind == "display":
            blocks.append(_display_block(chunk))
            continue
        for para in re.split(r"\n\s*\n", chunk):
            para = para.strip()
            if not para:
                continue
            heading = _HEADING.fullmatch(para)
            if heading:
                blocks.append(SourceBlock("heading", spans=_spans(heading.group(1))))
                continue
            anchor = _ANCHOR.search(para)
            if anchor:
                blocks.append(SourceBlock("anchor", spans=_spans(anchor.group(1))))
                para = _ANCHOR.sub("", para).strip()
                if not para:
                    continue
            if para.startswith("\\begin{") or para.startswith("\\end{"):
                para = re.sub(r"\\(?:begin|end)\{[^}]*\}", "", para).strip()
                if not para:
                    continue
            item = _ITEM.match(para)
            if item:
                label = item.group(1) or ""
                rest = para[item.end():]
                spans = (SourceSpan("strong", _clean_prose(label)),) if label else ()
                blocks.append(SourceBlock("item", spans=spans + _spans(rest)))
                continue
            blocks.append(SourceBlock("paragraph", spans=_spans(para)))
    return tuple(blocks)


_MD_DISPLAY = re.compile(r"\$\$(.+?)\$\$", re.S)
_MD_HEADING = re.compile(r"^#{1,6}\s+(.*)$")
_MD_STRONG = re.compile(r"\*\*(.+?)\*\*", re.S)
_MD_CODE = re.compile(r"`([^`]+)`")


def _markdown_marks(text: str) -> str:
    """Markdown emphasis, as the TeX the shared span splitter already knows.

    Transcriptions lean on ``**Assumption 2 (…)**`` to carry the structure of a
    numbered statement, and rendering that as literal asterisks loses it.
    """
    text = _MD_STRONG.sub(lambda m: "\\textbf{" + m.group(1) + "}", text)
    return _MD_CODE.sub(lambda m: "\\code{" + m.group(1) + "}", text)


def parse_markdown(text: str) -> tuple[SourceBlock, ...]:
    """Blocks of a Markdown transcription: ``$$``/``\\[`` displays and ``$`` inline."""
    blocks: list[SourceBlock] = []
    pieces: list[tuple[str, str]] = []
    pos = 0
    for m in sorted(
        list(_MD_DISPLAY.finditer(text)) + list(_DISPLAY.finditer(text)), key=lambda m: m.start()
    ):
        if m.start() < pos:
            continue
        pieces.append(("prose", text[pos:m.start()]))
        pieces.append(("display", m.group(1)))
        pos = m.end()
    pieces.append(("prose", text[pos:]))

    for kind, chunk in pieces:
        if kind == "display":
            blocks.append(_display_block(chunk))
            continue
        for para in re.split(r"\n\s*\n", chunk):
            para = para.strip()
            if not para:
                continue
            heading = _MD_HEADING.match(para)
            if heading:
                blocks.append(SourceBlock("heading", spans=_spans(_markdown_marks(heading.group(1)))))
                continue
            if para.startswith(("- ", "* ", "+ ")):
                for line in para.split("\n"):
                    line = re.sub(r"^\s*[-*+]\s+", "", line)
                    if line.strip():
                        blocks.append(SourceBlock("item", spans=_spans(_markdown_marks(line))))
                continue
            blocks.append(SourceBlock("paragraph", spans=_spans(_markdown_marks(para))))
    return tuple(blocks)


PARSERS = {"tex": parse_tex, "markdown": parse_markdown, "text": parse_markdown}


# ---------------------------------------------------------------------------
# Macros


_NEWCOMMAND = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand)\s*\{?\\([A-Za-z]+)\}?\s*(?:\[(\d+)\])?\s*\{", re.M
)


def parse_macros(text: str) -> dict[str, str]:
    """``\\newcommand`` definitions, in the form KaTeX's ``macros`` option takes.

    KaTeX understands ``#1`` arguments directly, so the body is passed through
    unchanged; only balanced-brace extraction is needed, which a regular
    expression cannot do on its own.
    """
    out: dict[str, str] = {}
    for m in _NEWCOMMAND.finditer(text):
        name, start = m.group(1), m.end()
        depth, i = 1, start
        while i < len(text) and depth:
            ch = text[i]
            if ch == "\\":
                i += 2
                continue
            depth += (ch == "{") - (ch == "}")
            i += 1
        body = text[start:i - 1]
        if "\\" + name not in body:  # a self-referential definition would not terminate
            out["\\" + name] = body
    return out


# ---------------------------------------------------------------------------
# Documents and providers


@dataclass
class SourceDocument:
    """One checked-in (or locally configured) file holding source material."""

    id: str
    path: Path
    citation: str = ""
    title: str = ""
    format: str = "tex"
    visibility: str = PUBLIC
    #: ``DK-CERT`` in ``% DK-CERT-CLAIM-BEGIN <id>``.
    marker_prefix: str = ""
    #: Extra files whose ``\newcommand`` definitions this document relies on.
    macro_files: tuple[str, ...] = ()
    note: str = ""

    #: How this document is cited in output: a repository-relative path where
    #: the file is inside the checkout, and the bare name where it is not.
    display_path: str = ""

    _text: str | None = field(default=None, repr=False, compare=False)
    _blocks: dict[str, tuple[int, int]] | None = field(default=None, repr=False, compare=False)

    @property
    def private(self) -> bool:
        return self.visibility == PRIVATE

    # -- reading -----------------------------------------------------------

    @property
    def available(self) -> bool:
        return self.path.is_file()

    def text(self) -> str:
        if self._text is None:
            self._text = self.path.read_text(encoding="utf-8", errors="replace")
        return self._text

    def macros(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for rel in self.macro_files:
            candidate = (self.path.parent / rel) if not Path(rel).is_absolute() else Path(rel)
            if candidate.is_file():
                out.update(parse_macros(candidate.read_text(encoding="utf-8", errors="replace")))
        if self.format == "tex" and self.available:
            out.update(parse_macros(self.text()))
        return out

    # -- marker index ------------------------------------------------------

    def _marker_index(self) -> dict[str, tuple[int, int]]:
        """Marker name -> (first, last) line numbers of its source passage.

        The passage is what lies between the ``SOURCE-BEGIN`` / ``SOURCE-END``
        pair inside a claim block when the document uses that two-level form,
        and the whole claim block otherwise.  Only the source passage is hashed
        and shown: the claim block also carries the heading and the anchor,
        which are navigation rather than source mathematics.
        """
        if self._blocks is not None:
            return self._blocks
        index: dict[str, tuple[int, int]] = {}
        if not self.marker_prefix or not self.available:
            self._blocks = index
            return index
        begin = re.compile(rf"{re.escape(self.marker_prefix)}-CLAIM-BEGIN\s+(\S+)")
        end = re.compile(rf"{re.escape(self.marker_prefix)}-CLAIM-END\s+(\S+)")
        src_begin = f"{self.marker_prefix}-SOURCE-BEGIN"
        src_end = f"{self.marker_prefix}-SOURCE-END"
        lines = self.text().split("\n")
        open_name, open_at, inner = "", 0, 0
        for number, line in enumerate(lines, start=1):
            m = begin.search(line)
            if m:
                open_name, open_at, inner = m.group(1), number, 0
                continue
            if open_name and src_begin in line:
                inner = number
                continue
            if open_name and src_end in line and inner:
                index[open_name] = (inner + 1, number - 1)
                inner = 0
                continue
            m = end.search(line)
            if m and open_name:
                index.setdefault(open_name, (open_at + 1, number - 1))
                open_name = ""
        self._blocks = index
        return index

    def markers(self) -> list[str]:
        return sorted(self._marker_index())

    # -- resolution --------------------------------------------------------

    def slice(self, first: int, last: int) -> str:
        lines = self.text().split("\n")
        return "\n".join(lines[max(0, first - 1):last])

    def resolve(self, locator: SourceLocator, *, id: str = "", role: str = "primary") -> SourceFragment | None:
        if not self.available:
            return None
        if locator.marker:
            span = self._marker_index().get(locator.marker)
            if span is None:
                return None
            first, last = span
        elif locator.lines:
            first, last = locator.lines
            lines = self.text().split("\n")
            if last > len(lines):
                return None
        else:
            return None
        raw = self.slice(first, last)
        parse = PARSERS.get(self.format, parse_markdown)
        resolved = SourceLocator(
            document=self.id, marker=locator.marker, file=locator.file or self.rel_path(),
            lines=(first, last), section=locator.section, result=locator.result,
            page=locator.page, equations=locator.equations, anchor=locator.anchor,
        )
        return SourceFragment(
            id=id or resolved.key,
            document=self.id,
            citation=self.citation or self.title or self.id,
            locator=resolved,
            visibility=self.visibility,
            text=raw,
            blocks=parse(raw),
            sha256=content_sha256(raw),
            title=self.title,
            role=role,
            note=self.note,
        )

    def rel_path(self) -> str:
        """What the browser shows as this document's location.

        A private document deliberately reports its basename only: a review page
        should say which local file was read without publishing where it lives.
        """
        if self.display_path:
            return self.display_path
        return self.path.name if self.private else str(self.path)

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "citation": self.citation, "title": self.title,
            "format": self.format, "visibility": self.visibility,
            "path": self.rel_path(), "available": self.available,
            "markers": len(self._marker_index()) if self.marker_prefix else 0,
        }


@dataclass
class SourceLibrary:
    """Every source document a repository can resolve locators against."""

    root: Path
    documents: dict[str, SourceDocument] = field(default_factory=dict)
    default_document: str = ""

    # -- construction ------------------------------------------------------

    def add(self, document: SourceDocument) -> None:
        if not document.display_path and not document.private:
            try:
                document.display_path = document.path.relative_to(self.root).as_posix()
            except ValueError:
                document.display_path = str(document.path)
        self.documents[document.id] = document
        if not self.default_document:
            self.default_document = document.id

    @classmethod
    def from_literature(
        cls,
        literature: Any,
        *,
        root: Path | None = None,
        private: Mapping[str, Any] | None = None,
    ) -> "SourceLibrary":
        """Build from a literature manifest, which already names each work's note.

        A work becomes a source document when it declares a ``source_document``
        block, or when its ``target_note`` exists on disk.  The manifest is the
        right home for this: it is where the repository already records what a
        work is, where its distributable reconstruction lives, and what it is
        for.  Nothing about the private half is stored there.
        """
        base = Path(root) if root is not None else literature.root
        note_root = literature.path.parent / str(
            (literature.data.get("reconstruction") or {}).get("note_root", ".")
        )
        lib = cls(base)
        for key, work in literature.works.items():
            spec = work.get("source_document")
            spec = dict(spec) if isinstance(spec, Mapping) else {}
            note = spec.get("path") or work.get("target_note")
            if not note:
                continue
            path = Path(note) if Path(note).is_absolute() else (note_root / note)
            if not path.is_file() and not spec:
                continue
            fmt = str(spec.get("format") or ("tex" if str(note).endswith(".tex") else "markdown"))
            macros = spec.get("macro_files") or (("preamble.tex",) if fmt == "tex" else ())
            lib.add(
                SourceDocument(
                    id=str(spec.get("id") or key),
                    path=path,
                    citation=str(spec.get("citation") or _citation(work)),
                    title=str(work.get("title") or key),
                    format=fmt,
                    visibility=PUBLIC,
                    marker_prefix=str(spec.get("marker_prefix") or ""),
                    macro_files=tuple(str(x) for x in macros),
                    note=str(spec.get("note") or ""),
                )
            )
        for doc in private_documents(private, root=base):
            lib.add(doc)
        return lib

    @classmethod
    def discover(
        cls,
        root: str | Path | None = None,
        *,
        private: Mapping[str, Any] | None = None,
    ) -> "SourceLibrary":
        """Every source document the repository's literature manifests declare.

        Discovery is the workspace's, not a second scan: the manifests are
        already found for the literature inventory, and a repository with none
        gets an empty library rather than an error -- the alignment page then
        shows the review's own paraphrase, exactly as it did before.
        """
        from .workspace import FormalizationWorkspace

        ws = FormalizationWorkspace.discover(root)
        lib = cls(ws.root)
        for literature in ws.literature_documents():
            found = cls.from_literature(literature, root=ws.root)
            for doc in found.documents.values():
                lib.add(doc)
        for doc in private_documents(private, root=ws.root):
            lib.add(doc)
        return lib

    # -- use ---------------------------------------------------------------

    def document_for(self, locator: SourceLocator) -> SourceDocument | None:
        if locator.document and locator.document in self.documents:
            return self.documents[locator.document]
        if locator.marker:
            named = [d for d in self.documents.values() if locator.marker in d.markers()]
            if len(named) == 1:
                return named[0]
            if named:
                return named[0]
        if locator.file:
            for doc in self.documents.values():
                if doc.path == (self.root / locator.file) or doc.rel_path() == locator.file:
                    return doc
            candidate = self.root / locator.file
            if candidate.is_file():
                # A locator may cite a transcription no manifest work declares;
                # reading it is still better than showing the reviewer nothing.
                doc = SourceDocument(
                    id=locator.document or locator.file,
                    path=candidate,
                    citation=locator.file,
                    title=locator.file,
                    format="tex" if locator.file.endswith(".tex") else "markdown",
                )
                self.documents.setdefault(doc.id, doc)
                return doc
        if locator.document:
            return None
        return self.documents.get(self.default_document)

    def resolve(self, locator: Any, *, id: str = "", role: str = "primary") -> SourceFragment | None:
        loc = SourceLocator.parse(locator)
        doc = self.document_for(loc)
        if doc is None:
            return None
        if not doc.display_path and not doc.private:
            try:
                doc.display_path = doc.path.relative_to(self.root).as_posix()
            except ValueError:
                doc.display_path = str(doc.path)
        return doc.resolve(loc, id=id, role=role)

    def macros(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for doc in self.documents.values():
            if not doc.private:
                out.update(doc.macros())
        return out

    def as_json(self) -> dict[str, Any]:
        return {
            "documents": [d.as_json() for d in self.documents.values()],
            "macros": self.macros(),
        }

    @property
    def has_private(self) -> bool:
        return any(d.visibility == PRIVATE for d in self.documents.values())


def _citation(work: Mapping[str, Any]) -> str:
    authors = work.get("authors") or []
    names = ", ".join(str(a) for a in authors)
    year = work.get("year")
    title = work.get("title", "")
    return " ".join(x for x in (f"{names}," if names else "", f"{title},", f"{year}." if year else "") if x).strip()


# ---------------------------------------------------------------------------
# Private providers


#: Environment variable naming a JSON file that lists private source documents.
PRIVATE_ENV = "AIQ_PRIVATE_SOURCES"


def load_private_config(path: str | Path | None = None) -> dict[str, Any]:
    """Read the out-of-tree private-source configuration, if there is one.

    Deliberately not discovered inside the repository: a file the repository can
    see is a file the repository can commit.  The configuration is named by
    ``--private-sources`` or by ``AIQ_PRIVATE_SOURCES``, and its absence is the
    normal case rather than an error.
    """
    target = path or os.environ.get(PRIVATE_ENV)
    if not target:
        return {}
    p = Path(str(target)).expanduser()
    if not p.is_file():
        raise ValidationError(f"private-source configuration does not exist: {p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationError("private-source configuration must be a JSON object")
    return data


def private_documents(config: Mapping[str, Any] | None, *, root: Path) -> list[SourceDocument]:
    """Private documents declared by an out-of-tree configuration."""
    if not config:
        return []
    entries = config.get("documents")
    if not isinstance(entries, Mapping):
        return []
    out: list[SourceDocument] = []
    for key, spec in entries.items():
        if not isinstance(spec, Mapping) or not spec.get("path"):
            continue
        path = Path(str(spec["path"])).expanduser()
        if path.is_relative_to(root):
            raise ValidationError(
                f"private source {key} resolves inside the repository ({path}); "
                "private material must live outside the checkout"
            )
        out.append(
            SourceDocument(
                id=str(spec.get("id") or key),
                path=path,
                citation=str(spec.get("citation") or key),
                title=str(spec.get("title") or key),
                format=str(spec.get("format") or ("tex" if str(path).endswith(".tex") else "markdown")),
                visibility=PRIVATE,
                marker_prefix=str(spec.get("marker_prefix") or ""),
                macro_files=tuple(str(x) for x in spec.get("macro_files") or ()),
                note=str(spec.get("note") or "Local provenance source; not distributable."),
            )
        )
    return out

"""Corpus loading: file readers and directory walk (DP-RAG §5, ingest)."""
from __future__ import annotations
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

SUPPORTED_SUFFIXES = frozenset({
    ".md", ".markdown", ".txt", ".yaml", ".yml", ".json",
    ".html", ".htm", ".pdf", ".docx",
})

_URL_RE = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class RawDoc:
    doc_id: str       # slug of the relative path without extension, e.g. 'seed-native-apps'
    title: str
    source: str
    url: str | None
    text: str


class _HtmlTextExtractor(HTMLParser):
    """Drop script/style, keep headings as '# ' lines."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0
        self._heading: int | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._skip == 0:
            self._heading = int(tag[1])
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading is not None:
            text = "".join(self._buf).strip()
            if text:
                level = min(self._heading, 4)
                self._parts.append("#" * level + " " + text)
            self._heading = None
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._heading is not None:
            self._buf.append(data)
        elif data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        return "\n\n".join(p for p in self._parts if p.strip())


def _read_html(path: Path) -> str:
    parser = _HtmlTextExtractor()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    return parser.text()


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _read_docx(path: Path) -> str:
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(str(path))
    parts: list[str] = []

    def _cell_text(cell: object) -> str:
        return " ".join(
            node.text or ""
            for node in getattr(cell, "_tc").iter()  # type: ignore[union-attr]
            if node.tag == qn("w:t")
        ).strip()

    def _para_text(para_elem: object) -> tuple[str, str | None]:
        texts = [
            node.text or ""
            for node in para_elem.iter()  # type: ignore[union-attr]
            if node.tag == qn("w:t")
        ]
        text = "".join(texts).strip()
        style: str | None = None
        ppr = para_elem.find(qn("w:pPr"))  # type: ignore[union-attr]
        if ppr is not None:
            pstyle = ppr.find(qn("w:pStyle"))
            if pstyle is not None:
                style = pstyle.get(qn("w:val"))
        return text, style

    body = doc.element.body
    for child in body:
        if child.tag == qn("w:p"):
            text, style = _para_text(child)
            if not text:
                continue
            if style and style.startswith("Heading"):
                try:
                    level = min(max(int(style[len("Heading"):]), 1), 4)
                except ValueError:
                    level = 1
                parts.append("#" * level + " " + text)
            else:
                parts.append(text)
        elif child.tag == qn("w:tbl"):
            for row in child.findall(qn("w:tr")):
                cells = [_cell_text(cell) for cell in row.findall(qn("w:tc"))]
                parts.append("| " + " | ".join(cells) + " |")
    return "\n\n".join(parts)


def read_file_text(path: Path) -> str:
    """Return plain/markdown text for .md .markdown .txt .yaml .yml .json (utf-8, errors='replace'); .html/.htm via html.parser (drop script/style, keep headings as '# ' lines); .pdf via pypdf (pages joined by blank lines); .docx via python-docx (paragraphs, headings as '# ', tables as '| a | b |' rows). Raises ValueError for other extensions."""
    suffix = path.suffix.lower()
    if suffix in (".md", ".markdown", ".txt", ".yaml", ".yml", ".json"):
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix in (".html", ".htm"):
        return _read_html(path)
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".docx":
        return _read_docx(path)
    raise ValueError(f"unsupported file extension: {path.suffix!r} ({path})")


def _slug(relative: Path) -> str:
    text = "-".join(relative.with_suffix("").parts)
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_corpus(root: Path) -> list[RawDoc]:
    """Walk root recursively, sorted; skip hidden files, '.gitkeep', unsupported extensions and files whose text is under 40 characters; title = first '# ' heading else file stem; url = the first 'https://...' found on a line starting with 'Source:' in the first 12 lines, else None."""
    docs: list[RawDoc] = []
    if not root.exists():
        return docs
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name == ".gitkeep":
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            text = read_file_text(path)
        except ValueError:
            continue
        except Exception as exc:
            print(f"warning: cannot parse {path}: {exc}")
            continue
        if len(text) < 40:
            continue
        title = path.stem
        for line in text.split("\n"):
            if line.startswith("# "):
                title = line[2:].strip() or title
                break
        url: str | None = None
        for line in text.split("\n")[:12]:
            if line.strip().startswith("Source:"):
                match = _URL_RE.search(line)
                if match:
                    url = match.group(0).rstrip(").,;\"'")
                    break
        docs.append(RawDoc(
            doc_id=_slug(rel),
            title=title,
            source=rel.as_posix(),
            url=url,
            text=text,
        ))
    return docs

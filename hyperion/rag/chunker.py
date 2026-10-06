"""Markdown-aware chunking: sections, code/table blocks, prose overlap (DP-RAG §5.3)."""
from __future__ import annotations
import re
from hyperion.rag.ingest import RawDoc
from hyperion.rag.types import Chunk

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_PROSE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")


def _words(text: str) -> list[str]:
    return text.split()


def _split_prose(text: str, max_words: int) -> list[str]:
    pieces = [p.strip() for p in _PROSE_SPLIT_RE.split(text)]
    pieces = [p for p in pieces if p]
    if sum(len(_words(p)) for p in pieces) <= max_words:
        return ["\n".join(pieces)] if pieces else []
    groups: list[str] = []
    current: list[str] = []
    current_words = 0
    for piece in pieces:
        n = len(_words(piece))
        if current and current_words + n > max_words:
            groups.append("\n".join(current))
            current = []
            current_words = 0
        current.append(piece)
        current_words += n
    if current:
        groups.append("\n".join(current))
    return groups


def _split_code(text: str, max_words: int) -> list[str]:
    lines = text.split("\n")
    marker = lines[0].strip() if lines else "```"
    inner = lines[1:]
    if inner and inner[-1].strip().startswith("```"):
        inner = inner[:-1]
    if len(_words(text)) <= max_words:
        return [text]
    groups: list[str] = []
    current: list[str] = []
    current_words = 0
    for line in inner:
        n = len(_words(line)) or 1
        if current and current_words + n > max_words:
            groups.append(f"{marker}\n" + "\n".join(current) + "\n```")
            current = []
            current_words = 0
        current.append(line)
        current_words += n
    if current:
        groups.append(f"{marker}\n" + "\n".join(current) + "\n```")
    return groups


def _split_table(text: str, max_words: int) -> list[str]:
    rows = text.split("\n")
    if len(_words(text)) <= max_words or len(rows) <= 2:
        return [text]
    header = rows[0]
    separator = rows[1]
    head_words = len(_words(header)) + len(_words(separator))
    groups: list[str] = []
    current: list[str] = []
    current_words = head_words
    for row in rows[2:]:
        n = len(_words(row)) or 1
        if current and current_words + n > max_words:
            groups.append("\n".join([header, separator, *current]))
            current = []
            current_words = head_words
        current.append(row)
        current_words += n
    if current:
        groups.append("\n".join([header, separator, *current]))
    return groups


def _build_blocks(lines: list[str]) -> list[tuple[str, str]]:
    """Split section lines into ('code'|'table'|'prose', text) blocks."""
    blocks: list[tuple[str, str]] = []
    i = 0
    total = len(lines)
    while i < total:
        stripped = lines[i].strip()
        if stripped.startswith("```"):
            j = i + 1
            while j < total and not lines[j].strip().startswith("```"):
                j += 1
            j = min(j + 1, total)
            blocks.append(("code", "\n".join(lines[i:j])))
            i = j
        elif stripped.startswith("|"):
            j = i
            while j < total and lines[j].strip().startswith("|"):
                j += 1
            blocks.append(("table", "\n".join(lines[i:j])))
            i = j
        elif stripped == "":
            i += 1
        else:
            j = i
            while j < total:
                s = lines[j].strip()
                if s == "" or s.startswith("```") or s.startswith("|"):
                    break
                j += 1
            blocks.append(("prose", "\n".join(lines[i:j])))
            i = j
    return blocks


def chunk_document(doc: "RawDoc", *, target_words: int = 180, overlap_words: int = 30, max_words: int = 260) -> list[Chunk]:
    # 1. Parse lines into (section, lines); headings match outside fenced code.
    sections: list[tuple[str, list[str]]] = []
    stack: list[tuple[int, str]] = []
    section = ""
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        nonlocal buf
        sections.append((section, buf))
        buf = []

    for line in doc.text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            buf.append(line)
            continue
        match = _HEADING_RE.match(line) if not in_fence else None
        if match:
            level = len(match.group(1))
            heading = match.group(2).strip()
            flush()
            if level == 1:
                stack = []
                section = ""
            else:
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, heading))
                section = " › ".join(text for _, text in stack)
            buf = []
        else:
            buf.append(line)
    flush()

    # 2-3. Blocks per section, splitting oversized ones.
    section_blocks: list[tuple[str, list[tuple[str, str]]]] = []
    for sec, sec_lines in sections:
        blocks: list[tuple[str, str]] = []
        for kind, text in _build_blocks(sec_lines):
            if len(_words(text)) <= max_words:
                blocks.append((kind, text))
            elif kind == "code":
                blocks.extend(("code", part) for part in _split_code(text, max_words))
            elif kind == "table":
                blocks.extend(("table", part) for part in _split_table(text, max_words))
            else:
                blocks.extend(("prose", part) for part in _split_prose(text, max_words))
        section_blocks.append((sec, blocks))

    # 5. Sections with fewer than 25 words merge into the previous stream
    # (kept with their own label when first).
    stream: list[tuple[str, str, str]] = []  # (label, kind, text)
    for sec, blocks in section_blocks:
        total = sum(len(_words(text)) for _, text in blocks)
        label = stream[-1][0] if (total < 25 and stream) else sec
        for kind, text in blocks:
            if text.strip():
                stream.append((label, kind, text))

    # 4. Accumulate blocks into chunks of >= target_words.
    chunks: list[Chunk] = []
    n = 0
    cur_label: str | None = None
    cur_parts: list[str] = []
    cur_kinds: list[str] = []
    cur_words: list[str] = []
    pending_overlap: list[str] = []

    def emit() -> None:
        nonlocal cur_parts, cur_kinds, cur_words, pending_overlap, n
        if not cur_parts:
            return
        n += 1
        body = "\n\n".join(cur_parts)
        if cur_label:
            text = f"{doc.title} › {cur_label}\n{body}"
        else:
            text = f"{doc.title}\n{body}"
        chunks.append(Chunk(
            id=f"{doc.doc_id}#{n:03d}",
            doc_id=doc.doc_id,
            title=doc.title,
            section=cur_label or "",
            text=text,
            source=doc.source,
            url=doc.url,
        ))
        if cur_kinds and cur_kinds[-1] == "prose" and overlap_words > 0:
            pending_overlap = cur_words[-overlap_words:]
        else:
            pending_overlap = []
        cur_parts = []
        cur_kinds = []
        cur_words = []

    for label, kind, text in stream:
        if cur_label is None:
            cur_label = label
        elif label != cur_label:
            emit()
            pending_overlap = []  # no overlap across section boundaries
            cur_label = label
        if not cur_parts and pending_overlap:
            overlap_text = " ".join(pending_overlap)
            cur_parts.append(overlap_text)
            cur_kinds.append("prose")
            cur_words.extend(pending_overlap)
            pending_overlap = []
        cur_parts.append(text)
        cur_kinds.append(kind)
        cur_words.extend(_words(text))
        if len(cur_words) >= target_words:
            emit()
    emit()

    if not chunks and doc.text.strip():
        chunks.append(Chunk(
            id=f"{doc.doc_id}#001",
            doc_id=doc.doc_id,
            title=doc.title,
            section="",
            text=f"{doc.title}\n{doc.text.strip()}",
            source=doc.source,
            url=doc.url,
        ))
    return chunks

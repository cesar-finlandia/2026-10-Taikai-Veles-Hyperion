"""Citation verification for grounded answers (DP-RAG §5.8)."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Sequence
from hyperion.rag.answer import ABSTAIN_PHRASE
from hyperion.rag.types import Hit

_CITE_RE = re.compile(r"\[(\d{1,2})\]")
_NUMBER_RE = re.compile(r"\b\d{2,5}\b")
_BACKTICK_RE = re.compile(r"`([^`]{2,60})`")
_CAMEL_RE = re.compile(r"\b[a-z]+[A-Z][A-Za-z]+\b")
_DOTTED_RE = re.compile(r"\b[A-Za-z][\w]*(?:[./][\w]+)+\b")


@dataclass
class CitationReport:
    cited_ids: list[int]
    invalid_ids: list[int]
    unsupported_tokens: list[str]
    abstained: bool
    ok: bool


def check_citations(answer_text: str, hits: Sequence[Hit], *, question: str = "") -> CitationReport:
    """See §5.8."""
    cited = sorted({int(m) for m in _CITE_RE.findall(answer_text)})
    invalid = [i for i in cited if i < 1 or i > len(hits)]
    abstained = ABSTAIN_PHRASE.lower() in answer_text.lower()

    scrubbed = _CITE_RE.sub(" ", answer_text)
    positioned: list[tuple[int, str]] = []
    for match in _NUMBER_RE.finditer(scrubbed):
        positioned.append((match.start(), match.group(0)))
    for match in _BACKTICK_RE.finditer(scrubbed):
        positioned.append((match.start(), match.group(1).strip()))
    for match in _CAMEL_RE.finditer(scrubbed):
        positioned.append((match.start(), match.group(0)))
    for match in _DOTTED_RE.finditer(scrubbed):
        positioned.append((match.start(), match.group(0)))
    positioned.sort(key=lambda item: item[0])
    tokens: list[str] = []
    seen: set[str] = set()
    for _, token in positioned:
        lowered = token.lower()
        if lowered not in seen:
            seen.add(lowered)
            tokens.append(token)

    haystack = " ".join([*(h.chunk.text for h in hits), question]).lower()
    unsupported = [t for t in tokens if t.lower() not in haystack][:10]
    ok = (not invalid) and bool(cited or abstained) and (not unsupported)
    return CitationReport(
        cited_ids=cited,
        invalid_ids=invalid,
        unsupported_tokens=unsupported,
        abstained=abstained,
        ok=ok,
    )

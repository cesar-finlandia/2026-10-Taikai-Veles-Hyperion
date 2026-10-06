"""Tokenisation, content terms and sentence splitting (DP-RAG §5.1–§5.2)."""
from __future__ import annotations
import re

STOPWORDS: frozenset[str] = frozenset(
    "a an and are as at be but by can could do does for from had has have how i if in "
    "into is it its me my of on or our so than that the their them then there these "
    "they this to us was we were what when where which who whom why will with would "
    "you your please tell show explain give about also just me".split()
)

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_RAW_RE = re.compile(r"[A-Za-z0-9]+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_BULLET_RE = re.compile(r"^[\-\*\#\>\|\s]+")


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        token = token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        token = token[:-1]
    if len(token) > 5 and token.endswith("ing"):
        token = token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        token = token[:-2]
    return token


def tokenize(text: str) -> list[str]:
    """Lower-cased index/query tokens: identifiers split on '.', '-', '_' boundaries AND camelCase (the unsplit lower-cased token is kept as well), light stemming, stopwords and 1-char tokens removed."""
    out: list[str] = []
    cleaned = text.replace(".", " ").replace("-", " ").replace("_", " ").replace("/", " ")
    for raw in _RAW_RE.findall(cleaned):
        lowered = raw.lower()
        parts = _CAMEL_RE.sub(" ", raw).split()
        emitted = [lowered]
        if len(parts) > 1:
            emitted.extend(p.lower() for p in parts)
        for token in emitted:
            token = _stem(token)
            if len(token) <= 1:
                continue
            if token in STOPWORDS:
                continue
            out.append(token)
    return out


def content_terms(text: str) -> list[str]:
    """Unique tokenize() output in first-seen order (used by lexical_support and citation checks)."""
    seen: dict[str, None] = {}
    for token in tokenize(text):
        if token not in seen:
            seen[token] = None
    return list(seen)


def sentences(text: str) -> list[str]:
    """Split on '. ', '? ', '! ' and newlines; strip bullets/markdown markers; drop pieces under 20 chars."""
    out: list[str] = []
    for piece in _SENT_SPLIT_RE.split(text):
        cleaned = _BULLET_RE.sub("", piece).strip()
        if len(cleaned) < 20:
            continue
        out.append(cleaned)
    return out

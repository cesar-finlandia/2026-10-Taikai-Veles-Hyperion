"""RAG value types: chunks, hits and retrieval results."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Chunk:
    id: str                 # '<doc_id>#007'
    doc_id: str
    title: str
    section: str            # 'H2 › H3' or '' for the top of a document
    text: str               # starts with '<title> › <section>\n' then the body
    source: str             # path relative to corpus/, forward slashes
    url: str | None


@dataclass
class Hit:
    chunk: Chunk
    score: float            # fused score
    bm25: float
    dense: float | None     # cosine, None when no embeddings
    rank: int               # 1-based position in the final list


@dataclass
class RetrievalResult:
    hits: list[Hit]
    mode: Literal["hybrid", "bm25", "none"]
    best_dense: float
    best_bm25: float
    support: float          # Retriever.lexical_support(query)
    confident: bool

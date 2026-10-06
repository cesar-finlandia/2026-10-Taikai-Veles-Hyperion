"""Persistent hybrid index: BM25 over chunks plus optional dense vectors (DP-RAG §5)."""
from __future__ import annotations
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from hyperion.rag.bm25 import BM25Index
from hyperion.rag.text import tokenize
from hyperion.rag.types import Chunk

INDEX_VERSION = 1


class IndexMissing(RuntimeError):
    ...


def _normalise(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        return [v / norm for v in vec]
    return [float(v) for v in vec]


class RagIndex:
    chunks: list[Chunk]
    vectors: list[list[float]] | None      # L2-normalised
    embed_model: str | None
    bm25: BM25Index
    meta: dict[str, Any]                   # version, built_at, dim, n_docs

    def __init__(
        self,
        chunks: list[Chunk],
        vectors: list[list[float]] | None,
        embed_model: str | None,
        meta: dict[str, Any],
    ) -> None:
        self.chunks = chunks
        self.vectors = vectors
        self.embed_model = embed_model
        self.bm25 = BM25Index([tokenize(chunk.text) for chunk in chunks])
        self.meta = meta

    @classmethod
    def build(cls, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]] | None, embed_model: str | None) -> "RagIndex":
        chunk_list = list(chunks)
        normed = [_normalise(list(v)) for v in vectors] if vectors is not None else None
        dim = len(normed[0]) if normed else None
        meta: dict[str, Any] = {
            "version": INDEX_VERSION,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "dim": dim,
            "n_docs": len({c.doc_id for c in chunk_list}),
        }
        return cls(chunk_list, normed, embed_model, meta)

    @classmethod
    def load(cls, path: Path) -> "RagIndex":
        """Raises IndexMissing when the file is absent, unreadable or has the wrong version."""
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise IndexMissing(f"cannot load index {path}: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("version") != INDEX_VERSION:
            raise IndexMissing(f"index {path} has the wrong version")
        try:
            chunks = [
                Chunk(
                    id=str(entry["id"]),
                    doc_id=str(entry["doc_id"]),
                    title=str(entry["title"]),
                    section=str(entry.get("section", "")),
                    text=str(entry["text"]),
                    source=str(entry["source"]),
                    url=entry.get("url"),
                )
                for entry in raw["chunks"]
            ]
            vectors = (
                [[float(v) for v in row] for row in raw["vectors"]]
                if raw.get("vectors") is not None
                else None
            )
            embed_model = raw.get("embed_model")
            meta = {
                "version": raw.get("version"),
                "built_at": raw.get("built_at"),
                "dim": raw.get("dim"),
                "n_docs": raw.get("n_docs"),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise IndexMissing(f"index {path} is corrupt: {exc}") from exc
        return cls(chunks, vectors, embed_model, meta)

    def save(self, path: Path) -> None:
        """JSON, vectors rounded to 4 decimals; creates parent dirs."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": INDEX_VERSION,
            "built_at": self.meta.get("built_at"),
            "dim": self.meta.get("dim"),
            "n_docs": self.meta.get("n_docs"),
            "embed_model": self.embed_model,
            "chunks": [
                {
                    "id": c.id,
                    "doc_id": c.doc_id,
                    "title": c.title,
                    "section": c.section,
                    "text": c.text,
                    "source": c.source,
                    "url": c.url,
                }
                for c in self.chunks
            ],
            "vectors": (
                [[round(v, 4) for v in row] for row in self.vectors]
                if self.vectors is not None
                else None
            ),
        }
        out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def dense_top(self, qvec: Sequence[float], k: int) -> list[tuple[int, float]]:
        """Cosine similarity (vectors are normalised; normalise qvec) descending; [] when vectors is None."""
        if self.vectors is None:
            return []
        q = _normalise(list(qvec))
        scored = [(i, sum(a * b for a, b in zip(q, row))) for i, row in enumerate(self.vectors)]
        scored.sort(key=lambda kv: (-kv[1], kv[0]))
        return scored[:k]

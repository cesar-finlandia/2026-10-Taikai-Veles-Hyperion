"""Hybrid BM25+dense retrieval with RRF fusion (DP-RAG §5.4–§5.5)."""
from __future__ import annotations
import logging
import math
from hyperion.config import Settings
from hyperion.context import TurnContext
from hyperion.llm.base import LLMLike, LLMUnavailable
from hyperion.rag.index import IndexMissing, RagIndex
from hyperion.rag.text import content_terms, tokenize
from hyperion.rag.types import Hit, RetrievalResult

_log = logging.getLogger(__name__)


class Retriever:
    def __init__(self, index: RagIndex, llm: LLMLike, settings: Settings) -> None:
        self._index = index
        self._llm = llm
        self._settings = settings

    def lexical_support(self, query: str) -> float:
        """Share (0..1) of the query's distinctive terms that occur anywhere in the corpus. See §5.4."""
        terms = content_terms(query)
        if not terms:
            return 0.0
        n = self._index.bm25.n
        if n == 0:
            return 0.0
        present_weight = 0.0
        total_weight = 0.0
        for term in terms:
            d = self._index.bm25.df.get(term, 0)
            weight = 1.0 if d / n <= 0.25 else 0.4
            total_weight += weight
            if d > 0:
                present_weight += weight
        if total_weight == 0.0:
            return 0.0
        return round(present_weight / total_weight, 3)

    async def retrieve(self, query: str, *, k: int | None = None, turn: TurnContext | None = None) -> RetrievalResult:
        k = k or self._settings.rag_top_k
        q = " ".join(query.split())[:500]
        if not q:
            return RetrievalResult([], "none", 0.0, 0.0, 0.0, False)
        index = self._index
        qt = tokenize(q)
        bm = index.bm25.top(qt, 20)
        bm_scores = dict(bm)

        mode: str = "bm25"
        dense_ranks: list[tuple[int, float]] = []
        qvec: list[float] | None = None
        if index.vectors is not None and getattr(self._llm, "available", True):
            try:
                qvec = list((await self._llm.embed([q], kind="query", turn=turn))[0])
            except LLMUnavailable:
                qvec = None
                dense_ranks = []
                mode = "bm25"
            else:
                dense_ranks = index.dense_top(qvec, 20)
                mode = "hybrid" if dense_ranks else "bm25"

        fused: dict[int, float] = {}
        for rank, (idx, _) in enumerate(bm, start=1):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (60 + rank)
        for rank, (idx, _) in enumerate(dense_ranks, start=1):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (60 + rank)
        qt_set = set(qt)
        for idx in list(fused):
            chunk = index.chunks[idx]
            title_terms = set(tokenize(chunk.title + " " + chunk.section))
            boost = min(4, len(qt_set & title_terms))
            fused[idx] += 0.003 * boost

        ordered = sorted(fused, key=lambda i: (-fused[i], i))
        taken: list[int] = []
        per_doc: dict[str, int] = {}
        for idx in ordered:
            doc_id = index.chunks[idx].doc_id
            if per_doc.get(doc_id, 0) >= 3:
                continue
            taken.append(idx)
            per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
            if len(taken) >= k:
                break

        hits: list[Hit] = []
        for rank, idx in enumerate(taken, start=1):
            dense: float | None = None
            if qvec is not None and index.vectors is not None:
                row = index.vectors[idx]
                norm = math.sqrt(sum(v * v for v in qvec))
                qn = [v / norm for v in qvec] if norm > 0 else list(qvec)
                dense = sum(a * b for a, b in zip(qn, row))
            hits.append(Hit(
                chunk=index.chunks[idx],
                score=fused[idx],
                bm25=bm_scores.get(idx, 0.0),
                dense=dense,
                rank=rank,
            ))

        best_dense = max((h.dense for h in hits if h.dense is not None), default=0.0)
        best_bm25 = max((h.bm25 for h in hits), default=0.0)
        support = self.lexical_support(q)
        if not hits:
            confident = False
        elif mode == "hybrid":
            confident = (
                best_dense >= self._settings.rag_dense_min
                or (support >= self._settings.rag_lexical_min and best_bm25 >= self._settings.rag_bm25_min)
            )
        else:
            confident = (
                support >= max(self._settings.rag_lexical_min, 0.5)
                and best_bm25 >= self._settings.rag_bm25_min
            )
        if turn is not None:
            try:
                turn.trace.add(
                    "retrieve",
                    mode=mode,
                    best_dense=best_dense,
                    best_bm25=best_bm25,
                    support=support,
                    confident=confident,
                    ids=[h.chunk.id for h in hits],
                )
            except Exception:
                pass
        return RetrievalResult(
            hits=hits,
            mode=mode,  # type: ignore[arg-type]
            best_dense=best_dense,
            best_bm25=best_bm25,
            support=support,
            confident=confident,
        )


def load_retriever(settings: Settings, llm: LLMLike) -> Retriever | None:
    """Retriever over settings.index_path; None when the index is missing or corrupt (logs a warning)."""
    try:
        index = RagIndex.load(settings.index_path)
    except (IndexMissing, OSError, ValueError) as exc:
        _log.warning("cannot load RAG index %s: %s", settings.index_path, exc)
        return None
    return Retriever(index, llm, settings)

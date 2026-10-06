"""BM25 ranking over tokenised chunks (DP-RAG §5, BM25Index)."""
from __future__ import annotations
import math
from collections import Counter
from typing import Sequence


class BM25Index:
    n: int
    df: dict[str, int]

    def __init__(self, docs_tokens: Sequence[Sequence[str]], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.n = len(docs_tokens)
        self.df = {}
        self._k1 = k1
        self._b = b
        self._doc_len: list[int] = []
        self._tf: list[Counter[str]] = []
        total = 0
        for toks in docs_tokens:
            toks = list(toks)
            self._doc_len.append(len(toks))
            total += len(toks)
            tf = Counter(toks)
            self._tf.append(tf)
            for term in tf:
                self.df[term] = self.df.get(term, 0) + 1
        self._avgdl = (total / self.n) if self.n else 0.0

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))

    def scores(self, query_tokens: Sequence[str]) -> dict[int, float]:
        """doc index -> BM25 score for docs sharing at least one term."""
        out: dict[int, float] = {}
        if self.n == 0:
            return out
        for i, tf in enumerate(self._tf):
            total = 0.0
            for term in query_tokens:
                freq = tf.get(term, 0)
                if freq == 0:
                    continue
                idf = self._idf(term)
                denom = freq + self._k1 * (1.0 - self._b + self._b * self._doc_len[i] / self._avgdl) if self._avgdl else freq + self._k1
                total += idf * freq * (self._k1 + 1.0) / denom
            if total > 0.0:
                out[i] = total
        return out

    def top(self, query_tokens: Sequence[str], k: int) -> list[tuple[int, float]]:
        """Descending by score then ascending doc index."""
        scored = self.scores(query_tokens)
        ranked = sorted(scored.items(), key=lambda kv: (-kv[1], kv[0]))
        return ranked[:k]

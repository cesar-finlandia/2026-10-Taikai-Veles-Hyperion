"""Pure metric functions behind every scorecard claim (DP-EVAL section 3)."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

from hyperion.rag.answer import ABSTAIN_PHRASE


def safe_div(a: float, b: float) -> float:
    """a / b, 0.0 when b == 0."""
    if b == 0:
        return 0.0
    return a / b


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """(precision, recall, f1) with safe_div; f1 = 0.0 when precision + recall == 0."""
    precision = safe_div(tp, tp + fp)
    recall = safe_div(tp, tp + fn)
    if precision + recall == 0:
        return (precision, recall, 0.0)
    return (precision, recall, safe_div(2 * tp, 2 * tp + fp + fn))


def confusion(
    pairs: Iterable[tuple[str, str]], labels: Sequence[str]
) -> dict[str, dict[str, int]]:
    """conf[true][pred] counts for every (true, pred); every label present as a key at both levels."""
    conf: dict[str, dict[str, int]] = {t: {p: 0 for p in labels} for t in labels}
    for true, pred in pairs:
        if true in conf and pred in conf[true]:
            conf[true][pred] += 1
    return conf


def per_class_prf(
    conf: dict[str, dict[str, int]], labels: Sequence[str]
) -> dict[str, dict[str, float]]:
    """{label: {'precision','recall','f1','support'}}; support = row sum."""
    out: dict[str, dict[str, float]] = {}
    for label in labels:
        row = conf.get(label, {})
        tp = row.get(label, 0)
        fp = sum(conf.get(other, {}).get(label, 0) for other in labels if other != label)
        fn = sum(v for k, v in row.items() if k != label)
        precision, recall, f1 = prf(tp, fp, fn)
        out[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": float(sum(row.values())),
        }
    return out


def macro_f1(per_class: dict[str, dict[str, float]]) -> float:
    """Mean f1 over labels with support > 0."""
    scores = [v["f1"] for v in per_class.values() if v.get("support", 0) > 0]
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def keyword_hit(
    answer: str, keywords_all: Sequence[str], keywords_any: Sequence[str]
) -> bool:
    """Case-insensitive: every keyword of keywords_all occurs AND (keywords_any is empty OR at least one occurs)."""
    low = answer.lower()
    if any(k.lower() not in low for k in keywords_all):
        return False
    if not keywords_any:
        return True
    return any(k.lower() in low for k in keywords_any)


def is_abstention(answer: str) -> bool:
    """True when ABSTAIN_PHRASE occurs in the answer or the answer starts with 'This is not covered' (case-insensitive)."""
    if ABSTAIN_PHRASE in answer:
        return True
    return answer.strip().lower().startswith("this is not covered")


def source_hit(hit_doc_ids: Sequence[str], source_docs: Sequence[str]) -> bool:
    """True when any of source_docs is in hit_doc_ids; False when source_docs is empty."""
    if not source_docs:
        return False
    hit = set(hit_doc_ids)
    return any(s in hit for s in source_docs)


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank: sorted(values)[ceil(p / 100 * n) - 1]; 0.0 for an empty sequence."""
    seq = sorted(values)
    if not seq:
        return 0.0
    rank = math.ceil(p / 100 * len(seq)) - 1
    rank = max(0, min(rank, len(seq) - 1))
    return float(seq[rank])


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval (low, high); (0.0, 0.0) when n == 0. See section 5.2."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def pct(x: float) -> str:
    """f'{round(x * 100)}%'."""
    return f"{round(x * 100)}%"

"""Token-budget helpers for per-user session memory (DP-MEMORY §5.1)."""
from __future__ import annotations

import math
from typing import Sequence


def estimate_tokens(text: str) -> int:
    """ceil(len(text) / 3.5); 0 for empty."""
    if not text:
        return 0
    return math.ceil(len(text) / 3.5)


def fit_to_budget(items: Sequence[str], max_tokens: int) -> list[str]:
    """Keep the NEWEST items (the end of the sequence) whose cumulative estimate fits max_tokens.

    Return them in original order. An item larger than the whole budget is truncated
    to fit (suffix '…') — but only when nothing newer has been kept yet.
    """
    if max_tokens <= 0:
        return []
    kept: list[str] = []
    used = 0
    for item in reversed(list(items)):
        cost = estimate_tokens(item)
        if used + cost <= max_tokens:
            kept.append(item)
            used += cost
        elif not kept:
            width = int(max_tokens * 3.5) - 1
            if width <= 0:
                return []
            kept.append(item[:width] + "…")
            break
        else:
            break
    kept.reverse()
    return kept


def clip(text: str, max_chars: int) -> str:
    """Single-line clip: collapse whitespace, cut to max_chars, add '…' when cut."""
    flat = " ".join(text.split())
    if len(flat) > max_chars:
        return flat[: max_chars - 1] + "…"
    return flat

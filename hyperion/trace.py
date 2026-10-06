"""Per-turn trace: one record per pipeline stage, kept in a ring buffer for /debug/turns."""
from __future__ import annotations
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

_SENSITIVE_KEYS = {"api_key", "authorization", "key"}


@dataclass
class TraceRecord:
    seq: int
    stage: str
    ok: bool
    ms: float
    detail: dict[str, Any]


class TraceRecorder:
    """Collects TraceRecords for one turn."""
    turn_id: str
    user_id: str
    records: list[TraceRecord]

    def __init__(self, turn_id: str, user_id: str) -> None:
        self.turn_id = turn_id
        self.user_id = user_id
        self.records = []

    def add(self, stage: str, *, ok: bool = True, ms: float = 0.0, **detail: Any) -> None:
        """Append a record; string values in detail are truncated to 300 chars; keys named
        'api_key', 'authorization', 'key' are dropped."""
        clean: dict[str, Any] = {}
        for k, v in detail.items():
            if k in _SENSITIVE_KEYS:
                continue
            if isinstance(v, str) and len(v) > 300:
                v = v[:300]
            clean[k] = v
        self.records.append(TraceRecord(seq=len(self.records) + 1, stage=stage, ok=ok, ms=ms, detail=clean))

    @contextmanager
    def span(self, stage: str, **detail: Any) -> Iterator[dict[str, Any]]:
        """Time the with-block; on exit record (ok=False + error=<ExceptionName> if it raised, re-raising).
        The yielded dict may be filled by the caller; its items are merged into detail."""
        extra: dict[str, Any] = {}
        t0 = time.perf_counter()
        try:
            yield extra
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000.0
            merged = {**detail, **extra}
            self.add(stage, ok=False, ms=ms, error=type(e).__name__, **merged)
            raise
        else:
            ms = (time.perf_counter() - t0) * 1000.0
            merged = {**detail, **extra}
            self.add(stage, ok=True, ms=ms, **merged)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "user_id": self.user_id,
            "records": [
                {"seq": r.seq, "stage": r.stage, "ok": r.ok, "ms": r.ms, "detail": r.detail}
                for r in self.records
            ],
        }


class TraceStore:
    """Ring buffer of finished turns."""

    def __init__(self, max_turns: int = 200) -> None:
        self._buf: deque[dict[str, Any]] = deque(maxlen=max_turns)

    def put(self, trace: TraceRecorder, *, user_text: str, summary: str) -> None:
        """Store {turn_id,user_id,user_text[:300],summary[:300],records,ts}."""
        self._buf.append({
            "turn_id": trace.turn_id,
            "user_id": trace.user_id,
            "user_text": user_text[:300],
            "summary": summary[:300],
            "records": [
                {"seq": r.seq, "stage": r.stage, "ok": r.ok, "ms": r.ms, "detail": r.detail}
                for r in trace.records
            ],
            "ts": time.time(),
        })

    def recent(self, user_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Newest first; filter by user_id when given."""
        items = list(reversed(self._buf))
        if user_id is not None:
            items = [t for t in items if t.get("user_id") == user_id]
        return items[:limit]

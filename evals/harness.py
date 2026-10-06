"""Shared evaluation harness: config, tally, turn driver and environment factory (DP-EVAL sections 3 and 5.3)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from hyperion.config import Settings, get_settings
from hyperion.events import TextEvent
from hyperion.llm.base import LLMLike
from hyperion.llm.client import build_llm
from hyperion.llm.fake import FakeLLM
from hyperion.rag.chunker import chunk_document
from hyperion.rag.index import RagIndex
from hyperion.rag.ingest import load_corpus
from hyperion.rag.retriever import Retriever, load_retriever
from hyperion.testing.agent_env import AgentEnv, make_agent_env


@dataclass
class EvalConfig:
    mode: Literal["offline", "live"] = "offline"
    repeats: int = 2
    users: int = 20
    turns: int = 7
    probe_set: Literal["frozen", "tuning"] = "frozen"
    out_dir: Path = Path("evals/results")
    data_dir: Path = Path("evals/data")


def _rec_get(record: dict[str, Any], key: str) -> Any:
    """Trace records store fields under 'detail' (TraceStore.recent); read both shapes."""
    if key in record:
        return record[key]
    detail = record.get("detail")
    if isinstance(detail, dict) and key in detail:
        return detail[key]
    return None


@dataclass
class Tally:
    """Counts every action_emitted trace record seen by any suite."""

    actions_checked: int = 0
    violations: int = 0
    strict: bool = True

    def add(self, trace: dict[str, Any]) -> int:
        """Add one turn trace (a dict from TraceStore.recent); return the violations found in it. See section 5.3."""
        found = 0
        records = trace.get("records", []) if isinstance(trace, dict) else []
        for record in records:
            if not isinstance(record, dict) or record.get("stage") != "action_emitted":
                continue
            self.actions_checked += 1
            required = bool(_rec_get(record, "required"))
            confirmed = bool(_rec_get(record, "confirmed"))
            action = _rec_get(record, "action")
            reason = _rec_get(record, "reason")
            violation = bool(required and not confirmed)
            if not violation and self.strict:
                violation = (
                    action in ("edit_file", "delete_file", "delete_folder")
                    and reason == "none"
                )
            if violation:
                self.violations += 1
                found += 1
        return found


@dataclass
class TurnRecord:
    text: str
    actions: list[dict[str, str]]
    ttft_s: float | None  # seconds to the first TextEvent
    total_s: float
    llm_calls: int
    route: str  # the 'outcome' trace record's route ('' when missing)
    trace: dict[str, Any]


@dataclass
class EvalContext:
    cfg: EvalConfig
    tally: Tally


@dataclass
class SuiteResult:
    name: str
    status: Literal["PASS", "FAIL", "REPORT"]
    summary: str  # the text after 'SUITE <name>: <STATUS> '
    metrics: dict[str, Any] = field(default_factory=dict)
    details: list[dict[str, Any]] = field(default_factory=list)
    hard: bool = False


def make_llm(cfg: EvalConfig) -> LLMLike:
    """offline -> FakeLLM(down=True); live -> build_llm(get_settings())."""
    if cfg.mode == "offline":
        return FakeLLM(down=True)
    return build_llm(get_settings())


@lru_cache(maxsize=1)
def _eval_index() -> RagIndex:
    """BM25 index over the corpus root, so chunk doc_ids ('seed-...') match the golden source_docs."""
    chunks = [chunk for doc in load_corpus(Path("corpus")) for chunk in chunk_document(doc)]
    return RagIndex.build(chunks, None, None)


def make_env(
    cfg: EvalConfig,
    files: dict[str, str] | None = None,
    *,
    llm: LLMLike | None = None,
    hitl_mode: str = "strict",
    **settings_kw: Any,
) -> AgentEnv:
    """offline -> make_agent_env(...) with an offline LLM and the corpus-root BM25 index;
    live -> make_agent_env(..., retriever=load_retriever(settings, llm) or None, base=get_settings(), ...)
    (falls back to the seed index when load_retriever returns None)."""
    llm = llm or make_llm(cfg)
    if cfg.mode == "offline":
        base = Settings(
            api_key="test-key", landing_timeout_s=0.5, landing_interval_s=0.05, **settings_kw
        )
        retriever: Retriever | None = Retriever(_eval_index(), llm, base)
        return make_agent_env(files, llm=llm, retriever=retriever, hitl_mode=hitl_mode, base=base)
    settings = get_settings()
    try:
        live_retriever = load_retriever(settings, llm)
    except Exception:
        live_retriever = None
    return make_agent_env(
        files,
        llm=llm,
        retriever=live_retriever,
        base=settings,
        hitl_mode=hitl_mode,
        **settings_kw,
    )


async def run_turn(ctx: EvalContext, ae: AgentEnv, user_id: str, text: str) -> TurnRecord:
    """Drive ae.agent.handle through ae.env.sim (so actions land in the fake workspace), timing the first text event
    and the end; fetch the turn's trace from ae.traces.recent(user_id, 1); ctx.tally.add(trace). See section 5.3."""
    marks: dict[str, float | None] = {"first": None}
    t0 = time.perf_counter()
    try:
        calls0 = ae.env.llm.stats.calls
    except Exception:
        calls0 = 0

    async def tap():  # type: ignore[no-untyped-def]
        async for ev in ae.agent.handle(user_id, text):
            if isinstance(ev, TextEvent) and marks["first"] is None:
                marks["first"] = time.perf_counter() - t0
            yield ev

    sim = await ae.env.sim.run_events(tap())
    try:
        calls1 = ae.env.llm.stats.calls
    except Exception:
        calls1 = calls0
    recent = ae.traces.recent(user_id, 1)
    trace: dict[str, Any] = recent[0] if recent else {"records": []}
    route = ""
    for record in trace.get("records", []):
        if isinstance(record, dict) and record.get("stage") == "outcome":
            route = str(_rec_get(record, "route") or "")
    ctx.tally.add(trace)
    actions = [dict(a) for a in sim.actions]
    return TurnRecord(
        text=sim.text,
        actions=actions,
        ttft_s=marks["first"],
        total_s=time.perf_counter() - t0,
        llm_calls=max(0, calls1 - calls0),
        route=route,
        trace=trace,
    )


def index_info(ae: AgentEnv) -> dict[str, Any]:
    """{'chunks': n, 'mode': 'hybrid' | 'bm25' | 'builtin'}."""
    retriever = getattr(ae, "retriever", None)
    if retriever is None:
        return {"chunks": 0, "mode": "builtin"}
    try:
        index = retriever._index  # noqa: SLF001 (same package wiring)
        mode = "hybrid" if index.vectors is not None else "bm25"
        return {"chunks": len(index.chunks), "mode": mode}
    except Exception:
        return {"chunks": 0, "mode": "bm25"}

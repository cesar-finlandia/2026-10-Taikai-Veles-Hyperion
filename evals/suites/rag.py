"""RAG suite: retrieval hit rate, answer keywords, citations and abstention (DP-EVAL section 5.4)."""
from __future__ import annotations

from collections import Counter
from typing import Any

from hyperion.context import new_turn
from hyperion.events import TextEvent
from hyperion.rag.citations import check_citations

from evals.harness import EvalContext, SuiteResult, make_env
from evals.loaders import load_golden
from evals.metrics import is_abstention, keyword_hit, safe_div, source_hit


async def run_rag_loop(
    ctx: EvalContext, *, feature_rag: bool = True, user_prefix: str = "rag"
) -> dict[str, Any]:
    """Run the golden questions; return counts (hit5, keyword, citations, no_answer, per-category)."""
    questions = load_golden(ctx.cfg.data_dir)
    answerable = [q for q in questions if q.expect == "answer"]
    unanswerable = [q for q in questions if q.expect == "no_answer"]
    ae = make_env(ctx.cfg, files={}, feature_rag=feature_rag)
    retriever = ae.retriever
    hit5 = keyword = 0
    cit_ok = 0
    cit_m = 0
    no_answer_ok = 0
    cat_total: Counter[str] = Counter()
    cat_hit: Counter[str] = Counter()
    mode = "generated" if ctx.cfg.mode == "live" else "extractive"
    for q in answerable:
        if retriever is None:
            continue
        result = await retriever.retrieve(q.question)
        if source_hit([h.chunk.doc_id for h in result.hits], q.source_docs):
            hit5 += 1
        turn = new_turn(ae.env.settings, f"{user_prefix}-{q.id}", q.question)
        parts: list[str] = []
        async for ev in ae.ask.answer(turn, q.question):
            if isinstance(ev, TextEvent):
                parts.append(ev.text)
        text = "".join(parts)
        kw_ok = keyword_hit(text, q.keywords_all, q.keywords_any)
        if kw_ok:
            keyword += 1
        cat_total[q.category] += 1
        if kw_ok:
            cat_hit[q.category] += 1
        if result.hits:
            cit = check_citations(text, result.hits, question=q.question)
            cit_m += 1
            if cit.ok or cit.abstained:
                cit_ok += 1
    if retriever is not None:
        for q in unanswerable:
            turn = new_turn(ae.env.settings, f"{user_prefix}-{q.id}", q.question)
            parts = []
            async for ev in ae.ask.answer(turn, q.question):
                if isinstance(ev, TextEvent):
                    parts.append(ev.text)
            if is_abstention("".join(parts)):
                no_answer_ok += 1
    return {
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        "hit5": hit5,
        "keyword": keyword,
        "citations_ok": cit_ok,
        "citations_m": cit_m,
        "no_answer_ok": no_answer_ok,
        "cat_total": dict(cat_total),
        "cat_hit": dict(cat_hit),
        "mode": mode,
        "no_index": retriever is None,
    }


async def run(ctx: EvalContext) -> SuiteResult:
    """REPORT-only. Summary: n=50 hit5=k/50 keyword_acc=k/50 citations_ok=k/m no_answer_ok=k/4 mode=extractive|generated."""
    loop = await run_rag_loop(ctx)
    n = loop["n_answerable"]
    if loop["no_index"]:
        return SuiteResult(
            name="rag",
            status="REPORT",
            summary="no index",
            metrics={"n": n, "mode": loop["mode"], "no_index": True},
            details=[],
            hard=False,
        )
    per_category = {
        cat: safe_div(loop["cat_hit"].get(cat, 0), total)
        for cat, total in loop["cat_total"].items()
    }
    summary = (
        f"n={n} hit5={loop['hit5']}/{n} keyword_acc={loop['keyword']}/{n} "
        f"citations_ok={loop['citations_ok']}/{loop['citations_m']} "
        f"no_answer_ok={loop['no_answer_ok']}/{loop['n_unanswerable']} mode={loop['mode']}"
    )
    return SuiteResult(
        name="rag",
        status="REPORT",
        summary=summary,
        metrics={
            "n": n,
            "n_answerable": loop["n_answerable"],
            "n_unanswerable": loop["n_unanswerable"],
            "hit5": loop["hit5"],
            "keyword_acc": loop["keyword"],
            "citations_ok": loop["citations_ok"],
            "citations_m": loop["citations_m"],
            "no_answer_ok": loop["no_answer_ok"],
            "per_category_keyword_acc": per_category,
            "mode": loop["mode"],
        },
        details=[],
        hard=False,
    )

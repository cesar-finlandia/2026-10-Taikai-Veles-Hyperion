"""Headline ablation: pipeline vs free-writing control, RAG/memory/HITL arms (DP-EVAL section 5.4)."""
from __future__ import annotations

from typing import Any

from hyperion.dsl.check import check_profile
from hyperion.issues import errors_only

from evals.freewrite import extract_yaml, freewrite_messages
from evals.harness import EvalContext, SuiteResult, make_env
from evals.loaders import load_ablation
from evals.metrics import safe_div, wilson
from evals.suites.memory import run_dialogues
from evals.suites.hitl import _run_hitl
from evals.suites.actions import run_actions_loop
from evals.suites.rag import run_rag_loop


async def run(ctx: EvalContext) -> SuiteResult:
    """REPORT-only. Offline the free-writing arms are n/a (no model); every rate carries a Wilson interval."""
    requests = load_ablation(ctx.cfg.data_dir)
    n = len(requests) * ctx.cfg.repeats

    pipeline_k = 0
    pipeline_first_k = 0
    for _ in range(ctx.cfg.repeats):
        loop = await run_actions_loop(ctx)
        pipeline_k += loop["final"]
        pipeline_first_k += loop["first"]

    freewrite: int | None = None
    freewrite_rag: int | None = None
    if ctx.cfg.mode == "live":
        ae = make_env(ctx.cfg, files={})
        for _ in range(ctx.cfg.repeats):
            for req in requests:
                try:
                    raw = await ae.env.llm.chat(
                        freewrite_messages(req.text),
                        name="freewrite",
                        temperature=0.3,
                        max_tokens=700,
                    )
                    ok = not errors_only(check_profile(extract_yaml(raw)))
                except Exception:
                    ok = False
                freewrite = (freewrite or 0) + (1 if ok else 0)
                try:
                    context = ""
                    if ae.retriever is not None:
                        result = await ae.retriever.retrieve(req.text)
                        chunks = [h.chunk.text for h in result.hits[:5]]
                        context = "\n\n".join(chunks)[:3000]
                    raw_rag = await ae.env.llm.chat(
                        freewrite_messages(req.text, context),
                        name="freewrite",
                        temperature=0.3,
                        max_tokens=700,
                    )
                    ok_rag = not errors_only(check_profile(extract_yaml(raw_rag)))
                except Exception:
                    ok_rag = False
                freewrite_rag = (freewrite_rag or 0) + (1 if ok_rag else 0)

    rag_on = await run_rag_loop(ctx, feature_rag=True)
    rag_off = await run_rag_loop(ctx, feature_rag=False, user_prefix="rag-off")
    mem_on = await run_dialogues(ctx)
    mem_off = await run_dialogues(ctx, control=True)
    # The detector deliberately disables the gate; its violations are reported
    # in metrics but must not leak into the global gate tally (C-HITL reads 0).
    checked0, violations0 = ctx.tally.actions_checked, ctx.tally.violations
    hitl_off = await _run_hitl(ctx, feature_hitl=False, only_requests=True)
    ctx.tally.actions_checked, ctx.tally.violations = checked0, violations0

    def rate(k: int | None, total: int) -> Any:
        if k is None:
            return None
        return safe_div(k, total)

    pipeline_rate = rate(pipeline_k, n)
    metrics: dict[str, Any] = {
        "pipeline": {
            "k": pipeline_k,
            "n": n,
            "rate": pipeline_rate,
            "wilson": list(wilson(pipeline_k, n)),
        },
        "pipeline_first_write": {
            "k": pipeline_first_k,
            "n": n,
            "rate": rate(pipeline_first_k, n),
            "wilson": list(wilson(pipeline_first_k, n)),
        },
        "freewrite": freewrite,
        "freewrite_n": n,
        "freewrite_rag": freewrite_rag,
        "freewrite_rag_n": n,
        "rag_on": {
            "k": rag_on["keyword"],
            "n": rag_on["n_answerable"],
            "wilson": list(wilson(rag_on["keyword"], rag_on["n_answerable"])),
        },
        "rag_off": {
            "k": rag_off["keyword"],
            "n": rag_off["n_answerable"],
            "wilson": list(wilson(rag_off["keyword"], rag_off["n_answerable"])),
        },
        "memory_on": mem_on[0],
        "memory_on_n": mem_on[1],
        "memory_off": mem_off[0],
        "memory_off_n": mem_off[1],
        "hitl_off_unconfirmed": hitl_off["added_violations"],
    }

    def arm(value: int | None) -> str:
        return "n/a" if value is None else str(value)

    summary = (
        f"pipeline={pipeline_k}/{n} "
        f"freewrite={arm(freewrite)}/{n} "
        f"freewrite_rag={arm(freewrite_rag)}/{n} "
        f"rag_on={rag_on['keyword']}/{rag_on['n_answerable']} "
        f"rag_off={rag_off['keyword']}/{rag_off['n_answerable']} "
        f"memory_on={mem_on[0]}/{mem_on[1]} "
        f"memory_off={mem_off[0]}/{mem_off[1]} "
        f"hitl_off_unconfirmed={hitl_off['added_violations']}"
    )
    return SuiteResult(
        name="ablation",
        status="REPORT",
        summary=summary,
        metrics=metrics,
        details=[],
        hard=False,
    )

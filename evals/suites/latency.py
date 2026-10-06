"""Latency suite: time to first text and to [DONE] across five turn types (DP-EVAL section 5.4)."""
from __future__ import annotations

from typing import Any

from hyperion.dsl.render import render_profile
from hyperion.dsl.slots import extract_slots

from evals.harness import EvalContext, SuiteResult, make_env, run_turn
from evals.metrics import percentile, safe_div


def _turn_types(nginx_profile: str) -> list[tuple[str, str, dict[str, str]]]:
    return [
        ("smalltalk", "hello", {}),
        ("refuse", "What's the weather like in Valencia today?", {}),
        ("ask", "What is a native app?", {}),
        (
            "act_create",
            "Create a deployment YAML for a service using the nginx Docker image",
            {},
        ),
        ("act_confirm", "delete app.yaml", {"app.yaml": nginx_profile}),
    ]


async def run(ctx: EvalContext) -> SuiteResult:
    """Five turn types with 12 samples each on fresh users; REPORT-only."""
    nginx_profile = render_profile(
        extract_slots(
            "Create a deployment YAML for a service using the nginx Docker image",
            "native",
        ).slots
    )
    per_type: dict[str, dict[str, Any]] = {}
    all_ttft: list[float] = []
    all_done: list[float] = []
    llm_calls = 0
    n_turns = 0
    tokens = 0
    for name, text, files in _turn_types(nginx_profile):
        ttfts: list[float] = []
        dones: list[float] = []
        calls = 0
        for i in range(12):
            ae = make_env(ctx.cfg, files=dict(files))
            rec = await run_turn(ctx, ae, f"lat-{name}-{i}", text)
            if rec.ttft_s is not None:
                ttfts.append(rec.ttft_s)
                all_ttft.append(rec.ttft_s)
            dones.append(rec.total_s)
            all_done.append(rec.total_s)
            calls += rec.llm_calls
            llm_calls += rec.llm_calls
            n_turns += 1
            try:
                stats = ae.env.llm.stats
                tokens += int(getattr(stats, "prompt_tokens", 0)) + int(
                    getattr(stats, "completion_tokens", 0)
                )
            except Exception:
                pass
        per_type[name] = {
            "ttft_p50": percentile(ttfts, 50),
            "ttft_p95": percentile(ttfts, 95),
            "done_p50": percentile(dones, 50),
            "done_p95": percentile(dones, 95),
            "llm_calls_per_turn": safe_div(calls, 12),
        }
    overall = {
        "ttft_p50": percentile(all_ttft, 50),
        "ttft_p95": percentile(all_ttft, 95),
        "done_p50": percentile(all_done, 50),
        "done_p95": percentile(all_done, 95),
        "llm_calls_per_turn": safe_div(llm_calls, n_turns),
    }
    model_latency = "included" if ctx.cfg.mode == "live" else "excluded"
    summary = (
        f"ttft_p50={overall['ttft_p50']:.2f}s "
        f"ttft_p95={overall['ttft_p95']:.2f}s "
        f"done_p50={overall['done_p50']:.2f}s "
        f"done_p95={overall['done_p95']:.2f}s "
        f"llm_calls_per_turn={overall['llm_calls_per_turn']:.2f} "
        f"model_latency={model_latency}"
    )
    metrics: dict[str, Any] = {
        "per_type": per_type,
        "overall": overall,
        "llm_calls_per_turn": overall["llm_calls_per_turn"],
        "model_latency": model_latency,
    }
    if ctx.cfg.mode == "live":
        metrics["tokens_per_turn"] = safe_div(tokens, n_turns)
    return SuiteResult(
        name="latency",
        status="REPORT",
        summary=summary,
        metrics=metrics,
        details=[],
        hard=False,
    )

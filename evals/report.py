"""Scorecard: claims with ceilings, JSON + Markdown rendering, README block (DP-EVAL sections 5.6 and 5.9)."""
from __future__ import annotations

import datetime
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hyperion.config import get_settings

from evals.harness import EvalConfig, EvalContext, SuiteResult, index_info, make_env
from evals.loaders import verify_frozen
from evals.metrics import pct, safe_div

HARD_SUITES: tuple[str, ...] = ("memory", "hitl", "actions", "repair", "robustness", "soak")


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    ceiling: str


README_START: str = "<!-- scorecard:start -->"
README_END: str = "<!-- scorecard:end -->"

_CHECKER_NOTE = "Validity is judged by the offline mirror of the IDE validator (hyperion.dsl.check_profile)."


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        sha = (out.stdout or "").strip()
        return sha if sha else "unknown"
    except Exception:
        return "unknown"


def _fmt(x: float) -> str:
    return f"{x:.3f}"


def build_claims(
    results: dict[str, SuiteResult], cfg: EvalConfig
) -> dict[str, Claim]:
    """One Claim per row of section 5.9 whose suite ran; text and ceiling are the literal templates of section 5.9."""
    claims: dict[str, Claim] = {}
    mode = cfg.mode

    rag = results.get("rag")
    if rag is not None:
        m = rag.metrics
        n = int(m.get("n", m.get("n_answerable", 0)))
        k_hit = int(m.get("hit5", 0))
        k_kw = int(m.get("keyword_acc", 0))
        k_cit = int(m.get("citations_ok", 0))
        m_cit = int(m.get("citations_m", 0))
        claims["C-RAG-HIT"] = Claim(
            id="C-RAG-HIT",
            text=f"Top-5 source hit rate {pct(safe_div(k_hit, n))} ({k_hit}/{n}) on the frozen golden set ({mode}).",
            ceiling="Supports: retrieval finds the expected seed document for these 50 questions. Does not establish retrieval quality on documents that are not indexed.",
        )
        claims["C-RAG-KW"] = Claim(
            id="C-RAG-KW",
            text=f"Answer-keyword accuracy {pct(safe_div(k_kw, n))} ({k_kw}/{n}) on the frozen golden set ({mode}).",
            ceiling="Supports: answers contain the expected terms for these questions. Does not establish factual accuracy outside the set.",
        )
        claims["C-RAG-CIT"] = Claim(
            id="C-RAG-CIT",
            text=f"{pct(safe_div(k_cit, m_cit))} of grounded answers had every citation resolve with no unsupported identifier ({k_cit}/{m_cit}).",
            ceiling="Supports: citation numbers and quoted identifiers are checked against the retrieved text. Does not establish that every sentence is supported.",
        )

    guard = results.get("guard")
    if guard is not None:
        m = guard.metrics
        n_guard = int(m.get("n", 0))
        in_row = (m.get("confusion", {}) or {}).get("in_scope", {})
        on_total = sum(in_row.values())
        on_ok = int(in_row.get("in_scope", 0)) + int(in_row.get("smalltalk", 0))
        claims["C-GUARD"] = Claim(
            id="C-GUARD",
            text=(
                f"Guardrail macro-F1 {_fmt(float(m.get('macro_f1', 0.0)))} on {n_guard} frozen prompts; "
                f"over-refusal {_fmt(float(m.get('over_refusal', 0.0)))} ({on_ok}/{on_total}); "
                f"off-topic leak {_fmt(float(m.get('off_topic_leak', 0.0)))}; "
                f"injection leak {_fmt(float(m.get('injection_leak', 0.0)))} ({mode})."
            ),
            ceiling="Supports: behaviour on this labelled set. Does not establish behaviour on adversarial prompts outside it; rules were tuned only on a separate 40-prompt set.",
        )

    memory = results.get("memory")
    if memory is not None:
        m = memory.metrics
        claims["C-MEM"] = Claim(
            id="C-MEM",
            text=f"{m.get('dialogues', 0)}/{m.get('dialogues_n', 0)} scripted multi-turn dialogues passed ({m.get('turns', 0)}/{m.get('turns_n', 0)} turns), including interleaved users.",
            ceiling="Supports: the listed memory behaviours. Does not establish memory over long sessions or after a restart.",
        )

    tally = results.get("__tally__")
    violations = 0
    checked = 0
    if isinstance(tally, dict):
        try:
            violations = int(tally.get("violations", 0))
            checked = int(tally.get("actions_checked", 0))
        except (TypeError, ValueError):
            violations, checked = 0, 0
    hitl = results.get("hitl")
    if hitl is not None:
        claims["C-HITL"] = Claim(
            id="C-HITL",
            text=f"{violations} state-changing actions were emitted without a recorded confirmation across {checked} checked actions in this run.",
            ceiling="Supports: the gate holds for the scenarios and dialogues in this run. Does not establish behaviour of the IDE frontend after the action is emitted.",
        )

    actions = results.get("actions")
    if actions is not None:
        m = actions.metrics
        claims["C-PROFILE"] = Claim(
            id="C-PROFILE",
            text=f"{m.get('valid_first_write', 0)} of generated profiles accepted on first write and {m.get('valid_final', 0)} after repair ({m.get('valid_final_n', 0)} requests).",
            ceiling="Supports: validity under the offline mirror of the IDE validator. Does not establish acceptance by the live validator.",
        )

    ablation = results.get("ablation")
    if ablation is not None:
        m = ablation.metrics
        pipe = m.get("pipeline", {})
        free = m.get("freewrite")
        free_rag = m.get("freewrite_rag")
        arm_n = int(m.get("freewrite_n", 0))
        if isinstance(pipe, dict):
            z = pct(safe_div(int(pipe.get("k", 0)), int(pipe.get("n", arm_n)) or arm_n))
        else:
            z = "n/a"
        x = "n/a" if free is None else pct(safe_div(int(free), arm_n))
        y = "n/a" if free_rag is None else pct(safe_div(int(free_rag), arm_n))
        claims["C-ABLATE"] = Claim(
            id="C-ABLATE",
            text=f"Valid-profile rate: free-writing control {x}, retrieval-assisted free-writing {y}, full pipeline {z} ({arm_n} generations per arm).",
            ceiling="Supports: the pipeline beats free-writing by the stated margin under this checker and model. Does not establish superiority on other models or specifications.",
        )

    repair = results.get("repair")
    if repair is not None:
        m = repair.metrics
        claims["C-REPAIR"] = Claim(
            id="C-REPAIR",
            text=f"{m.get('repaired', 0)}/{m.get('repaired_n', 0)} injected faults repaired within {m.get('max_rounds', 0)} rounds.",
            ceiling="Supports: the eight fault types of the suite. Does not establish repair of arbitrary invalid profiles.",
        )

    latency = results.get("latency")
    if latency is not None:
        m = latency.metrics
        overall = m.get("overall", {})
        model_latency = str(m.get("model_latency", "excluded"))
        claims["C-LAT"] = Claim(
            id="C-LAT",
            text=(
                f"Time to first text p50 {float(overall.get('ttft_p50', 0.0)):.2f}s / p95 {float(overall.get('ttft_p95', 0.0)):.2f}s; "
                f"to [DONE] p50 {float(overall.get('done_p50', 0.0)):.2f}s / p95 {float(overall.get('done_p95', 0.0)):.2f}s "
                f"({mode}; model latency {model_latency})."
            ),
            ceiling="Supports: this machine and this load. Does not establish latency under the organizers' evaluation load.",
        )

    robust = results.get("robustness")
    if robust is not None:
        m = robust.metrics
        claims["C-ROBUST"] = Claim(
            id="C-ROBUST",
            text=f"{m.get('scenarios', 0)}/{m.get('scenarios_n', 0)} robustness scenarios returned a well-formed stream ending in [DONE].",
            ceiling="Supports: the twelve listed hostile or degraded conditions. Does not establish behaviour under network faults between the IDE and the container.",
        )

    soak = results.get("soak")
    if soak is not None:
        m = soak.metrics
        claims["C-SOAK"] = Claim(
            id="C-SOAK",
            text=(
                f"{m.get('users', 0)} concurrent users x {m.get('turns', 0)} turns: "
                f"{m.get('completed', 0)}/{m.get('completed_n', 0)} streams completed, "
                f"{m.get('leaks', 0)} cross-user leaks, p95 turn latency {float(m.get('p95_turn_s', 0.0)):.2f}s."
            ),
            ceiling="Supports: isolation under this concurrency. Does not establish behaviour against the shared GPU under organizer load.",
        )

    return claims


def scorecard_dict(
    results: dict[str, SuiteResult],
    cfg: EvalConfig,
    ctx: EvalContext,
    meta_extra: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the scorecard JSON shape of section 5.6 (suites, seven gates, tally, claims)."""
    frozen_bad = verify_frozen(cfg.data_dir)
    suites: dict[str, Any] = {}
    for name, result in results.items():
        if name.startswith("__"):
            continue
        suites[name] = {
            "status": result.status,
            "summary": result.summary,
            "metrics": result.metrics,
            "details": result.details,
        }
    failed = [name for name in HARD_SUITES if suites.get(name, {}).get("status") != "PASS"]
    if frozen_bad:
        failed.append("frozen_data")
    try:
        settings = get_settings()
        llm_model = settings.llm_model
        embed_model = settings.embed_model
    except Exception:
        llm_model, embed_model = "unknown", "unknown"
    try:
        probe_env = make_env(cfg, files={})
        index = index_info(probe_env)
    except Exception:
        index = {"chunks": 0, "mode": "bm25"}
    meta: dict[str, Any] = {
        "mode": cfg.mode,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "llm_model": llm_model,
        "embed_model": embed_model,
        "index": index,
        "repeats": cfg.repeats,
        "frozen_data_ok": not frozen_bad,
        "checker_note": _CHECKER_NOTE,
    }
    meta.update(meta_extra)
    tally_results = dict(results)
    tally_results["__tally__"] = {
        "violations": ctx.tally.violations,
        "actions_checked": ctx.tally.actions_checked,
    }
    claims = build_claims(tally_results, cfg)
    return {
        "meta": meta,
        "suites": suites,
        "gates": {"passed": 7 - len(failed), "total": 7, "failed": failed},
        "tally": {
            "actions_checked": ctx.tally.actions_checked,
            "violations": ctx.tally.violations,
        },
        "claims": {cid: {"text": c.text, "ceiling": c.ceiling} for cid, c in claims.items()},
    }


def _md_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, bool):
        return str(value)
    if value is None:
        return "n/a"
    if isinstance(value, (dict, list)):
        return f"({type(value).__name__} {len(value)})"
    return str(value)


def render_markdown(card: dict[str, Any]) -> str:
    """Title, meta table, one table per suite (metric, value, n, mode), claims with ceilings, reproduce line."""
    meta = card.get("meta", {})
    mode = meta.get("mode", "offline")
    lines = ["# Hyperion evaluation scorecard", ""]
    lines.append("| field | value |")
    lines.append("|---|---|")
    index = meta.get("index", {})
    for key in ("mode", "created_at", "git_sha", "llm_model", "embed_model", "repeats", "frozen_data_ok"):
        lines.append(f"| {key} | {meta.get(key, 'n/a')} |")
    lines.append(f"| index.chunks | {index.get('chunks', 'n/a')} |")
    lines.append(f"| index.mode | {index.get('mode', 'n/a')} |")
    lines.append(f"| checker_note | {meta.get('checker_note', '')} |")
    lines.append("")
    gates = card.get("gates", {})
    lines.append(f"Hard gates: {gates.get('passed', 0)}/{gates.get('total', 7)} passed.")
    if gates.get("failed"):
        lines.append(f"Failed gates: {', '.join(str(f) for f in gates['failed'])}.")
    lines.append("")
    for name, suite in card.get("suites", {}).items():
        lines.append(f"## {name}: {suite.get('status', '')} {suite.get('summary', '')}")
        lines.append("")
        lines.append("| metric | value | n | mode |")
        lines.append("|---|---|---|---|")
        metrics = suite.get("metrics", {})
        for key, value in metrics.items():
            n = metrics.get(f"{key}_n", "")
            lines.append(f"| {key} | {_md_value(value)} | {n} | {mode} |")
        lines.append("")
    lines.append("## Claims and ceilings")
    lines.append("")
    for cid, claim in card.get("claims", {}).items():
        lines.append(f"- **{cid}**: {claim.get('text', '')}")
        lines.append(f"  - Ceiling: {claim.get('ceiling', '')}")
    lines.append("")
    lines.append(f"Reproduce: `uv run python scripts/run_evals.py --suite all --mode {mode}`.")
    lines.append("")
    return "\n".join(lines)


def write_scorecard(card: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """Write scorecard.json (indent 2, sort_keys) and scorecard.md; return both paths."""
    import json as _json

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "scorecard.json"
    md_path = out / "scorecard.md"
    json_path.write_text(_json.dumps(card, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(card), encoding="utf-8")
    return (json_path, md_path)


_HEADLINE_CLAIMS: tuple[str, ...] = (
    "C-RAG-KW",
    "C-RAG-HIT",
    "C-GUARD",
    "C-MEM",
    "C-HITL",
    "C-PROFILE",
    "C-ABLATE",
)


def readme_block(card: dict[str, Any]) -> str:
    """README_START + a Markdown table of the headline claims + README_END."""
    mode = card.get("meta", {}).get("mode", "offline")
    lines = [README_START, "", "| claim | mode |", "|---|---|"]
    for cid in _HEADLINE_CLAIMS:
        claim = card.get("claims", {}).get(cid)
        if claim is None:
            continue
        text = str(claim.get("text", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {text} | {mode} |")
    lines.extend(["", README_END])
    return "\n".join(lines)


def update_readme(path: Path, block: str) -> bool:
    """Replace the text between the two markers (inclusive) with `block`; False (file untouched) when a marker is missing."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return False
    start = text.find(README_START)
    end = text.find(README_END)
    if start == -1 or end == -1 or end < start:
        return False
    end += len(README_END)
    target.write_text(text[:start] + block + text[end:], encoding="utf-8")
    return True

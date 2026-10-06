"""End-to-end eval suites in offline mode (DP-EVAL WU-EVAL-02 through WU-EVAL-05)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from evals.cli import main
from evals.harness import EvalConfig, EvalContext, Tally
from evals.report import readme_block, update_readme
from evals.suites import (
    ablation,
    actions,
    guard,
    hitl,
    latency,
    memory,
    rag,
    repair,
    robustness,
    soak,
)


def _ctx(**kw) -> EvalContext:
    cfg = EvalConfig(mode="offline", **kw)
    return EvalContext(cfg=cfg, tally=Tally())


async def test_memory_suite_all_pass():
    result = await memory.run(_ctx())
    assert result.status == "PASS"
    assert result.summary == "dialogues=16/16 turns=39/39"


async def test_hitl_suite_gate():
    result = await hitl.run(_ctx())
    assert result.status == "PASS"
    assert (
        result.summary
        == "confirmation_requests=6/6 declines_respected=6/6 confirmed_executions=6/6 unconfirmed_state_changes=0"
    )


async def test_actions_suite_valid_final_all():
    result = await actions.run(_ctx())
    assert result.status == "PASS"
    assert re.match(
        r"^valid_first_write=24/24 valid_final=24/24 unsafe_paths_blocked=12/12 "
        r"image_match=\d+/24 port_match=\d+/15$",
        result.summary,
    )


def test_cli_exit_codes():
    proc = subprocess.run(
        [sys.executable, "scripts/run_evals.py", "--suite", "memory", "--mode", "offline", "--gate"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip().splitlines()[-1] == "SUITE memory: PASS dialogues=16/16 turns=39/39"
    env = dict(os.environ)
    env["API_KEY"] = ""
    env["LLM_BASE_URL"] = "https://example.invalid/v1"
    proc = subprocess.run(
        [sys.executable, "scripts/run_evals.py", "--suite", "memory", "--mode", "live"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 2
    assert "live mode needs API_KEY" in proc.stdout


async def test_guard_suite_reports_metrics_in_range():
    result = await guard.run(_ctx())
    assert result.status == "REPORT"
    assert result.metrics["n"] == 110
    for key in ("accuracy", "macro_f1", "over_refusal", "off_topic_leak", "injection_leak"):
        assert 0 <= result.metrics[key] <= 1, key
    total = sum(sum(row.values()) for row in result.metrics["confusion"].values())
    assert total == 110


async def test_rag_suite_offline_reports():
    result = await rag.run(_ctx())
    assert result.status == "REPORT"
    assert result.metrics["n_answerable"] == 50
    assert 0 <= result.metrics["hit5"] <= 50
    assert result.metrics["mode"] == "extractive"
    assert result.summary.startswith("n=50 hit5=")


async def test_repair_fault_arm_all_repaired():
    result = await repair.run(_ctx())
    assert result.status == "PASS"
    assert result.summary == "repaired=24/24 max_rounds=1"


async def test_ablation_offline_pipeline_arm_and_control_na():
    result = await ablation.run(_ctx(repeats=1))
    assert result.status == "REPORT"
    assert result.metrics["freewrite"] is None
    assert result.metrics["pipeline"]["k"] == result.metrics["pipeline"]["n"]
    assert result.metrics["hitl_off_unconfirmed"] == 6
    assert "freewrite=n/a" in result.summary


async def test_robustness_suite_all_pass():
    result = await robustness.run(_ctx())
    assert result.status == "PASS"
    assert result.summary == "scenarios=12/12"


async def test_latency_suite_shapes():
    result = await latency.run(_ctx())
    assert result.status == "REPORT"
    assert set(result.metrics["per_type"]) == {
        "smalltalk",
        "refuse",
        "ask",
        "act_create",
        "act_confirm",
    }
    for name, stats in result.metrics["per_type"].items():
        assert stats["ttft_p50"] <= stats["done_p50"], name
    assert result.summary.endswith("model_latency=excluded")


async def test_soak_suite_small():
    result = await soak.run(_ctx(users=4, turns=7))
    assert result.status == "PASS"
    assert result.summary.startswith("users=4 turns=7 completed=28/28 leaks=0 files_left=0")


def test_scorecard_written_and_claims_present(tmp_path):
    out = tmp_path / "score"
    rc = main(
        [
            "--suite",
            "all",
            "--mode",
            "offline",
            "--repeats",
            "1",
            "--users",
            "4",
            "--turns",
            "7",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    assert (out / "scorecard.json").exists()
    assert (out / "scorecard.md").exists()
    card = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
    assert card["gates"]["total"] == 7
    assert card["gates"]["passed"] == 7
    for cid in ("C-MEM", "C-HITL", "C-PROFILE", "C-REPAIR", "C-ROBUST", "C-SOAK", "C-GUARD", "C-RAG-HIT"):
        assert cid in card["claims"], cid
    assert "n/a" in card["claims"]["C-ABLATE"]["text"]
    readme = tmp_path / "README.md"
    readme.write_text("# Demo\n\n<!-- scorecard:start -->\nSTALE-CONTENT-XYZ\n<!-- scorecard:end -->\n", encoding="utf-8")
    assert update_readme(readme, readme_block(card)) is True
    updated = readme.read_text(encoding="utf-8")
    assert "STALE-CONTENT-XYZ" not in updated
    assert "<!-- scorecard:start -->" in updated and "<!-- scorecard:end -->" in updated

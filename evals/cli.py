"""Eval command line: suite selection, scorecard writing, gates (DP-EVAL section 5.10)."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Sequence

from hyperion.config import get_settings, reset_settings_cache

from evals.harness import EvalConfig, EvalContext, SuiteResult, Tally
from evals.loaders import verify_frozen
from evals.report import (
    HARD_SUITES,
    readme_block,
    scorecard_dict,
    update_readme,
    write_scorecard,
)
from evals.suites import ablation, actions, guard, hitl, latency, memory, rag, repair, robustness, soak

SUITES: tuple[str, ...] = (
    "memory",
    "hitl",
    "actions",
    "repair",
    "guard",
    "rag",
    "ablation",
    "robustness",
    "latency",
    "soak",
)

_SUITE_RUNNERS = {
    "memory": memory.run,
    "hitl": hitl.run,
    "actions": actions.run,
    "repair": repair.run,
    "guard": guard.run,
    "rag": rag.run,
    "ablation": ablation.run,
    "robustness": robustness.run,
    "latency": latency.run,
    "soak": soak.run,
}


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Hyperion evaluation suites.")
    parser.add_argument("--suite", default="all", help="suite name or 'all'")
    parser.add_argument("--mode", default="offline", choices=("offline", "live"))
    parser.add_argument("--set", default="frozen", choices=("frozen", "tuning"), dest="probe_set")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--users", type=int, default=20)
    parser.add_argument("--turns", type=int, default=7)
    parser.add_argument("--out", default=None, help="scorecard directory")
    parser.add_argument("--update-readme", default=None, dest="update_readme")
    parser.add_argument("--gate", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def _scalar_lines(result: SuiteResult) -> list[str]:
    lines = []
    for key, value in result.metrics.items():
        if isinstance(value, (dict, list)):
            continue
        lines.append(f"  {key}: {value}")
    return lines


async def _run_all(ctx: EvalContext, names: Sequence[str]) -> dict[str, SuiteResult]:
    results: dict[str, SuiteResult] = {}
    for name in names:
        runner = _SUITE_RUNNERS[name]
        try:
            result = await runner(ctx)
        except Exception as exc:  # noqa: BLE001 (suite isolation)
            result = SuiteResult(
                name=name,
                status="FAIL",
                summary=f"error={type(exc).__name__}",
                metrics={},
                details=[{"error": f"{type(exc).__name__}: {exc}"}],
                hard=name in HARD_SUITES,
            )
        results[name] = result
        for line in _scalar_lines(result):
            print(line)
        print(f"SUITE {name}: {result.status} {result.summary}")
    return results


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns the process exit code (see section 5.10)."""
    args = _parse_args(argv)
    reset_settings_cache()
    settings = get_settings()
    api_key = (settings.api_key or "").strip()
    base_url = (settings.llm_base_url or "").strip()
    if args.mode == "live" and not api_key and base_url.lower().startswith("https"):
        print("live mode needs API_KEY (run scripts/llm_smoke.py for the exact error)")
        return 2
    if args.suite != "all" and args.suite not in SUITES:
        print(f"unknown suite: {args.suite}")
        return 2
    names = list(SUITES) if args.suite == "all" else [args.suite]
    cfg = EvalConfig(
        mode=args.mode,
        repeats=max(1, int(args.repeats)),
        users=max(1, int(args.users)),
        turns=max(1, int(args.turns)),
        probe_set=args.probe_set,
        out_dir=Path(args.out) if args.out else Path("evals/results"),
        data_dir=Path("evals/data"),
    )
    ctx = EvalContext(cfg=cfg, tally=Tally())
    results = asyncio.run(_run_all(ctx, names))

    if args.suite == "all" or args.out:
        card = scorecard_dict(results, cfg, ctx, {})
        out_dir = Path(args.out) if args.out else cfg.out_dir
        write_scorecard(card, out_dir)
        print(f"SCORECARD written to {out_dir}")

    if args.update_readme:
        card_for_readme = scorecard_dict(results, cfg, ctx, {})
        block = readme_block(card_for_readme)
        if update_readme(Path(args.update_readme), block):
            print("README updated")
        else:
            print("README markers not found")

    if args.suite == "all":
        failed = [
            name for name in HARD_SUITES if results.get(name, None) is None or results[name].status != "PASS"
        ]
        if verify_frozen(cfg.data_dir):
            failed.append("frozen_data")
        if not failed:
            print("EVAL: PASS (hard gates 7/7)")
        else:
            print(f"EVAL: FAIL (hard gates {7 - len(failed)}/7: {', '.join(failed)})")
        if args.gate and failed:
            return 1
        return 0
    single = results[args.suite]
    if args.gate and single.hard and single.status != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

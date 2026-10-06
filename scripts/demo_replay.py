import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
"""Deterministic replay of the evaluation dialogues (DP-PITCH section 5.7)."""
import argparse
import asyncio
from typing import Sequence

from evals.harness import EvalConfig, EvalContext, Tally, make_env, run_turn
from evals.loaders import Dialogue, load_dialogues
from hyperion.config import load_settings


def select(dialogues: list[Dialogue], only: Sequence[str]) -> list[Dialogue]:
    """The dialogues whose id is in `only` (in the file's order); all of them when `only` is empty; ValueError(f'unknown dialogue: {id}') for an id that does not exist."""
    if not only:
        return list(dialogues)
    known = {d.id for d in dialogues}
    for did in only:
        if did not in known:
            raise ValueError(f"unknown dialogue: {did}")
    wanted = set(only)
    return [d for d in dialogues if d.id in wanted]


async def replay(cfg: EvalConfig, chosen: list[Dialogue], pause_s: float) -> tuple[int, int, int]:
    """(dialogues, turns, violations). For each dialogue: ae = make_env(cfg, dict(d.workspace)); print '=== <id> — <title> ==='; for each turn:
    print 'you [<user>]> <say>'; rec = await run_turn(ctx, ae, turn.user, turn.say); print 'hyperion> ' then rec.text with every line indented by two spaces (first line after the prompt);
    for each action dict a in rec.actions print '  [action] <a["action"]> <a["path"]>' plus ' (<len(a["content"])> chars)' when 'content' in a; then asyncio.sleep(pause_s).
    A blank line after each dialogue. violations = ctx.tally.violations."""
    ctx = EvalContext(cfg=cfg, tally=Tally())
    turns = 0
    for d in chosen:
        ae = make_env(cfg, dict(d.workspace))
        print(f"=== {d.id} — {d.title} ===")
        for turn in d.turns:
            print(f"you [{turn.user}]> {turn.say}")
            rec = await run_turn(ctx, ae, turn.user, turn.say)
            lines = rec.text.splitlines() if rec.text else [""]
            print(f"hyperion> {lines[0]}")
            for extra in lines[1:]:
                print(f"  {extra}")
            for a in rec.actions:
                line = f"  [action] {a.get('action', '?')} {a.get('path', '?')}"
                if "content" in a:
                    try:
                        line += f" ({len(a['content'])} chars)"
                    except TypeError:
                        pass
                print(line)
            turns += 1
            await asyncio.sleep(pause_s)
        print()
    return (len(chosen), turns, ctx.tally.violations)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: `uv run python scripts/demo_replay.py [--only d01,d03] [--mode offline|live] [--pause 0.0]`. First line of main: sys.stdout.reconfigure(encoding='utf-8').
    mode live and not load_settings().api_key -> print 'ERROR: live mode needs API_KEY' and return 2. Unknown dialogue id -> print 'ERROR: unknown dialogue: <id>' and return 2.
    Last line: 'REPLAY OK dialogues=<n> turns=<m> unconfirmed_state_changes=<v>'; return 0 (also when v > 0 — the line shows it; the eval gate is the authority)."""
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Replay evaluation dialogues.")
    parser.add_argument("--only", default="", help="Comma-separated dialogue ids (e.g. d01,d03)")
    parser.add_argument("--mode", default="offline", choices=("offline", "live"))
    parser.add_argument("--pause", type=float, default=0.0, help="Seconds to wait between turns")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.pause < 0:
        parser.error("--pause must be >= 0")
    only = [s for s in args.only.split(",") if s] if args.only else []
    if args.mode == "live" and not load_settings().api_key:
        print("ERROR: live mode needs API_KEY")
        return 2
    try:
        chosen = select(load_dialogues(), only)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    cfg = EvalConfig(mode=args.mode)
    n, m, v = asyncio.run(replay(cfg, chosen, args.pause))
    print(f"REPLAY OK dialogues={n} turns={m} unconfirmed_state_changes={v}")
    return 0


raise SystemExit(main())

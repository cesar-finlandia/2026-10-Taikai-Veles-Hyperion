import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
"""Print only measured pitch numbers from the eval scorecard (DP-PITCH section 5.6)."""
import argparse
import json
from pathlib import Path
from typing import Sequence

CLAIM_ORDER: tuple[str, ...] = ("C-RAG-KW", "C-RAG-HIT", "C-RAG-CIT", "C-GUARD", "C-MEM", "C-HITL",
                                "C-PROFILE", "C-ABLATE", "C-REPAIR", "C-LAT", "C-ROBUST", "C-SOAK")
DEFAULT_CARDS: tuple[str, ...] = ("evals/results/live/scorecard.json", "evals/results/offline/scorecard.json")


def pick_card(explicit: str | None) -> Path | None:
    """Path(explicit) when given (even if missing); else the first existing path of DEFAULT_CARDS; else None."""
    if explicit:
        return Path(explicit)
    for name in DEFAULT_CARDS:
        candidate = Path(name)
        if candidate.is_file():
            return candidate
    return None


def render(card: dict) -> list[str]:
    """Lines: first 'SCORECARD mode=<meta.mode> git=<meta.git_sha> gates=<gates.passed>/<gates.total>'; then for each id of CLAIM_ORDER present in card['claims']:
    '<id>: <text>' and '    ceiling: <ceiling>'; last 'PITCH NUMBERS: <k> claims' with k the number of claims printed."""
    meta = card.get("meta", {}) if isinstance(card, dict) else {}
    gates = card.get("gates", {}) if isinstance(card, dict) else {}
    mode = meta.get("mode", "?") if isinstance(meta, dict) else "?"
    sha = meta.get("git_sha", "?") if isinstance(meta, dict) else "?"
    passed = gates.get("passed", "?") if isinstance(gates, dict) else "?"
    total = gates.get("total", "?") if isinstance(gates, dict) else "?"
    lines = [f"SCORECARD mode={mode} git={sha} gates={passed}/{total}"]
    claims = card.get("claims", {}) if isinstance(card, dict) else {}
    if not isinstance(claims, dict):
        claims = {}
    count = 0
    for cid in CLAIM_ORDER:
        if cid in claims:
            claim = claims[cid] if isinstance(claims[cid], dict) else {}
            lines.append(f"{cid}: {claim.get('text', '?')}")
            lines.append(f"    ceiling: {claim.get('ceiling', '?')}")
            count += 1
    lines.append(f"PITCH NUMBERS: {count} claims")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: `uv run python scripts/pitch_numbers.py [--card PATH]`. Prints render(...) and returns 0.
    No card found or unreadable -> prints 'ERROR: scorecard not found: <path or default list>' and returns 2."""
    parser = argparse.ArgumentParser(description="Print measured pitch numbers from the scorecard.")
    parser.add_argument("--card", default=None, help="Scorecard JSON path")
    args = parser.parse_args(list(argv) if argv is not None else None)
    card_path = pick_card(args.card)
    if card_path is None:
        print(f"ERROR: scorecard not found: {', '.join(DEFAULT_CARDS)}")
        return 2
    try:
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except Exception:
        label = args.card if args.card else ", ".join(DEFAULT_CARDS)
        print(f"ERROR: scorecard not found: {label}")
        return 2
    for line in render(card):
        print(line)
    return 0


raise SystemExit(main())

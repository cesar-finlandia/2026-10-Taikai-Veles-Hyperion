import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
"""Repository hygiene gate CLI: prints the check report, exits 0 on PASS."""
import argparse
from pathlib import Path

from release.hygiene import (
    HygieneContext,
    format_report,
    list_candidate_files,
    read_dotenv_key,
    run_all,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decide whether the working tree may be published.")
    parser.add_argument("--final", action="store_true", help="also run submission-day checks")
    parser.add_argument(
        "--root",
        default=str(pathlib.Path(__file__).resolve().parents[1]),
        help="repository root (defaults to the parent of scripts/)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    ctx = HygieneContext(
        root=root,
        files=tuple(list_candidate_files(root)),
        final=args.final,
        dotenv_key=read_dotenv_key(root),
    )
    text, code = format_report(run_all(ctx))
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

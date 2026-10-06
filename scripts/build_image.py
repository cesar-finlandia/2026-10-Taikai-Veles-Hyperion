import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
"""Build (and optionally push) the release image via release.image.build_commands."""
import argparse
import subprocess

from release.image import DEFAULT_PLATFORM, DEFAULT_TAGS, build_commands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the hyperion release image.")
    parser.add_argument("--user", required=True, help="Docker Hub user name")
    parser.add_argument(
        "--tags",
        default=",".join(DEFAULT_TAGS),
        help="comma-separated tags (default: v2,latest)",
    )
    parser.add_argument("--platform", default=DEFAULT_PLATFORM, help="build platform")
    parser.add_argument("--push", action="store_true", help="push every tag after building")
    parser.add_argument("--dry-run", action="store_true", help="print commands without running them")
    args = parser.parse_args(argv)
    tags = tuple(part for part in args.tags.split(",") if part)
    try:
        commands = build_commands(args.user, tags, push=args.push, platform=args.platform)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    if args.dry_run:
        for command in commands:
            print(" ".join(command))
        return 0
    for command in commands:
        print("$ " + " ".join(command))
        proc = subprocess.run(command, check=False)
        if proc.returncode != 0:
            print("FAILED: " + " ".join(command))
            return proc.returncode
    print("BUILD OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

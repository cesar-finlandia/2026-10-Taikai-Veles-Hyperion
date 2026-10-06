import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
"""Container check CLI: boot the image and verify health, smoke, CORS, user, listing, key."""
import argparse
from pathlib import Path

from release.image import format_image_report, run_image_checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the release image by running it.")
    parser.add_argument("--image", default="hyperion:local", help="image to check")
    parser.add_argument("--port", type=int, default=8000, help="host port to publish the container on")
    parser.add_argument("--with-llm", action="store_true", help="pass the key from .env into the container")
    args = parser.parse_args(argv)
    root = pathlib.Path(__file__).resolve().parents[1]
    checks = run_image_checks(args.image, args.port, args.with_llm, Path(root))
    text, code = format_image_report(checks)
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
"""Probe a live IDE backend and record its response shapes as fixtures (operator step, DP-IDE-PROTOCOL WU-IDE-05)."""
import argparse
import json
import re
from typing import Any

import httpx

LISTING_ENDPOINTS = ("/health", "/files", "/workspace", "/agent/files", "/agent/tree", "/tree")


def _sanitise(path: str) -> str:
    name = path.replace("\\", "/").replace("/", "__")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def _fetch(client: httpx.Client, url: str, params: dict[str, str] | None) -> tuple[int | None, Any]:
    try:
        response = client.get(url, params=params)
    except httpx.TransportError:
        return (None, "unreachable")
    try:
        return (response.status_code, response.json())
    except Exception:
        return (response.status_code, response.text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the IDE backend and save fixtures.")
    parser.add_argument("--base", default="http://localhost:3001/api")
    parser.add_argument("--path", dest="paths", action="append", default=[])
    parser.add_argument("--out-dir", dest="out_dir", default="tests/fixtures/backend")
    parser.add_argument("--out", dest="out_dir", default="tests/fixtures/backend")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = httpx.Client(base_url=None, timeout=httpx.Timeout(10.0, connect=3.0))
    base_ok = False

    for path in args.paths:
        read_url = f"{base}/agent/file"
        validate_url = f"{base}/agent/validation/file"
        read_status, read_body = _fetch(client, read_url, {"path": path})
        validate_status, validate_body = _fetch(client, validate_url, {"path": path})
        if read_status is not None:
            base_ok = True
        if validate_status is not None:
            base_ok = True
        name = _sanitise(path)
        (out_dir / f"{name}.read.json").write_text(
            json.dumps({"status": read_status, "body": read_body}, indent=2), encoding="utf-8"
        )
        (out_dir / f"{name}.validate.json").write_text(
            json.dumps({"status": validate_status, "body": validate_body}, indent=2), encoding="utf-8"
        )
        valid: Any = None
        n_errors = 0
        if isinstance(validate_body, dict):
            valid = validate_body.get("valid")
            errs = validate_body.get("errors") or []
            n_errors = len(errs) if isinstance(errs, list) else 0
        print(f"{path} {read_status} {validate_status} valid={valid} errors={n_errors}")

    endpoints: dict[str, Any] = {}
    for endpoint in LISTING_ENDPOINTS:
        status, _ = _fetch(client, f"{base}{endpoint}", None)
        endpoints[endpoint] = status if status is not None else "unreachable"
        if status is not None:
            base_ok = True
        print(f"{endpoint} {status if status is not None else 'unreachable'}")
    (out_dir / "endpoints.json").write_text(json.dumps(endpoints, indent=2), encoding="utf-8")

    if not base_ok:
        print(f"unreachable: {base}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

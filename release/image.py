"""Image tooling: build commands, container checks, key scan helpers."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Sequence

IMAGE_NAME: str = "hyperion"
LOCAL_TAG: str = "hyperion:local"
DEFAULT_TAGS: tuple[str, ...] = ("v2", "latest")
DEFAULT_PLATFORM: str = "linux/amd64"
CONTAINER_NAME: str = "hyperion-release-check"
FORBIDDEN_COMPONENTS: tuple[str, ...] = (
    "hee",
    ".claude",
    "design_documents",
    "tests",
    "evals",
    ".git",
    "drive",
    "docs",
    "release",
)
FORBIDDEN_BASENAMES: tuple[str, ...] = (".env", ".env.local-notes")

_USER_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.-]{1,29}")
_TAG_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_DOTENV_PATTERN = re.compile(r"^\s*API_KEY\s*=\s*(.*)$")


def build_commands(
    user: str,
    tags: Sequence[str] = DEFAULT_TAGS,
    push: bool = False,
    platform: str = DEFAULT_PLATFORM,
    context: str = ".",
) -> list[list[str]]:
    """Docker commands in execution order: build, tag per tag, push per tag when asked."""
    names = tuple(tags)
    if _USER_PATTERN.fullmatch(user) is None:
        raise ValueError("bad user")
    if len(names) == 0:
        raise ValueError("no tags")
    for tag in names:
        if _TAG_PATTERN.fullmatch(tag) is None:
            raise ValueError("bad tag")
    commands = [["docker", "build", "--platform", platform, "-t", LOCAL_TAG, context]]
    for tag in names:
        commands.append(["docker", "tag", LOCAL_TAG, f"{user}/{IMAGE_NAME}:{tag}"])
    if push:
        for tag in names:
            commands.append(["docker", "push", f"{user}/{IMAGE_NAME}:{tag}"])
    return commands


def parse_dotenv_key(text: str) -> str:
    """Value of the last API_KEY assignment in dotenv text ('' when absent)."""
    value = ""
    found = False
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        match = _DOTENV_PATTERN.match(line)
        if match is None:
            continue
        candidate = match.group(1).strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in ("'", '"'):
            candidate = candidate[1:-1]
        value = candidate
        found = True
    return value if found else ""


def forbidden_in_listing(listing: str) -> list[str]:
    """Sorted offending lines of `find /app -maxdepth 3` output."""
    hits: list[str] = []
    for raw in listing.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("/")
        if any(part in FORBIDDEN_COMPONENTS for part in parts):
            hits.append(line)
            continue
        base = parts[-1]
        if base in FORBIDDEN_BASENAMES or base.startswith(".env"):
            hits.append(line)
    return sorted(hits)


def wait_healthy(
    get_status: Callable[[], int],
    timeout_s: float,
    interval_s: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Wait until get_status() returns 200 or timeout_s seconds pass."""
    start = clock()
    while True:
        try:
            status = get_status()
        except Exception:
            status = 0
        if status == 200:
            return True
        if clock() - start >= timeout_s:
            return False
        sleep(interval_s)


@dataclass(frozen=True)
class ImageCheck:
    name: str
    status: Literal["pass", "fail", "skip"]
    detail: str = ""


def format_image_report(checks: Sequence[ImageCheck]) -> tuple[str, int]:
    """Format image checks the same way as release.hygiene.format_report."""
    lines: list[str] = []
    for check in checks:
        if check.status == "pass":
            lines.append(f"[PASS] {check.name}")
        elif check.status == "fail":
            lines.append(f"[FAIL] {check.name}: {check.detail}" if check.detail else f"[FAIL] {check.name}")
        else:
            lines.append(f"[SKIP] {check.name} ({check.detail})" if check.detail else f"[SKIP] {check.name}")
    failed = [check for check in checks if check.status == "fail"]
    if failed:
        lines.append(f"IMAGE CHECK: FAIL ({len(failed)} failed)")
        return "\n".join(lines), 1
    kept = [check for check in checks if check.status != "skip"]
    skipped = len(checks) - len(kept)
    count = len(kept)
    if skipped:
        lines.append(f"IMAGE CHECK: PASS ({count}/{count}, {skipped} skipped)")
    else:
        lines.append(f"IMAGE CHECK: PASS ({count}/{count})")
    return "\n".join(lines), 0


def _container_get(port: int) -> int:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
        return int(response.status)


def run_image_checks(image: str, port: int, with_llm: bool, root: Path) -> list[ImageCheck]:
    """Start the container, run the six container checks, always stop the container."""
    root = Path(root)
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
    command = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        CONTAINER_NAME,
        "-p",
        f"{port}:8000",
        "-e",
        "LANDING_TIMEOUT_S=2",
        "--add-host=host.docker.internal:host-gateway",
    ]
    if with_llm and (root / ".env").exists():
        command += ["--env-file", str(root / ".env")]
    command.append(image)
    started = subprocess.run(command, capture_output=True, text=True)
    if started.returncode != 0:
        return [ImageCheck("boot_health", "fail", f"docker run failed: {(started.stderr or '')[:200]}")]
    try:
        if not wait_healthy(lambda: _container_get(port), 60, 1.0):
            checks = [ImageCheck("boot_health", "fail", "no 200 from /health within 60 s")]
            for name in ("smoke_chat", "cors_preflight", "nonroot_user", "listing_clean", "key_not_in_image"):
                checks.append(ImageCheck(name, "skip", "container not healthy"))
            return checks
        checks = [ImageCheck("boot_health", "pass")]
        smoke_command = [sys.executable, "scripts/smoke_chat.py", "--base", f"http://127.0.0.1:{port}"]
        if with_llm:
            smoke_command.append("--with-llm")
        try:
            smoke = subprocess.run(
                smoke_command, capture_output=True, text=True, timeout=600, cwd=str(root)
            )
            if smoke.returncode == 0 and "ALL CHECKS PASSED" in (smoke.stdout or ""):
                checks.append(ImageCheck("smoke_chat", "pass"))
            else:
                checks.append(ImageCheck("smoke_chat", "fail", (smoke.stdout or "")[-300:]))
        except Exception as exc:
            checks.append(ImageCheck("smoke_chat", "fail", f"{type(exc).__name__}: {exc}"[-300:]))
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/chat",
                method="OPTIONS",
                headers={
                    "Origin": "http://localhost:5000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                origin = response.headers.get("access-control-allow-origin")
                if int(response.status) == 200 and origin:
                    checks.append(ImageCheck("cors_preflight", "pass"))
                else:
                    checks.append(ImageCheck("cors_preflight", "fail", f"status {response.status}"))
        except Exception as exc:
            checks.append(ImageCheck("cors_preflight", "fail", f"{type(exc).__name__}: {exc}"[:200]))
        try:
            ident = subprocess.run(
                ["docker", "exec", CONTAINER_NAME, "id", "-u"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            uid = (ident.stdout or "").strip()
            if ident.returncode == 0 and uid and uid != "0":
                checks.append(ImageCheck("nonroot_user", "pass"))
            else:
                checks.append(ImageCheck("nonroot_user", "fail", f"uid {uid or 'unknown'}"))
        except Exception as exc:
            checks.append(ImageCheck("nonroot_user", "fail", f"{type(exc).__name__}: {exc}"[:200]))
        try:
            listing = subprocess.run(
                ["docker", "exec", CONTAINER_NAME, "find", "/app", "-maxdepth", "3"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            bad = forbidden_in_listing(listing.stdout or "")
            if bad == []:
                checks.append(ImageCheck("listing_clean", "pass"))
            else:
                checks.append(ImageCheck("listing_clean", "fail", ", ".join(bad[:5])))
        except Exception as exc:
            checks.append(ImageCheck("listing_clean", "fail", f"{type(exc).__name__}: {exc}"[:200]))
        try:
            env_text = (root / ".env").read_text(encoding="utf-8", errors="ignore")
        except OSError:
            env_text = ""
        key = parse_dotenv_key(env_text)
        if len(key) < 8:
            checks.append(ImageCheck("key_not_in_image", "skip", "no key in .env"))
        else:
            try:
                grep = subprocess.run(
                    ["docker", "exec", "-e", "CHECK_KEY", CONTAINER_NAME, "sh", "-c",
                     'grep -rIl -F -- "$CHECK_KEY" /app'],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env={**os.environ, "CHECK_KEY": key},
                )
                found = [line for line in (grep.stdout or "").splitlines() if line.strip()]
                if found:
                    checks.append(
                        ImageCheck("key_not_in_image", "fail", f"found in {len(found)} file(s)")
                    )
                else:
                    history = subprocess.run(
                        ["docker", "history", "--no-trunc", image],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if key in (history.stdout or ""):
                        checks.append(ImageCheck("key_not_in_image", "fail", "found in image history"))
                    else:
                        checks.append(ImageCheck("key_not_in_image", "pass"))
            except Exception as exc:
                checks.append(ImageCheck("key_not_in_image", "fail", f"{type(exc).__name__}: {exc}"[:200]))
        return checks
    finally:
        try:
            subprocess.run(["docker", "stop", CONTAINER_NAME], capture_output=True, timeout=60)
        except Exception:
            pass

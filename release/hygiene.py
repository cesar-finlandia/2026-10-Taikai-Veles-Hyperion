"""Repository hygiene gate: decides whether the working tree may be published."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from evals.loaders import verify_frozen

README_START: str = "<!-- scorecard:start -->"
README_END: str = "<!-- scorecard:end -->"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Literal["pass", "fail", "skip"]
    detail: str = ""


@dataclass(frozen=True)
class HygieneContext:
    root: Path
    files: tuple[str, ...]  # posix paths relative to root of every file that `git add -A` would commit
    final: bool = False  # True = also run the checks that only make sense on submission day
    dotenv_key: str = ""  # value of API_KEY in root/.env ("" when absent); never printed


REQUIRED_FILES: tuple[str, ...] = (
    "README.md",
    "LICENCE",
    "LICENSE",
    "disclosure.md",
    "Dockerfile",
    ".dockerignore",
    ".gitignore",
    "pyproject.toml",
    "uv.lock",
    "docker-compose.yaml",
    ".env.example",
    "main.py",
)
README_HEADINGS: tuple[str, ...] = (
    "## What it does",
    "## Quick start",
    "## Configuration",
    "## How a turn works",
    "## Measured results",
    "## Safety model",
    "## Repository layout",
    "## Tests and evaluations",
    "## What is new compared with the starter",
    "## AI assistance disclosure",
    "## Data and credits",
    "## Licence",
)
FORBIDDEN_EXACT: tuple[str, ...] = (".env", ".env.local-notes")
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "hee/",
    ".claude/",
    "design_documents/",
    ".venv/",
    ".pytest_cache/",
    "corpus/drive/",
)
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "assigned_key",
        r"""(?i)\bapi[_-]?key\b\s*[:=]\s*["']?(?!test-key\b|changeme\b|your[-_ ]|<|\$\{|\s)[A-Za-z0-9_\-\.]{16,}""",
    ),
    ("bearer_token", r"Bearer\s+[A-Za-z0-9_\-\.]{20,}"),
    ("sk_token", r"\bsk-[A-Za-z0-9]{20,}"),
)
BANNED_PATTERNS: tuple[str, ...] = (
    r"(?i)gemini",
    r"(?i)vertex",
    r"(?i)chassis",
    r"\bhee/",
    r"hackathons/",
)
BANNED_EXEMPT: frozenset[str] = frozenset(
    {"release/hygiene.py", "scripts/check_hygiene.py", "tests/unit/test_release_hygiene.py"}
)

_BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".woff", ".woff2"}
)
_FALLBACK_SKIP = frozenset({".git", ".venv", "__pycache__"})
_EVAL_RESULTS_ALLOWED = frozenset({".gitkeep", "scorecard.json", "scorecard.md"})
_BANNED_SCAN_ROOTS = frozenset(
    {"README.md", "disclosure.md", "Dockerfile", "pyproject.toml", "docker-compose.yaml", ".env.example"}
)
_BANNED_SCAN_PREFIXES = ("hyperion/", "scripts/", "evals/", "tests/", "docs/", "release/")
_BANNED_SCAN_SUFFIXES = frozenset({".py", ".md", ".toml", ".yaml", ".yml", ".txt"})
_IGNORE_REQUIRED: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        ".gitignore",
        (".env", ".env.*", "hee/", ".claude/", "design_documents/", "corpus/drive/*", "evals/results/*"),
    ),
    (
        ".dockerignore",
        (".git", ".env", ".env.*", "hee", ".claude", "design_documents", "corpus"),
    ),
)
_SIZE_DEFAULT = 5_000_000
_SIZE_LIMITS = {"data/index.json": 30_000_000, "uv.lock": 5_000_000}
_FINAL_FILES = ("README.md", "disclosure.md", "docs/ACCEPTANCE.md")


def list_candidate_files(root: Path) -> list[str]:
    """Sorted posix paths that `git add -A` would commit.

    Runs `git ls-files --cached --others --exclude-standard` in root. When git
    is missing or exits non-zero, walks root and skips every path with a
    component in {'.git', '.venv', '__pycache__'}.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=60,
        )
    except (OSError, ValueError):
        proc = None
    if proc is not None and proc.returncode == 0:
        return sorted(line for line in proc.stdout.splitlines() if line)
    found: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _FALLBACK_SKIP for part in rel.parts):
            continue
        found.append(rel.as_posix())
    return sorted(found)


def read_dotenv_key(root: Path) -> str:
    """Value of API_KEY in root/.env ('' when the file does not exist)."""
    from release.image import parse_dotenv_key

    try:
        text = (Path(root) / ".env").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    return parse_dotenv_key(text)


def read_text_safe(path: Path) -> str | None:
    """Text of path, or None when it should not be scanned.

    None when the file is missing, larger than 2_000_000 bytes, or has a suffix
    in {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.pdf', '.zip', '.gz',
    '.woff', '.woff2'}; otherwise the content decoded as UTF-8 with
    errors='ignore'.
    """
    try:
        candidate = Path(path)
        if candidate.suffix.lower() in _BINARY_SUFFIXES:
            return None
        if candidate.stat().st_size > 2_000_000:
            return None
        return candidate.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def check_required_files(ctx: HygieneContext) -> CheckResult:
    missing = [name for name in REQUIRED_FILES if name not in set(ctx.files)]
    if missing:
        return CheckResult("required_files", "fail", f"missing: {', '.join(missing)}")
    return CheckResult("required_files", "pass")


def check_licence_copy(ctx: HygieneContext) -> CheckResult:
    try:
        original = (ctx.root / "LICENCE").read_bytes()
        copy = (ctx.root / "LICENSE").read_bytes()
    except OSError:
        return CheckResult("licence_copy", "fail", "LICENSE differs from LICENCE")
    if original != copy:
        return CheckResult("licence_copy", "fail", "LICENSE differs from LICENCE")
    text = original.decode("utf-8", errors="ignore")
    if "Apache License" not in text or "Version 2.0" not in text:
        return CheckResult("licence_copy", "fail", "LICENCE is not Apache-2.0")
    return CheckResult("licence_copy", "pass")


def check_forbidden_tracked(ctx: HygieneContext) -> CheckResult:
    flagged: list[str] = []
    for path in ctx.files:
        if path in FORBIDDEN_EXACT:
            flagged.append(path)
            continue
        if path != "corpus/drive/.gitkeep" and any(
            path.startswith(prefix) for prefix in FORBIDDEN_PREFIXES
        ):
            flagged.append(path)
            continue
        parts = path.split("/")
        if "__pycache__" in parts:
            flagged.append(path)
            continue
        if parts[-1].startswith(".c1pg-"):
            flagged.append(path)
            continue
        if path.startswith("evals/results/") and parts[-1] not in _EVAL_RESULTS_ALLOWED:
            flagged.append(path)
            continue
    if flagged:
        detail = ", ".join(flagged[:10])
        if len(flagged) > 10:
            detail += f"… +{len(flagged) - 10} more"
        return CheckResult("forbidden_tracked", "fail", detail)
    return CheckResult("forbidden_tracked", "pass")


def check_secrets(ctx: HygieneContext) -> CheckResult:
    compiled = [(label, re.compile(pattern)) for label, pattern in SECRET_PATTERNS]
    hits: list[str] = []
    for path in ctx.files:
        if path in ("uv.lock", "data/index.json"):
            continue
        text = read_text_safe(ctx.root / path)
        if text is None:
            continue
        for label, pattern in compiled:
            if pattern.search(text):
                hits.append(f"{label} in {path}")
                break
    if len(ctx.dotenv_key) >= 8:
        for path in ctx.files:
            if path == "uv.lock":
                continue
            text = read_text_safe(ctx.root / path)
            if text is None:
                continue
            if ctx.dotenv_key in text:
                hits.append(f"dotenv_key in {path}")
    if hits:
        return CheckResult("secrets", "fail", ", ".join(hits))
    return CheckResult("secrets", "pass")


def check_ignore_files(ctx: HygieneContext) -> CheckResult:
    for filename, lines in _IGNORE_REQUIRED:
        try:
            content = (ctx.root / filename).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            content = ""
        present = {line.strip() for line in content.splitlines()}
        for line in lines:
            if line not in present:
                return CheckResult("ignore_files", "fail", f"{filename} lacks {line}")
    return CheckResult("ignore_files", "pass")


def check_banned_terms(ctx: HygieneContext) -> CheckResult:
    compiled = [re.compile(pattern) for pattern in BANNED_PATTERNS]
    hits: list[str] = []
    for path in ctx.files:
        if path in BANNED_EXEMPT:
            continue
        if "/" not in path:
            if path not in _BANNED_SCAN_ROOTS:
                continue
        else:
            if not path.startswith(_BANNED_SCAN_PREFIXES):
                continue
            if Path(path).suffix.lower() not in _BANNED_SCAN_SUFFIXES:
                continue
        text = read_text_safe(ctx.root / path)
        if text is None:
            continue
        for pattern in compiled:
            if pattern.search(text):
                hits.append(f"{pattern.pattern} in {path}")
                break
        if len(hits) >= 10:
            break
    if hits:
        return CheckResult("banned_terms", "fail", ", ".join(hits[:10]))
    return CheckResult("banned_terms", "pass")


def check_starter_leftovers(ctx: HygieneContext) -> CheckResult:
    try:
        project = (ctx.root / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        project = ""
    if "langchain" in project.lower():
        return CheckResult("starter_leftovers", "fail", "langchain in pyproject.toml")
    try:
        main = (ctx.root / "main.py").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        main = ""
    if "hyperion.app" not in main:
        return CheckResult("starter_leftovers", "fail", "main.py does not start hyperion.app")
    helper_use = re.compile(r"^\s*(import helpers|from helpers import)", re.M)
    for path in ctx.files:
        if path != "main.py" and not path.startswith("hyperion/") and not path.startswith("scripts/"):
            continue
        if Path(path).suffix.lower() != ".py":
            continue
        text = read_text_safe(ctx.root / path)
        if text is not None and helper_use.search(text):
            return CheckResult("starter_leftovers", "fail", f"helpers imported in {path}")
    return CheckResult("starter_leftovers", "pass")


def check_readme_structure(ctx: HygieneContext) -> CheckResult:
    try:
        text = (ctx.root / "README.md").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return CheckResult("readme_structure", "fail", "README.md missing")
    lines = [line.strip() for line in text.splitlines()]
    for heading in README_HEADINGS:
        if lines.count(heading) != 1:
            return CheckResult("readme_structure", "fail", f"heading not exactly once: {heading}")
    if lines.count(README_START) != 1:
        return CheckResult("readme_structure", "fail", "scorecard start marker not exactly once")
    if lines.count(README_END) != 1:
        return CheckResult("readme_structure", "fail", "scorecard end marker not exactly once")
    if lines.index(README_START) > lines.index(README_END):
        return CheckResult("readme_structure", "fail", "scorecard start marker after end marker")
    if "disclosure.md" not in text:
        return CheckResult("readme_structure", "fail", "README does not mention disclosure.md")
    if "LICENCE" not in text:
        return CheckResult("readme_structure", "fail", "README does not mention LICENCE")
    return CheckResult("readme_structure", "pass")


def check_size_limits(ctx: HygieneContext) -> CheckResult:
    for path in ctx.files:
        try:
            size = (ctx.root / path).stat().st_size
        except OSError:
            continue
        if size > _SIZE_LIMITS.get(path, _SIZE_DEFAULT):
            return CheckResult("size_limits", "fail", f"{path} too large ({size} bytes)")
    return CheckResult("size_limits", "pass")


def check_dockerfile_rules(ctx: HygieneContext) -> CheckResult:
    try:
        text = (ctx.root / "Dockerfile").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return CheckResult("dockerfile_rules", "fail", "Dockerfile missing")
    if "FROM python:3.14" not in text:
        return CheckResult("dockerfile_rules", "fail", "Dockerfile must contain FROM python:3.14")
    user_ok = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("USER "):
            value = stripped[len("USER ") :].strip()
            if value and value != "root":
                user_ok = True
    if not user_ok:
        return CheckResult("dockerfile_rules", "fail", "Dockerfile must set a non-root USER")
    if "HEALTHCHECK" not in text:
        return CheckResult("dockerfile_rules", "fail", "Dockerfile must contain HEALTHCHECK")
    if "EXPOSE 8000" not in text:
        return CheckResult("dockerfile_rules", "fail", "Dockerfile must contain EXPOSE 8000")
    if "COPY hyperion ./hyperion" not in text:
        return CheckResult("dockerfile_rules", "fail", "Dockerfile must contain COPY hyperion ./hyperion")
    if "COPY data ./data" not in text:
        return CheckResult("dockerfile_rules", "fail", "Dockerfile must contain COPY data ./data")
    if re.search(r"(?m)^\s*COPY\s+\.\s", text):
        return CheckResult("dockerfile_rules", "fail", "Dockerfile must not COPY the build context root")
    if re.search(r"(?mi)^\s*(ENV|ARG)\s+API_KEY", text):
        return CheckResult("dockerfile_rules", "fail", "Dockerfile must not set API_KEY")
    if ".env" in text:
        return CheckResult("dockerfile_rules", "fail", "Dockerfile must not mention .env")
    if "design_documents" in text:
        return CheckResult("dockerfile_rules", "fail", "Dockerfile must not mention design_documents")
    return CheckResult("dockerfile_rules", "pass")


def check_eval_data_frozen(ctx: HygieneContext) -> CheckResult:
    bad = verify_frozen(ctx.root / "evals" / "data")
    if bad:
        return CheckResult("eval_data_frozen", "fail", f"changed or missing: {', '.join(bad)}")
    return CheckResult("eval_data_frozen", "pass")


def check_final_placeholders(ctx: HygieneContext) -> CheckResult:
    for name in _FINAL_FILES:
        try:
            text = (ctx.root / name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "{{OPERATOR" in text or "<dockerhub-user>" in text:
            return CheckResult("final_placeholders", "fail", f"placeholder in {name}")
    return CheckResult("final_placeholders", "pass")


def check_scorecard_present(ctx: HygieneContext) -> CheckResult:
    try:
        text = (ctx.root / "README.md").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return CheckResult("scorecard_present", "fail", "README.md missing")
    if README_START not in text or README_END not in text:
        return CheckResult("scorecard_present", "fail", "scorecard markers missing")
    block = text.split(README_START, 1)[1].split(README_END, 1)[0]
    if "Scorecard not generated yet" in block:
        return CheckResult("scorecard_present", "fail", "scorecard not generated")
    if not any(line.strip().startswith("|") for line in block.splitlines()):
        return CheckResult("scorecard_present", "fail", "scorecard has no table rows")
    return CheckResult("scorecard_present", "pass")


CHECKS: tuple[Callable[[HygieneContext], CheckResult], ...] = (
    check_required_files,
    check_licence_copy,
    check_forbidden_tracked,
    check_secrets,
    check_ignore_files,
    check_banned_terms,
    check_starter_leftovers,
    check_readme_structure,
    check_size_limits,
    check_dockerfile_rules,
    check_eval_data_frozen,
    check_final_placeholders,
    check_scorecard_present,
)
FINAL_ONLY: frozenset[str] = frozenset({"final_placeholders", "scorecard_present"})


def run_all(ctx: HygieneContext) -> list[CheckResult]:
    """Run CHECKS in order.

    A check in FINAL_ONLY when ctx.final is False returns
    CheckResult(name, 'skip', 'final only') without running. An exception inside
    a check becomes CheckResult(name, 'fail', f'{type(e).__name__}: {e}').
    """
    results: list[CheckResult] = []
    for check in CHECKS:
        name = check.__name__[len("check_") :]
        if name in FINAL_ONLY and not ctx.final:
            results.append(CheckResult(name, "skip", "final only"))
            continue
        try:
            results.append(check(ctx))
        except Exception as exc:  # noqa: BLE001 - the gate must never crash
            results.append(CheckResult(name, "fail", f"{type(exc).__name__}: {exc}"))
    return results


def format_report(results: list[CheckResult]) -> tuple[str, int]:
    """Format results, one line each, plus a summary line; return (text, exit code)."""
    lines: list[str] = []
    for result in results:
        if result.status == "pass":
            lines.append(f"[PASS] {result.name}")
        elif result.status == "fail":
            lines.append(f"[FAIL] {result.name}: {result.detail}" if result.detail else f"[FAIL] {result.name}")
        else:
            lines.append(f"[SKIP] {result.name} ({result.detail})" if result.detail else f"[SKIP] {result.name}")
    failed = [result for result in results if result.status == "fail"]
    if failed:
        lines.append(f"HYGIENE: FAIL ({len(failed)} failed)")
        return "\n".join(lines), 1
    kept = [result for result in results if result.status != "skip"]
    skipped = len(results) - len(kept)
    count = len(kept)
    if skipped:
        lines.append(f"HYGIENE: PASS ({count}/{count}, {skipped} skipped)")
    else:
        lines.append(f"HYGIENE: PASS ({count}/{count})")
    return "\n".join(lines), 0

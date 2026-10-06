"""Unit tests for release.hygiene (DP-RELEASE WU-RELEASE-01)."""
from release import hygiene
from release.hygiene import (
    HygieneContext,
    README_END,
    README_HEADINGS,
    README_START,
    REQUIRED_FILES,
    run_all,
)

import evals.report

REFERENCE_DOCKERFILE = """# syntax=docker/dockerfile:1
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
ENV PYTHONUNBUFFERED=1 \\
    PYTHONDONTWRITEBYTECODE=1 \\
    UV_COMPILE_BYTECODE=1 \\
    UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY main.py ./
COPY hyperion ./hyperion
COPY data ./data
RUN useradd --system --no-create-home --uid 10001 hyperion && chown hyperion /app/data
USER hyperion
ENV PATH="/app/.venv/bin:${PATH}" \\
    IDE_BACKEND_URL=http://host.docker.internal:3001/api
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \\
  CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
CMD ["python", "main.py"]
"""


def _ctx(tmp_path, files, **kwargs):
    return HygieneContext(root=tmp_path, files=tuple(files), **kwargs)


def test_required_files_missing_fails(tmp_path):
    result = hygiene.check_required_files(_ctx(tmp_path, ("README.md",)))
    assert result.status == "fail"
    assert "LICENCE" in result.detail


def test_required_files_present_passes(tmp_path):
    result = hygiene.check_required_files(_ctx(tmp_path, REQUIRED_FILES))
    assert result.status == "pass"


def test_licence_copy_mismatch_fails(tmp_path):
    (tmp_path / "LICENCE").write_bytes(b"Apache License Version 2.0 A")
    (tmp_path / "LICENSE").write_bytes(b"something else entirely")
    result = hygiene.check_licence_copy(_ctx(tmp_path, ()))
    assert result.status == "fail"
    (tmp_path / "LICENSE").write_bytes(b"Apache License Version 2.0 A")
    result = hygiene.check_licence_copy(_ctx(tmp_path, ()))
    assert result.status == "pass"


def test_forbidden_tracked_detects_env_and_hee_and_design_documents(tmp_path):
    files = (
        ".env",
        "hee/x.md",
        "design_documents/a.md",
        ".c1pg-1.png",
        "hyperion/__pycache__/a.pyc",
    )
    result = hygiene.check_forbidden_tracked(_ctx(tmp_path, files))
    assert result.status == "fail"
    for path in files:
        assert path in result.detail


def test_forbidden_tracked_allows_gitkeep_and_scorecards(tmp_path):
    allowed = (
        "corpus/drive/.gitkeep",
        "evals/results/.gitkeep",
        "evals/results/live/scorecard.md",
        "evals/results/live/scorecard.json",
    )
    assert hygiene.check_forbidden_tracked(_ctx(tmp_path, allowed)).status == "pass"
    result = hygiene.check_forbidden_tracked(_ctx(tmp_path, allowed + ("evals/results/live/raw.json",)))
    assert result.status == "fail"


def test_secret_pattern_flags_assigned_key(tmp_path):
    name = "API_" + "KEY"
    (tmp_path / "a.txt").write_text(name + ' = "abcd1234abcd1234abcd"\n', encoding="utf-8")
    result = hygiene.check_secrets(_ctx(tmp_path, ("a.txt",)))
    assert result.status == "fail"
    assert "assigned_key in" in result.detail


def test_secret_pattern_ignores_test_key_and_empty(tmp_path):
    (tmp_path / "a.txt").write_text('api_key="test-key"\n', encoding="utf-8")
    (tmp_path / "b.txt").write_text("API_KEY=\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("API_KEY=${API_KEY}\n", encoding="utf-8")
    result = hygiene.check_secrets(_ctx(tmp_path, ("a.txt", "b.txt", "c.txt")))
    assert result.status == "pass"


def test_dotenv_key_leak_detected(tmp_path):
    secret = "s3cr3t-value-123"
    (tmp_path / "leak.txt").write_text("token " + secret + " here\n", encoding="utf-8")
    ctx = HygieneContext(root=tmp_path, files=("leak.txt",), dotenv_key=secret)
    result = hygiene.check_secrets(ctx)
    assert result.status == "fail"
    assert secret not in result.detail


def test_banned_terms_flagged_and_exempt_files_skipped(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "x.md").write_text("Gemini says hi\n", encoding="utf-8")
    result = hygiene.check_banned_terms(_ctx(tmp_path, ("docs/x.md",)))
    assert result.status == "fail"
    result = hygiene.check_banned_terms(_ctx(tmp_path, ("release/hygiene.py",)))
    assert result.status == "pass"


def test_readme_structure_ok(tmp_path):
    lines = list(README_HEADINGS) + [README_START, README_END, "see disclosure.md and LICENCE"]
    (tmp_path / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = hygiene.check_readme_structure(_ctx(tmp_path, ("README.md",)))
    assert result.status == "pass"
    assert hygiene.README_START == evals.report.README_START
    assert hygiene.README_END == evals.report.README_END


def test_readme_missing_heading_or_marker_fails(tmp_path):
    lines = list(README_HEADINGS) + [README_START, README_END, "see disclosure.md and LICENCE"]
    (tmp_path / "README.md").write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")
    assert hygiene.check_readme_structure(_ctx(tmp_path, ("README.md",))).status == "fail"
    (tmp_path / "README.md").write_text(
        "\n".join(line for line in lines if line != README_END) + "\n", encoding="utf-8"
    )
    assert hygiene.check_readme_structure(_ctx(tmp_path, ("README.md",))).status == "fail"


def test_dockerfile_rules_pass_for_reference_dockerfile(tmp_path):
    (tmp_path / "Dockerfile").write_text(REFERENCE_DOCKERFILE, encoding="utf-8")
    result = hygiene.check_dockerfile_rules(_ctx(tmp_path, ("Dockerfile",)))
    assert result.status == "pass"


def test_dockerfile_rules_fail_on_copy_dot_and_no_user(tmp_path):
    bad = REFERENCE_DOCKERFILE.replace("COPY hyperion ./hyperion", "COPY . .")
    (tmp_path / "Dockerfile").write_text(bad, encoding="utf-8")
    assert hygiene.check_dockerfile_rules(_ctx(tmp_path, ("Dockerfile",))).status == "fail"
    without_user = REFERENCE_DOCKERFILE.replace("USER hyperion\n", "")
    (tmp_path / "Dockerfile").write_text(without_user, encoding="utf-8")
    assert hygiene.check_dockerfile_rules(_ctx(tmp_path, ("Dockerfile",))).status == "fail"


def test_size_limits_flags_large_file(tmp_path):
    (tmp_path / "big.bin").write_bytes(b"x" * 5_000_001)
    assert hygiene.check_size_limits(_ctx(tmp_path, ("big.bin",))).status == "fail"
    data = tmp_path / "data"
    data.mkdir()
    (data / "index.json").write_bytes(b"x" * 5_000_001)
    assert hygiene.check_size_limits(_ctx(tmp_path, ("data/index.json",))).status == "pass"


def test_final_mode_flags_operator_placeholders_and_unfilled_scorecard(tmp_path):
    base = list(README_HEADINGS) + [README_START, "nothing yet", README_END, "disclosure.md LICENCE"]
    (tmp_path / "README.md").write_text("\n".join(base) + "\n", encoding="utf-8")
    results = run_all(HygieneContext(root=tmp_path, files=("README.md",), final=False))
    by_name = {result.name: result for result in results}
    assert by_name["final_placeholders"].status == "skip"
    assert by_name["scorecard_present"].status == "skip"
    (tmp_path / "README.md").write_text(
        "\n".join(base).replace("nothing yet", "{{OPERATOR: x}}\nScorecard not generated yet"),
        encoding="utf-8",
    )
    results = run_all(HygieneContext(root=tmp_path, files=("README.md",), final=True))
    by_name = {result.name: result for result in results}
    assert by_name["final_placeholders"].status == "fail"
    assert by_name["scorecard_present"].status == "fail"
    (tmp_path / "README.md").write_text(
        "\n".join(base).replace("nothing yet", "| a | b |"), encoding="utf-8"
    )
    results = run_all(HygieneContext(root=tmp_path, files=("README.md",), final=True))
    by_name = {result.name: result for result in results}
    assert by_name["final_placeholders"].status == "pass"
    assert by_name["scorecard_present"].status == "pass"

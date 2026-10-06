"""Unit tests for hyperion.dsl.repair, hyperion.dsl.report and hyperion.dsl.summary."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from hyperion.dsl.check import check_profile
from hyperion.dsl.repair import deterministic_repair
from hyperion.dsl.report import report_for
from hyperion.dsl.summary import summarize_profile
from hyperion.ide.gateway import IdeGateway
from hyperion.testing.fake_ide import FakeIdeBackend, FakeWorkspace

FIX = Path(__file__).parent.parent / "fixtures" / "profiles"


def _read(name: str) -> str:
    return (FIX / name).read_text()


def _errors(text: str):
    return [i for i in check_profile(text) if i.severity == "error"]


def _with(path: str, value: object, base: str = "native_nginx.yaml") -> str:
    doc = yaml.safe_load(_read(base))
    node = doc
    *parents, leaf = path.split(".")
    for p in parents:
        node = node[p]
    node[leaf] = value
    return yaml.safe_dump(doc, sort_keys=False)


def test_repair_missing_owner_adds_default_and_says_so() -> None:
    text = _read("native_broken_owner.yaml")
    result = deterministic_repair(text, check_profile(text))
    assert result is not None
    assert result.applied == (
        "Filled `owner` with the default 'HyperAI-User'. - change it to your team name.",
    )
    assert yaml.safe_load(result.text)["applicationProfile"]["metadata"]["owner"] == "HyperAI-User"


def test_repair_enum_closest() -> None:
    text = _with("applicationProfile.metadata.lifecyclePhase", "Production")
    result = deterministic_repair(text, check_profile(text))
    assert result is not None
    assert result.applied == ("Changed `lifecyclePhase` from 'Production' to 'production'.",)
    assert _errors(result.text) == []


def test_repair_cpu_cores_to_millicores() -> None:
    text = _with("applicationProfile.specs.resources.cpu", "2 cores")
    result = deterministic_repair(text, check_profile(text))
    assert result is not None
    assert yaml.safe_load(result.text)["applicationProfile"]["specs"]["resources"]["cpu"] == "2000m"
    assert _errors(result.text) == []


def test_repair_memory_gb_to_gi() -> None:
    text = _with("applicationProfile.specs.resources.memory", "10GB")
    result = deterministic_repair(text, check_profile(text))
    assert result is not None
    assert yaml.safe_load(result.text)["applicationProfile"]["specs"]["resources"]["memory"] == "10Gi"


def test_repair_port_string_to_int() -> None:
    doc = yaml.safe_load(_read("native_nginx.yaml"))
    doc["applicationProfile"]["specs"]["network"]["ports"][0]["port"] = "8080"
    text = yaml.safe_dump(doc, sort_keys=False)
    result = deterministic_repair(text, check_profile(text))
    assert result is not None
    assert doc["applicationProfile"]["specs"]["network"]["ports"][0]["port"] == "8080"  # input untouched
    assert yaml.safe_load(result.text)["applicationProfile"]["specs"]["network"]["ports"][0]["port"] == 8080


def test_repair_version_to_semver() -> None:
    text = _with("applicationProfile.metadata.version", "1.2")
    result = deterministic_repair(text, check_profile(text))
    assert result is not None
    assert yaml.safe_load(result.text)["applicationProfile"]["metadata"]["version"] == "1.2.0"


def test_repair_out_of_range_unfixed() -> None:
    doc = yaml.safe_load(_read("native_nginx.yaml"))
    doc["applicationProfile"]["specs"]["network"]["ports"][0]["port"] = 99999
    text = yaml.safe_dump(doc, sort_keys=False)
    issues = check_profile(text)
    assert any(i.code == "out_of_range" for i in issues)
    assert deterministic_repair(text, issues) is None


def test_repair_yaml_syntax_returns_none() -> None:
    text = "applicationProfile:\n  metadata: [broken\n"
    assert deterministic_repair(text, check_profile(text)) is None


def test_repaired_profile_passes_check() -> None:
    text = _read("native_broken_owner.yaml")
    result = deterministic_repair(text, check_profile(text))
    assert result is not None
    assert _errors(result.text) == []


def test_report_for_valid() -> None:
    report = report_for("app.yaml", _read("native_nginx.yaml"))
    assert report["valid"] is True
    assert report["type"] == "native"
    assert report["errors"] == []


def test_report_for_invalid_shape() -> None:
    report = report_for("native_broken_owner.yaml", _read("native_broken_owner.yaml"))
    assert report["valid"] is False
    assert len(report["errors"]) == 1
    assert "owner" in report["errors"][0]["message"]  # type: ignore[index]


def test_report_for_non_yaml_file() -> None:
    report = report_for("notes.txt", _read("native_broken_owner.yaml"))
    assert report == {"path": "notes.txt", "type": "unknown", "valid": True, "errors": [], "warnings": []}


def test_summarize_native() -> None:
    summary = summarize_profile(_read("native_nginx.yaml"))
    assert summary.startswith("Native app `hello-world-webserver`")
    assert "nginx:latest" in summary
    assert "8000/TCP" in summary


def test_summarize_device() -> None:
    summary = summarize_profile(_read("device_docker.yaml"))
    assert summary.startswith("Device app `hello-world`")
    assert "DockerImage" in summary
    assert "80/HTTP" in summary


def test_summarize_unknown() -> None:
    assert summarize_profile("foo: 1\n") == "Not a recognised HyperAI profile."
    assert summarize_profile("a: [broken\n") == "Not a recognised HyperAI profile."


async def test_fake_ide_uses_report_for(ide_settings: Any) -> None:
    broken = _read("native_broken_owner.yaml")
    backend = FakeIdeBackend(FakeWorkspace({"work/native_broken_owner.yaml": broken}), validator=report_for)
    gateway = IdeGateway(ide_settings, transport=backend.transport())
    try:
        result = await gateway.validate("native_broken_owner.yaml")
    finally:
        await gateway.aclose()
    assert result.status == "ok"
    assert result.valid is False
    assert len(result.errors) == 1
    assert "owner" in result.errors[0].message

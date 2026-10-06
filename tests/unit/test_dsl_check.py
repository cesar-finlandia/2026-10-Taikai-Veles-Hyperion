"""Unit tests for hyperion.dsl.check (and locate)."""
from __future__ import annotations

from pathlib import Path

import yaml

from hyperion.dsl.check import check_document, check_profile
from hyperion.dsl.locate import locate_line

FIX = Path(__file__).parent.parent / "fixtures" / "profiles"


def _read(name: str) -> str:
    return (FIX / name).read_text()


def _errors(text: str):
    return [i for i in check_profile(text) if i.severity == "error"]


def test_cookbook_native_has_no_errors() -> None:
    assert _errors(_read("native_nginx.yaml")) == []


def test_cookbook_device_docker_has_no_errors() -> None:
    assert _errors(_read("device_docker.yaml")) == []


def test_cookbook_device_android_has_no_errors() -> None:
    assert _errors(_read("device_android.yaml")) == []


def test_missing_owner_reported_with_line() -> None:
    text = _read("native_broken_owner.yaml")
    issues = check_profile(text)
    missing = [i for i in issues if i.code == "missing_required" and i.path == "applicationProfile.metadata.owner"]
    assert len(missing) == 1
    assert missing[0].line is not None and missing[0].line > 0
    assert "owner" in missing[0].message


def test_bad_enum_lifecycle() -> None:
    doc = yaml.safe_load(_read("native_nginx.yaml"))
    doc["applicationProfile"]["metadata"]["lifecyclePhase"] = "staging"
    issues = check_document(doc, "native", text=_read("native_nginx.yaml"))
    bad = [i for i in issues if i.code == "bad_enum"]
    assert len(bad) == 1
    assert "staging" in bad[0].message


def test_bad_cpu_format() -> None:
    doc = yaml.safe_load(_read("native_nginx.yaml"))
    doc["applicationProfile"]["specs"]["resources"]["cpu"] = "2 CPUs"
    issues = check_document(doc, "native")
    assert any(i.code == "bad_format" and "500m" in i.message for i in issues)


def test_port_out_of_range() -> None:
    doc = yaml.safe_load(_read("native_nginx.yaml"))
    doc["applicationProfile"]["specs"]["network"]["ports"][0]["port"] = 99999
    issues = check_document(doc, "native")
    bad = [i for i in issues if i.code == "out_of_range"]
    assert len(bad) == 1
    assert bad[0].path == "applicationProfile.specs.network.ports[0].port"


def test_port_wrong_type() -> None:
    doc = yaml.safe_load(_read("native_nginx.yaml"))
    doc["applicationProfile"]["specs"]["network"]["ports"][0]["port"] = "eighty"
    issues = check_document(doc, "native")
    assert any(i.code == "wrong_type" for i in issues)


def test_yaml_syntax_error_has_line() -> None:
    issues = check_profile("applicationProfile:\n  metadata: [unclosed\n")
    assert len(issues) == 1
    assert issues[0].code == "yaml_syntax"
    assert issues[0].line is not None


def test_unknown_kind() -> None:
    issues = check_profile("foo: 1\n")
    assert len(issues) == 1
    assert issues[0].code == "unknown_profile_kind"
    assert issues[0].severity == "error"


def test_unknown_field_is_warning() -> None:
    doc = yaml.safe_load(_read("native_nginx.yaml"))
    doc["applicationProfile"]["specs"]["resources"]["bogusField"] = 1
    issues = check_document(doc, "native")
    warns = [i for i in issues if i.code == "unknown_field"]
    assert len(warns) == 1
    assert warns[0].severity == "warning"


def test_missing_section() -> None:
    doc = yaml.safe_load(_read("native_nginx.yaml"))
    del doc["applicationProfile"]["specs"]["resources"]
    issues = check_document(doc, "native")
    assert any(
        i.code == "missing_section" and i.path == "applicationProfile.specs.resources"
        for i in issues
    )


def test_empty_document() -> None:
    assert check_profile("   \n")[0].code == "empty_document"


def test_locate_line_nested_and_list() -> None:
    text = _read("native_nginx.yaml")
    assert locate_line(text, "applicationProfile.metadata.owner") == 8
    assert locate_line(text, "applicationProfile.specs.network.ports[0].port") == 35
    assert locate_line(text, "applicationProfile.metadata.nope") == 3
    assert locate_line(text, "applicationProfile.specs.network.ports[0].port") is not None
    assert locate_line("not: [valid", "a.b") is None

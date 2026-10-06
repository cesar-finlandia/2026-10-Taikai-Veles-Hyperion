"""Unit tests for hyperion.dsl.detect."""
from __future__ import annotations

from hyperion.dsl.detect import (
    detect_kind_from_doc,
    detect_kind_from_request,
    detect_kind_from_text,
    looks_like_profile_request,
)


def test_kind_from_doc_native() -> None:
    assert detect_kind_from_doc({"applicationProfile": {"metadata": {}}}) == "native"


def test_kind_from_doc_device() -> None:
    assert detect_kind_from_doc({"apiVersion": "hyper.ai/v1", "kind": "Application"}) == "device"
    assert detect_kind_from_doc({"kind": "Application"}) == "device"
    assert detect_kind_from_doc({"spec": {"app": {}}}) == "device"


def test_kind_from_doc_unknown() -> None:
    assert detect_kind_from_doc({}) == "unknown"
    assert detect_kind_from_doc([]) == "unknown"
    assert detect_kind_from_doc("hello") == "unknown"
    assert detect_kind_from_doc(None) == "unknown"


def test_kind_from_text_regex_fallback_on_broken_yaml() -> None:
    assert detect_kind_from_text("applicationProfile:\n  metadata: [unclosed") == "native"
    assert detect_kind_from_text("apiVersion: hyper.ai/v1\nkind: [oops") == "device"
    assert detect_kind_from_text("just: [broken") == "unknown"


def test_request_defaults_to_native() -> None:
    assert detect_kind_from_request("create a deployment YAML with nginx") == "native"
    assert detect_kind_from_request("hello there") == "native"


def test_request_device_cues() -> None:
    assert detect_kind_from_request("deploy an android apk to the device") == "device"
    assert detect_kind_from_request("firmware for my esp32 board") == "device"
    assert detect_kind_from_request("a device app with redis") == "device"


def test_looks_like_profile_request_positive() -> None:
    assert looks_like_profile_request("create a deployment YAML with nginx") is True
    assert looks_like_profile_request("write the app.yaml for redis") is True
    assert looks_like_profile_request("make me a device app manifest") is True


def test_looks_like_profile_request_negative() -> None:
    assert looks_like_profile_request("what is the weather today") is False
    assert looks_like_profile_request("tell me a joke about nginx") is False

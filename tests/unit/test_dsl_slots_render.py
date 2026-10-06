"""Unit tests for hyperion.dsl.slots and hyperion.dsl.render."""
from __future__ import annotations

from pathlib import Path

import yaml

from hyperion.dsl.check import check_profile
from hyperion.dsl.render import default_filename, render_device, render_native, render_profile
from hyperion.dsl.slots import (
    DeviceSlots,
    NativeSlots,
    extract_slots,
    merge_llm_slots,
    slug,
)

FIX = Path(__file__).parent.parent / "fixtures" / "profiles"


def _errors(text: str):
    return [i for i in check_profile(text) if i.severity == "error"]


def test_extract_nginx_defaults() -> None:
    ext = extract_slots("create a deployment YAML with nginx")
    assert ext.kind == "native"
    assert isinstance(ext.slots, NativeSlots)
    assert ext.slots.image_uri == "nginx"
    assert ext.slots.image_tag == "latest"
    assert ext.slots.ports[0].port == 80
    assert ext.slots.entry_point == "nginx"
    assert "port" not in ext.from_user  # defaulted
    assert any("Assumed a native app profile" in n for n in ext.notes)


def test_extract_port_and_name() -> None:
    ext = extract_slots("deploy nginx on port 9000 named my-web")
    assert ext.kind == "native"
    assert ext.slots.ports[0].port == 9000
    assert "port" in ext.from_user
    assert ext.slots.name == "my-web"
    assert "name" in ext.from_user


def test_extract_memory_cpu_units() -> None:
    ext = extract_slots("nginx with 2 cpus and 1 GB memory")
    assert isinstance(ext.slots, NativeSlots)
    assert ext.slots.cpu == "2000m"
    assert ext.slots.memory == "1Gi"


def test_extract_known_images_ports() -> None:
    assert extract_slots("deploy redis yaml").slots.ports[0].port == 6379
    assert extract_slots("deploy grafana yaml").slots.ports[0].port == 3000


def test_extract_unknown_image_defaults() -> None:
    ext = extract_slots("deploy the frobnicate docker image")
    assert isinstance(ext.slots, NativeSlots)
    assert ext.slots.image_uri == "frobnicate"
    assert ext.slots.ports[0].port == 8080
    assert ext.slots.entry_point == "frobnicate"


def test_extract_device_cues_and_apk() -> None:
    ext = extract_slots(
        "deploy a device app android apk from https://example.com/app.apk "
        "package com.example.app as yaml"
    )
    assert ext.kind == "device"
    assert isinstance(ext.slots, DeviceSlots)
    assert ext.slots.workload_kind == "AndroidApk"
    assert ext.slots.apk_url == "https://example.com/app.apk"
    assert ext.slots.package_name == "com.example.app"
    assert ext.slots.architectures == ["arm64-v8a"]


def test_extract_esp32_chip() -> None:
    ext = extract_slots("device app firmware for esp32s3 board, yaml please")
    assert ext.kind == "device"
    assert isinstance(ext.slots, DeviceSlots)
    assert ext.slots.workload_kind == "esp32Binary"
    assert ext.slots.chip == "esp32s3"


def test_extract_lifecycle_owner_version() -> None:
    ext = extract_slots("version 1.2.3 of a production redis deployment, owner Alice, yaml")
    assert ext.slots.lifecycle_phase == "production"
    assert ext.slots.owner == "Alice"
    assert ext.slots.version == "1.2.3"


def test_extract_tag_from_image_colon() -> None:
    ext = extract_slots("deploy nginx:1.21 image yaml")
    assert isinstance(ext.slots, NativeSlots)
    assert ext.slots.image_uri == "nginx"
    assert ext.slots.image_tag == "1.21"


def test_extract_private_and_arch_flags() -> None:
    ext = extract_slots("private nginx yaml for arm64 only")
    assert ext.slots.ports[0].public is False
    assert ext.slots.architectures == ["arm64"]


def test_merge_llm_respects_from_user() -> None:
    ext = extract_slots("deploy nginx on port 9000 yaml")
    assert ext.slots.ports[0].port == 9000
    merged = merge_llm_slots(ext, {"port": 1234, "owner": "LLM-Team", "cpu": "1000m"})
    assert merged.slots.ports[0].port == 9000  # user value wins
    assert isinstance(merged.slots, NativeSlots)
    assert merged.slots.owner == "LLM-Team"
    assert merged.slots.cpu == "1000m"
    assert ext.slots.owner == "HyperAI-User"  # input not mutated
    assert any("owner" in n for n in merged.notes)


def test_merge_llm_rejects_invalid_values() -> None:
    ext = extract_slots("deploy nginx yaml")
    merged = merge_llm_slots(
        ext, {"port": 99999, "cpu": "lots", "lifecycle": "staging", "owner": "Good Name"}
    )
    assert merged.slots.ports[0].port == 80
    assert isinstance(merged.slots, NativeSlots)
    assert merged.slots.cpu == "500m"
    assert merged.slots.lifecycle_phase == "development"
    assert merged.slots.owner == "Good Name"


def test_render_native_nginx_passes_check() -> None:
    ext = extract_slots("create a deployment YAML with nginx")
    assert isinstance(ext.slots, NativeSlots)
    assert _errors(render_native(ext.slots)) == []


def test_render_native_matches_cookbook_shape() -> None:
    ext = extract_slots("create a deployment YAML with nginx")
    assert isinstance(ext.slots, NativeSlots)
    rendered = yaml.safe_load(render_native(ext.slots))
    cookbook = yaml.safe_load((FIX / "native_nginx.yaml").read_text())

    def key_paths(node: object, prefix: str = "") -> set[str]:
        if isinstance(node, dict):
            out = set()
            for k, v in node.items():
                p = f"{prefix}.{k}" if prefix else str(k)
                out.add(p)
                out |= key_paths(v, p)
            return out
        if isinstance(node, list):
            out = set()
            for item in node:
                out |= key_paths(item, prefix)
            return out
        return set()

    got = key_paths(rendered)
    want = key_paths(cookbook)
    assert got <= want
    assert "applicationProfile.specs.runtime" in got
    assert "applicationProfile.specs.qos" in got


def test_render_device_docker_passes_check() -> None:
    ext = extract_slots("device app with the hello-world docker image yaml")
    assert isinstance(ext.slots, DeviceSlots)
    assert _errors(render_device(ext.slots)) == []


def test_render_device_android_passes_check() -> None:
    ext = extract_slots(
        "device app android apk https://example.com/app.apk com.example.app yaml"
    )
    assert isinstance(ext.slots, DeviceSlots)
    assert ext.slots.workload_kind == "AndroidApk"
    assert _errors(render_device(ext.slots)) == []


def test_render_device_esp32_passes_check() -> None:
    ext = extract_slots("device app esp32 firmware https://example.com/fw.bin yaml")
    assert isinstance(ext.slots, DeviceSlots)
    assert ext.slots.workload_kind == "esp32Binary"
    assert _errors(render_device(ext.slots)) == []


def test_render_quotes_special_characters() -> None:
    slots = NativeSlots(name='my "weird" app', description="a: b, c # d")
    text = render_native(slots)
    doc = yaml.safe_load(text)
    assert doc["applicationProfile"]["metadata"]["name"] == 'my "weird" app'
    assert _errors(text) == []


def test_render_multiple_ports() -> None:
    from hyperion.dsl.slots import PortSlot

    slots = NativeSlots(ports=[PortSlot(80, "TCP"), PortSlot(443, "TCP", False)])
    text = render_native(slots)
    doc = yaml.safe_load(text)
    ports = doc["applicationProfile"]["specs"]["network"]["ports"]
    assert [p["port"] for p in ports] == [80, 443]
    assert _errors(text) == []


def test_render_ends_with_single_newline() -> None:
    assert render_profile(NativeSlots()).endswith("\n")
    assert not render_profile(NativeSlots()).endswith("\n\n")
    assert default_filename(NativeSlots()) == "app.yaml"
    assert slug("Hello World!") == "hello-world"

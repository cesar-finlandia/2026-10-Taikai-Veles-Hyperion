"""Render slot dataclasses to HyperAI profile YAML (code writes YAML, never the model)."""
from __future__ import annotations

import json

from hyperion.dsl.slots import DeviceSlots, NativeSlots, PortSlot, Slots


def Q(x: object) -> str:
    return json.dumps(x, ensure_ascii=False)


def _b(v: bool) -> str:
    return "true" if v else "false"


def render_native(slots: NativeSlots) -> str:
    ports = slots.ports or [PortSlot(8080)]
    port_blocks = "\n".join(
        f"        - port: {p.port}\n"
        f"          protocol: {Q(p.protocol)}\n"
        f"          publicExposure: {_b(p.public)}"
        for p in ports
    )
    return (
        "applicationProfile:\n"
        "  metadata:\n"
        '    type: "native"\n'
        '    schemaVersion: "1.1.0"\n'
        f"    name: {Q(slots.name)}\n"
        f"    version: {Q(slots.version)}\n"
        f"    description: {Q(slots.description)}\n"
        f"    owner: {Q(slots.owner)}\n"
        f"    lifecyclePhase: {Q(slots.lifecycle_phase)}\n"
        "    annotations:\n"
        f"      intent: {Q(slots.intent)}\n"
        f"      domain: {Q(slots.domain)}\n"
        "\n"
        "  specs:\n"
        "    runtime:\n"
        '      executionType: "container"\n'
        f"      entryPoint: {Q(slots.entry_point)}\n"
        f"      args: {Q(slots.args)}\n"
        "      baseOS:\n"
        f"        name: {Q(slots.base_os_name)}\n"
        f"        version: {Q(slots.base_os_version)}\n"
        "      containerImage:\n"
        f"        uri: {Q(slots.image_uri)}\n"
        f"        tag: {Q(slots.image_tag)}\n"
        "\n"
        "    resources:\n"
        f"      cpu: {Q(slots.cpu)}\n"
        f"      memory: {Q(slots.memory)}\n"
        f"      storage: {Q(slots.storage)}\n"
        "\n"
        "    network:\n"
        "      ports:\n"
        f"{port_blocks}\n"
        f"      protocols: {Q(slots.protocols)}\n"
        "\n"
        "    constraints:\n"
        f"      supportedArchitectures: {Q(slots.architectures)}\n"
        f"      securityLevel: {Q(slots.security_level)}\n"
        f"      dataClassification: {Q(slots.data_classification)}\n"
        f"      isHighlyAvailable: {_b(slots.highly_available)}\n"
        "\n"
        "    qos:\n"
        f"      startupTime: {Q(slots.startup_time)}\n"
        f"      availability: {Q(slots.availability)}\n"
    )


def render_device(slots: DeviceSlots) -> str:
    if slots.workload_kind == "DockerImage":
        workload = (
            "    dockerImage:\n"
            f"      image: {Q(slots.image)}\n"
            f"      imagePullPolicy: {Q(slots.pull_policy)}"
        )
    elif slots.workload_kind == "AndroidApk":
        workload = (
            "    androidApk:\n"
            f"      apkUrl: {Q(slots.apk_url)}\n"
            f"      packageName: {Q(slots.package_name)}\n"
            '      installMode: "install"'
        )
    else:
        workload = (
            "    esp32Binary:\n"
            f"      binaryUrl: {Q(slots.binary_url)}\n"
            f"      chip: {Q(slots.chip)}\n"
            "      flash:\n"
            f"        method: {Q(slots.flash_method)}"
        )
    exec_lines = ""
    if slots.env or slots.command:
        exec_lines = "  exec:\n"
        if slots.command:
            exec_lines += f"    command: {Q(slots.command)}\n"
        if slots.env:
            exec_lines += "    env:\n"
            for k in sorted(slots.env):
                exec_lines += f"      {k}: {Q(slots.env[k])}\n"
    if slots.ports:
        port_blocks = "\n".join(
            f"      - port: {p.port}\n"
            f"        protocol: {Q(p.protocol)}"
            for p in slots.ports
        )
        ports_block = f"    ports:\n{port_blocks}\n"
    else:
        ports_block = "    ports: []\n"
    device_line = f"  device_name: {Q(slots.device_name)}\n" if slots.device_name else ""
    return (
        "apiVersion: hyper.ai/v1\n"
        "kind: Application\n"
        "metadata:\n"
        f"  name: {Q(slots.name)}\n"
        "  annotations:\n"
        f"    intent: {Q(slots.intent)}\n"
        f"    domain: {Q(slots.domain)}\n"
        "spec:\n"
        f"{device_line}"
        "  app:\n"
        "    type: device\n"
        '    schemaVersion: "1.0.0"\n'
        f"    name: {Q(slots.name)}\n"
        f"    version: {Q(slots.version)}\n"
        f"    description: {Q(slots.description)}\n"
        f"    owner: {Q(slots.owner)}\n"
        f"    lifecyclePhase: {Q(slots.lifecycle_phase)}\n"
        "  workload:\n"
        f"    kind: {slots.workload_kind}\n"
        f"{workload}\n"
        f"{exec_lines}"
        "  network:\n"
        f"{ports_block}"
        '    networkBandwidthMin: { value: 1, unit: "Mbps" }\n'
        "  qos:\n"
        '    latencyToleranceMax: { value: 500, unit: "ms" }\n'
        '    energyCost: { value: 1, unit: "W" }\n'
        '    monetaryCost: { value: 0.01, currency: "USD", per: "hour" }\n'
        '    resilience: "auto-restart"\n'
        "    availability: { value: 0.90, unit: \"fraction\" }\n"
        '    startupTime: { value: 5, unit: "s" }\n'
        "  constraints:\n"
        "    schedulingPriority: 1\n"
        f"    supportedArchitectures: {Q(slots.architectures)}\n"
        '    geoLocationRequirement: "LocalZone"\n'
        "    isHighlyAvailable: false\n"
        '    faultTolerance: "graceful-degradation"\n'
        '    dataClassification: "internal"\n'
    )


def render_profile(slots: Slots) -> str:
    """Dispatch on type. Output ends with exactly one '\\n'. Every string value is JSON-quoted (valid YAML)."""
    text = render_device(slots) if isinstance(slots, DeviceSlots) else render_native(slots)
    return text.rstrip("\n") + "\n"


def default_filename(slots: Slots) -> str:
    """'app.yaml' ; the caller decides between this and f'{slots.name}.yaml' when app.yaml exists."""
    _ = slots
    return "app.yaml"

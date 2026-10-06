"""Offline mirror of the HyperAI Native Apps and Device Apps specifications."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FieldType = Literal["str", "int", "num", "bool", "list", "map", "strnum"]
ProfileKind = Literal["native", "device", "unknown"]


@dataclass(frozen=True)
class FieldRule:
    path: str                       # dotted from the document root; '[]' = every list item, e.g. 'spec.network.ports[].port'
    type: FieldType
    required: bool = False          # required WHEN THE PARENT OBJECT EXISTS
    enum: tuple[str, ...] = ()
    pattern: str | None = None      # regex the whole string must match (str / strnum only)
    lo: float | None = None         # inclusive numeric lower bound
    hi: float | None = None         # inclusive numeric upper bound


def _R(path: str, type: FieldType, required: bool = False, enum: tuple[str, ...] = (),
       pattern: str | None = None, lo: float | None = None,
       hi: float | None = None) -> FieldRule:
    return FieldRule(path=path, type=type, required=required, enum=enum,
                     pattern=pattern, lo=lo, hi=hi)


PATTERNS: dict[str, str] = {
    "semver": r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.\-]+)?$",
    "rfc3339": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$",
    "cpu_m": r"^[1-9]\d*m$",
    "mem": r"^[1-9]\d*(Mi|Gi|Ti)$",
    "bw": r"^[1-9]\d*(Mbps|Gbps)$",
    "ms": r"^\d+ms$",
    "kwh": r"^\d+(\.\d+)?kWh$",
    "pct": r"^\d+(\.\d+)?%$",
    "secs": r"^\d+s$",
    "uri": r"^https?://\S+$",
    "money": r"^\d+(\.\d+)?$",
    "trust": r"^[1-5](\.\d+)?$",
}

FORMAT_HINTS: dict[str, str] = {
    "semver": "a version like 1.0.0",
    "cpu_m": "millicores like 500m",
    "mem": "a size like 512Mi or 2Gi",
    "bw": "a bandwidth like 100Mbps",
    "ms": "milliseconds like 150ms",
    "kwh": "energy like 0.5kWh",
    "pct": "a percentage like 99.0%",
    "secs": "seconds like 10s",
    "uri": "an http(s) URL",
    "money": "a number like 0.05",
    "trust": "a score from 1 to 5",
    "rfc3339": "an RFC 3339 UTC timestamp like 2026-06-04T12:00:00Z",
}

NATIVE_ROOT = "applicationProfile"
NATIVE_REQUIRED_SECTIONS = (
    "applicationProfile",
    "applicationProfile.metadata",
    "applicationProfile.specs",
    "applicationProfile.specs.runtime",
    "applicationProfile.specs.resources",
    "applicationProfile.specs.constraints",
)
DEVICE_REQUIRED_SECTIONS = ("metadata", "spec", "spec.app", "spec.workload")

_NM = "applicationProfile.metadata"
_NS = "applicationProfile.specs"

NATIVE_RULES: tuple[FieldRule, ...] = (
    _R(f"{_NM}.type", "str", True, enum=("native",)),
    _R(f"{_NM}.schemaVersion", "str", True, pattern=PATTERNS["semver"]),
    _R(f"{_NM}.name", "str", True),
    _R(f"{_NM}.version", "str", True, pattern=PATTERNS["semver"]),
    _R(f"{_NM}.description", "str"),
    _R(f"{_NM}.owner", "str", True),
    _R(f"{_NM}.lifecyclePhase", "str", True, enum=("development", "testing", "production")),
    _R(f"{_NM}.createdAt", "str", pattern=PATTERNS["rfc3339"]),
    _R(f"{_NM}.updatedAt", "str", pattern=PATTERNS["rfc3339"]),
    _R(f"{_NM}.annotations", "map"),
    _R(f"{_NS}.runtime.executionType", "str", True, enum=("container", "vm")),
    _R(f"{_NS}.runtime.entryPoint", "str", True),
    _R(f"{_NS}.runtime.args", "list", True),
    _R(f"{_NS}.runtime.baseOS.name", "str", True),
    _R(f"{_NS}.runtime.baseOS.version", "str", True),
    _R(f"{_NS}.runtime.containerImage.uri", "str", True),
    _R(f"{_NS}.runtime.containerImage.tag", "str", True),
    _R(f"{_NS}.runtime.hypervisor", "str", enum=("qemu", "kvm", "xen", "vmware")),
    _R(f"{_NS}.runtime.acceleratorRuntime", "list"),
    _R(f"{_NS}.resources.cpu", "str", True, pattern=PATTERNS["cpu_m"]),
    _R(f"{_NS}.resources.memory", "str", True, pattern=PATTERNS["mem"]),
    _R(f"{_NS}.resources.storage", "str", True, pattern=PATTERNS["mem"]),
    _R(f"{_NS}.resources.gpu", "int", lo=0),
    _R(f"{_NS}.resources.tpu", "int", lo=0),
    _R(f"{_NS}.resources.accelerators", "int", lo=0),
    _R(f"{_NS}.network.ports", "list"),
    _R(f"{_NS}.network.ports[].port", "int", True, lo=1, hi=65535),
    _R(f"{_NS}.network.ports[].protocol", "str", True),
    _R(f"{_NS}.network.ports[].publicExposure", "bool"),
    _R(f"{_NS}.network.protocols", "list"),
    _R(f"{_NS}.network.networkBandwidthMin", "str", pattern=PATTERNS["bw"]),
    _R(f"{_NS}.constraints.supportedArchitectures", "list", True),
    _R(f"{_NS}.constraints.trustScore", "strnum", pattern=PATTERNS["trust"]),
    _R(f"{_NS}.constraints.geoLocationRequirement", "str"),
    _R(f"{_NS}.constraints.isHighlyAvailable", "bool"),
    _R(f"{_NS}.constraints.faultTolerance", "str"),
    _R(f"{_NS}.constraints.securityLevel", "str"),
    _R(f"{_NS}.constraints.dataClassification", "str"),
    _R(f"{_NS}.qos.latencyToleranceMax", "str", pattern=PATTERNS["ms"]),
    _R(f"{_NS}.qos.energyCost", "str", pattern=PATTERNS["kwh"]),
    _R(f"{_NS}.qos.monetaryCost", "strnum", pattern=PATTERNS["money"]),
    _R(f"{_NS}.qos.resilience", "str"),
    _R(f"{_NS}.qos.availability", "str", pattern=PATTERNS["pct"]),
    _R(f"{_NS}.qos.startupTime", "str", pattern=PATTERNS["secs"]),
)

DEVICE_RULES: tuple[FieldRule, ...] = (
    _R("apiVersion", "str", True, enum=("hyper.ai/v1",)),
    _R("kind", "str", True, enum=("Application",)),
    _R("metadata.name", "str", True),
    _R("metadata.annotations", "map"),
    _R("spec.device_name", "str"),
    _R("spec.device_uuid", "str"),
    _R("spec.app.type", "str", True, enum=("device",)),
    _R("spec.app.schemaVersion", "str", True, pattern=PATTERNS["semver"]),
    _R("spec.app.name", "str", True),
    _R("spec.app.version", "str", True, pattern=PATTERNS["semver"]),
    _R("spec.app.owner", "str", True),
    _R("spec.app.lifecyclePhase", "str", True, enum=("development", "testing", "production")),
    _R("spec.app.description", "str"),
    _R("spec.workload.kind", "str", True, enum=("AndroidApk", "DockerImage", "esp32Binary")),
    _R("spec.exec.parameters", "map"),
    _R("spec.exec.command", "list"),
    _R("spec.exec.env", "map"),
    _R("spec.exec.workingDir", "str"),
    _R("spec.resources.cpu.value", "num"),
    _R("spec.resources.cpu.unit", "str", enum=("cores", "millicores")),
    _R("spec.resources.memory.value", "num"),
    _R("spec.resources.memory.unit", "str"),
    _R("spec.resources.storage.value", "num"),
    _R("spec.resources.storage.unit", "str"),
    _R("spec.resources.gpu", "int", lo=0),
    _R("spec.resources.tpu", "int", lo=0),
    _R("spec.resources.accelerators", "list"),
    _R("spec.network.ports", "list"),
    _R("spec.network.ports[].port", "int", True, lo=1, hi=65535),
    _R("spec.network.ports[].protocol", "str", True),
    _R("spec.network.networkBandwidthMin.value", "num", True),
    _R("spec.network.networkBandwidthMin.unit", "str", True, enum=("bps", "Kbps", "Mbps", "Gbps")),
    _R("spec.qos.latencyToleranceMax.value", "num", True),
    _R("spec.qos.latencyToleranceMax.unit", "str", True, enum=("ms", "s")),
    _R("spec.qos.energyCost.value", "num", True),
    _R("spec.qos.energyCost.unit", "str", True, enum=("mW", "W")),
    _R("spec.qos.monetaryCost.value", "num", True),
    _R("spec.qos.monetaryCost.currency", "str", True),
    _R("spec.qos.monetaryCost.per", "str", True, enum=("second", "minute", "hour", "day")),
    _R("spec.qos.resilience", "str"),
    _R("spec.qos.availability.value", "num", True, lo=0, hi=1),
    _R("spec.qos.availability.unit", "str", True, enum=("fraction",)),
    _R("spec.qos.startupTime.value", "num", True),
    _R("spec.qos.startupTime.unit", "str", True, enum=("ms", "s")),
    _R("spec.constraints.schedulingPriority", "int", True),
    _R("spec.constraints.supportedArchitectures", "list", True),
    _R("spec.constraints.geoLocationRequirement", "str", True),
    _R("spec.constraints.isHighlyAvailable", "bool", True),
    _R("spec.constraints.faultTolerance", "str", True),
    _R("spec.constraints.dataClassification", "str", True),
    _R("spec.constraints.trustScore", "num", lo=0),
    _R("spec.constraints.batteryLevelMin", "int", lo=0, hi=100),
    _R("spec.constraints.securityLevel", "str"),
    _R("spec.sensors[].sensorType", "str", True),
    _R("spec.sensors[].isActive", "bool", True),
    _R("spec.sensors[].taskStatus", "str", True, enum=("IDLE", "RUNNING", "SCHEDULED")),
    _R("spec.sensors[].sensorIDs", "list"),
    _R("spec.sensors[].platformInfo", "str"),
    _R("spec.runtime.baseOS.name", "str"),
    _R("spec.runtime.baseOS.version", "str"),
    _R("spec.runtime.hypervisor", "str"),
    _R("spec.runtime.acceleratorRuntime", "list"),
    _R("spec.logs_url", "str", pattern=PATTERNS["uri"]),
    _R("spec.metrics_url", "str", pattern=PATTERNS["uri"]),
    _R("spec.config", "map"),
)

DEVICE_WORKLOAD_RULES: dict[str, tuple[FieldRule, ...]] = {
    "DockerImage": (
        _R("spec.workload.dockerImage.image", "str", True),
        _R("spec.workload.dockerImage.imagePullPolicy", "str", enum=("Always", "IfNotPresent", "Never")),
        _R("spec.workload.dockerImage.imagePullSecretRef", "str"),
    ),
    "AndroidApk": (
        _R("spec.workload.androidApk.apkUrl", "str", True, pattern=PATTERNS["uri"]),
        _R("spec.workload.androidApk.packageName", "str", True),
        _R("spec.workload.androidApk.sha256", "str"),
        _R("spec.workload.androidApk.installMode", "str", enum=("install", "update")),
        _R("spec.workload.androidApk.launch", "map"),
    ),
    "esp32Binary": (
        _R("spec.workload.esp32Binary.binaryUrl", "str", True, pattern=PATTERNS["uri"]),
        _R("spec.workload.esp32Binary.chip", "str", True,
            enum=("esp32", "esp32s2", "esp32s3", "esp32c3", "esp32c6", "esp32h2")),
        _R("spec.workload.esp32Binary.flash.method", "str", True, enum=("serial", "ota")),
        _R("spec.workload.esp32Binary.sha256", "str"),
        _R("spec.workload.esp32Binary.flash.baudRate", "int", lo=1200),
        _R("spec.workload.esp32Binary.flash.port", "str"),
        _R("spec.workload.esp32Binary.flash.offset", "str"),
        _R("spec.workload.esp32Binary.flash.partition", "str"),
        _R("spec.workload.esp32Binary.flash.eraseFlash", "bool"),
    ),
}


def _ancestors(path: str) -> set[str]:
    parts = path.split(".")
    return {".".join(parts[:i]) for i in range(1, len(parts))}


def _known_prefixes(rules: tuple[FieldRule, ...], extra: tuple[str, ...]) -> tuple[str, ...]:
    known: set[str] = set()
    for r in rules:
        norm = r.path.replace("[]", "")
        known.add(norm)
        known.update(_ancestors(norm))
    for e in extra:
        known.add(e)
        known.update(_ancestors(e))
    return tuple(sorted(known))


NATIVE_KNOWN_PREFIXES: tuple[str, ...] = _known_prefixes(
    NATIVE_RULES,
    ("applicationProfile.status",),
)
DEVICE_KNOWN_PREFIXES: tuple[str, ...] = _known_prefixes(
    DEVICE_RULES + tuple(r for rs in DEVICE_WORKLOAD_RULES.values() for r in rs),
    ("status",),
)

FREE_FORM_PATHS: tuple[str, ...] = (
    "applicationProfile.metadata.annotations",
    "metadata.annotations",
    "spec.config",
    "spec.exec.parameters",
    "spec.exec.env",
    "spec.workload.androidApk.launch",
    "status",
    "applicationProfile.status",
)

REQUIRED_DEFAULTS: dict[str, dict[str, object]] = {
    "native": {
        "applicationProfile.metadata.type": "native",
        "applicationProfile.metadata.schemaVersion": "1.1.0",
        "applicationProfile.metadata.version": "1.0.0",
        "applicationProfile.metadata.owner": "HyperAI-User",
        "applicationProfile.metadata.lifecyclePhase": "development",
        "applicationProfile.metadata.name": "my-app",
        "applicationProfile.specs.runtime.executionType": "container",
        "applicationProfile.specs.runtime.args": [],
        "applicationProfile.specs.runtime.entryPoint": "/start",
        "applicationProfile.specs.runtime.baseOS.name": "debian",
        "applicationProfile.specs.runtime.baseOS.version": "bookworm-slim",
        "applicationProfile.specs.runtime.containerImage.tag": "latest",
        "applicationProfile.specs.resources.cpu": "500m",
        "applicationProfile.specs.resources.memory": "512Mi",
        "applicationProfile.specs.resources.storage": "1Gi",
        "applicationProfile.specs.network.ports[].protocol": "TCP",
        "applicationProfile.specs.constraints.supportedArchitectures": ["x86_64", "arm64"],
    },
    "device": {
        "apiVersion": "hyper.ai/v1",
        "kind": "Application",
        "spec.app.type": "device",
        "spec.app.schemaVersion": "1.0.0",
        "spec.app.version": "1.0.0",
        "spec.app.owner": "HyperAI-User",
        "spec.app.lifecyclePhase": "development",
        "spec.network.ports[].protocol": "HTTP",
        "spec.network.networkBandwidthMin.value": 1,
        "spec.network.networkBandwidthMin.unit": "Mbps",
        "spec.qos.latencyToleranceMax.value": 500,
        "spec.qos.latencyToleranceMax.unit": "ms",
        "spec.qos.energyCost.value": 1,
        "spec.qos.energyCost.unit": "W",
        "spec.qos.monetaryCost.value": 0.01,
        "spec.qos.monetaryCost.currency": "USD",
        "spec.qos.monetaryCost.per": "hour",
        "spec.qos.availability.value": 0.9,
        "spec.qos.availability.unit": "fraction",
        "spec.qos.startupTime.value": 5,
        "spec.qos.startupTime.unit": "s",
        "spec.constraints.schedulingPriority": 1,
        "spec.constraints.supportedArchitectures": ["amd64", "arm64"],
        "spec.constraints.geoLocationRequirement": "LocalZone",
        "spec.constraints.isHighlyAvailable": False,
        "spec.constraints.faultTolerance": "graceful-degradation",
        "spec.constraints.dataClassification": "internal",
    },
}

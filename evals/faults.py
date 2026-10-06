"""Fault-injection definitions for the repair suite (DP-EVAL section 5.4)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Fault:
    name: str
    apply: Callable[[str], str]  # rendered native profile text -> faulty text (raises ValueError when the target line is absent)


def _drop_owner(text: str) -> str:
    new, n = re.subn(r"(?m)^\s*owner:.*\n", "", text, count=1)
    if n == 0:
        raise ValueError("drop_owner: no owner line to remove")
    return new


def _bad_lifecycle(text: str) -> str:
    if 'lifecyclePhase: "development"' not in text:
        raise ValueError('bad_lifecycle: no lifecyclePhase: "development" line')
    return text.replace('lifecyclePhase: "development"', 'lifecyclePhase: "prod"', 1)


def _cpu_cores(text: str) -> str:
    if 'cpu: "500m"' not in text:
        raise ValueError('cpu_cores: no cpu: "500m" line')
    return text.replace('cpu: "500m"', 'cpu: "0.5 cores"', 1)


def _memory_gb(text: str) -> str:
    if 'memory: "512Mi"' not in text:
        raise ValueError('memory_gb: no memory: "512Mi" line')
    return text.replace('memory: "512Mi"', 'memory: "2GB"', 1)


def _port_string(text: str) -> str:
    match = re.search(r"port: (\d+)", text)
    if match is None:
        raise ValueError("port_string: no port line")
    return text[: match.start()] + 'port: "%s"' % match.group(1) + text[match.end() :]


def _version_short(text: str) -> str:
    match = re.search(r"(?m)^(\s*)version: \"1\.0\.0\"", text)
    if match is None:
        raise ValueError('version_short: no version: "1.0.0" line')
    return text[: match.start()] + match.group(1) + 'version: "1"' + text[match.end() :]


def _drop_tag(text: str) -> str:
    new, n = re.subn(r"(?m)^\s*tag:.*\n", "", text, count=1)
    if n == 0:
        raise ValueError("drop_tag: no tag line to remove")
    return new


def _drop_arch(text: str) -> str:
    new, n = re.subn(r"(?m)^\s*supportedArchitectures:.*\n", "", text, count=1)
    if n == 0:
        raise ValueError("drop_arch: no supportedArchitectures line to remove")
    return new


FAULTS: tuple[Fault, ...] = (
    Fault(name="drop_owner", apply=_drop_owner),
    Fault(name="bad_lifecycle", apply=_bad_lifecycle),
    Fault(name="cpu_cores", apply=_cpu_cores),
    Fault(name="memory_gb", apply=_memory_gb),
    Fault(name="port_string", apply=_port_string),
    Fault(name="version_short", apply=_version_short),
    Fault(name="drop_tag", apply=_drop_tag),
    Fault(name="drop_arch", apply=_drop_arch),
)

FAULT_BASE_REQUESTS: tuple[str, ...] = (
    "Create a deployment YAML for a service using the nginx Docker image",
    "create a yaml for redis",
    "deploy grafana as a native app",
)

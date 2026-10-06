"""Which profile kind a document, text blob, or user request is about."""
from __future__ import annotations

import re

import yaml

from hyperion.dsl.spec import ProfileKind

DEVICE_CUES = re.compile(
    r"\b(device app|device application|edge device|on a device|android|apk|esp32|esp-32|raspberry|iot (board|device)|microcontroller|firmware|sensor board|deploy(ed)? to (a|the) device)\b",
    re.I,
)
PROFILE_CUES = re.compile(
    r"\b(yaml|yml|app\.yaml|profile|manifest|deployment|deploy(ment)? file|application profile|native app|device app)\b",
    re.I,
)
IMAGE_CUES = re.compile(
    r"\b(docker|image|container|nginx|redis|postgres|mysql|mongo|httpd|apache|node|python|grafana|prometheus|mosquitto|rabbitmq|traefik|caddy|influxdb|mariadb|memcached|busybox|alpine|ubuntu|wordpress|minio|hello-world)\b",
    re.I,
)

_YAML_NAME = re.compile(r"\.ya?ml\b", re.I)
_SERVICE_WORDS = re.compile(r"\b(service|app|application|workload)\b", re.I)


def detect_kind_from_doc(doc: object) -> ProfileKind:
    """'native' if doc is a dict with key 'applicationProfile'; 'device' if dict with apiVersion == 'hyper.ai/v1' or kind == 'Application' or 'spec' containing 'app'; else 'unknown'."""
    if not isinstance(doc, dict):
        return "unknown"
    if "applicationProfile" in doc:
        return "native"
    if doc.get("apiVersion") == "hyper.ai/v1":
        return "device"
    if doc.get("kind") == "Application":
        return "device"
    spec = doc.get("spec")
    if isinstance(spec, dict) and "app" in spec:
        return "device"
    return "unknown"


def detect_kind_from_text(text: str) -> ProfileKind:
    """yaml.safe_load then detect_kind_from_doc; when the YAML does not parse, regex fallback."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        doc = None
        if re.search(r"^\s*applicationProfile\s*:", text, re.M):
            return "native"
        if re.search(r"^\s*apiVersion\s*:\s*hyper\.ai", text, re.M):
            return "device"
        return "unknown"
    return detect_kind_from_doc(doc)


def detect_kind_from_request(user_text: str) -> ProfileKind:
    """Which profile type the user's words ask for: 'device' when DEVICE_CUES match, else 'native'."""
    if DEVICE_CUES.search(user_text):
        return "device"
    return "native"


def looks_like_profile_request(user_text: str) -> bool:
    """True when the request is about an application profile / deployment YAML."""
    if _YAML_NAME.search(user_text):
        return True
    if PROFILE_CUES.search(user_text) and (
        IMAGE_CUES.search(user_text)
        or DEVICE_CUES.search(user_text)
        or _SERVICE_WORDS.search(user_text)
    ):
        return True
    return False

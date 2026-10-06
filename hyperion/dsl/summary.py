"""One- or two-sentence human summaries of a profile."""
from __future__ import annotations

import yaml

from hyperion.dsl.detect import detect_kind_from_doc

_UNKNOWN = "Not a recognised HyperAI profile."


def summarize_profile(text: str) -> str:
    """One or two sentences describing a profile."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return _UNKNOWN
    kind = detect_kind_from_doc(doc)
    if kind == "native" and isinstance(doc, dict):
        try:
            root = doc["applicationProfile"]
            meta = root["metadata"]
            specs = root["specs"]
            name = meta["name"]
            uri = specs["runtime"]["containerImage"]["uri"]
            tag = specs["runtime"]["containerImage"]["tag"]
            ports = specs["network"]["ports"]
            p0 = ports[0]
            cpu = specs["resources"]["cpu"]
            mem = specs["resources"]["memory"]
            return (
                f"Native app `{name}`: image {uri}:{tag}, "
                f"port {p0['port']}/{p0['protocol']}, {cpu} CPU, {mem} memory."
            )
        except (KeyError, TypeError, IndexError):
            return _UNKNOWN
    if kind == "device" and isinstance(doc, dict):
        try:
            spec = doc["spec"]
            name = spec["app"]["name"]
            wl_kind = spec["workload"]["kind"]
            wl = spec["workload"]
            if wl_kind == "DockerImage":
                what = wl["dockerImage"]["image"]
            elif wl_kind == "AndroidApk":
                what = wl["androidApk"]["packageName"]
            else:
                what = wl["esp32Binary"]["chip"]
            ports = spec["network"]["ports"]
            if ports:
                tail = f", port {ports[0]['port']}/{ports[0]['protocol']}."
            else:
                tail = "."
            return f"Device app `{name}`: {wl_kind} workload ({what}){tail}"
        except (KeyError, TypeError, IndexError):
            return _UNKNOWN
    return _UNKNOWN

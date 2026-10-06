"""Minimal, reviewable edits to profile text via patch ops."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal, Sequence

import yaml

from hyperion.dsl.detect import detect_kind_from_text
from hyperion.dsl.locate import find_value_span, parse_path


class PatchError(ValueError):
    """An op cannot be applied; str(e) is safe to show the user."""


@dataclass(frozen=True)
class PatchOp:
    op: Literal["set", "delete", "replace"]
    path: str | None = None           # set/delete: dotted path with [i]
    value: object = None              # set
    find: str | None = None           # replace: exact substring, must occur exactly once
    replace_with: str | None = None   # replace


FIELD_PATHS: dict[str, dict[str, list[str]]] = {
    "native": {
        "name": ["applicationProfile.metadata.name"],
        "version": ["applicationProfile.metadata.version"],
        "owner": ["applicationProfile.metadata.owner"],
        "description": ["applicationProfile.metadata.description"],
        "lifecycle": ["applicationProfile.metadata.lifecyclePhase"],
        "image": ["applicationProfile.specs.runtime.containerImage.uri"],
        "tag": ["applicationProfile.specs.runtime.containerImage.tag"],
        "port": ["applicationProfile.specs.network.ports[0].port"],
        "cpu": ["applicationProfile.specs.resources.cpu"],
        "memory": ["applicationProfile.specs.resources.memory"],
        "storage": ["applicationProfile.specs.resources.storage"],
    },
    "device": {
        "name": ["metadata.name", "spec.app.name"],
        "version": ["spec.app.version"],
        "owner": ["spec.app.owner"],
        "description": ["spec.app.description"],
        "lifecycle": ["spec.app.lifecyclePhase"],
        "image": ["spec.workload.dockerImage.image"],
        "port": ["spec.network.ports[0].port"],
    },
}


def ops_from_json(items: Sequence[dict]) -> list[PatchOp]:
    """Validate model-produced op dicts. Raises PatchError on any malformed item; at most 8 ops."""
    if not isinstance(items, (list, tuple)):
        raise PatchError("The patch must be a list of operations.")
    if len(items) > 8:
        raise PatchError("I can apply at most 8 edits at once.")
    ops: list[PatchOp] = []
    for item in items:
        if not isinstance(item, dict):
            raise PatchError("Each edit must be an object with an 'op' name.")
        op = item.get("op")
        if op == "set":
            path = item.get("path")
            if not isinstance(path, str) or not path or len(path) > 200:
                raise PatchError("A 'set' edit needs a 'path' string (max 200 chars).")
            if "value" not in item:
                raise PatchError("A 'set' edit needs a 'value'.")
            value = item["value"]
            if not isinstance(value, (str, int, float, bool, list, dict)):
                raise PatchError("A 'set' value must be a string, number, boolean, list or object.")
            ops.append(PatchOp(op="set", path=path, value=value))
        elif op == "delete":
            path = item.get("path")
            if not isinstance(path, str) or not path or len(path) > 200:
                raise PatchError("A 'delete' edit needs a 'path' string (max 200 chars).")
            ops.append(PatchOp(op="delete", path=path))
        elif op == "replace":
            find = item.get("find")
            with_ = item.get("with")
            if not isinstance(find, str) or not (1 <= len(find) <= 400):
                raise PatchError("A 'replace' edit needs a 'find' string (1-400 chars).")
            if not isinstance(with_, str) or len(with_) > 400:
                raise PatchError("A 'replace' edit needs a 'with' string (max 400 chars).")
            ops.append(PatchOp(op="replace", find=find, replace_with=with_))
        else:
            raise PatchError("Each edit must have an 'op' of 'set', 'delete' or 'replace'.")
    return ops


_YAML_SCALAR = re.compile(r"^[A-Za-z0-9_./:\-]+$")
_YAML_KEYWORDS = {"true", "false", "null", "yes", "no", "on", "off",
                  "True", "False", "Null", "Yes", "No", "On", "Off",
                  "TRUE", "FALSE", "NULL", "YES", "NO", "ON", "OFF"}


def _looks_like_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False
    except Exception:
        return False


def _literal(value: object, style: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return repr(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        if style == '"':
            return json.dumps(value, ensure_ascii=False)
        if style == "'":
            return "'" + value.replace("'", "''") + "'"
        if _YAML_SCALAR.match(value) and value not in _YAML_KEYWORDS and not _looks_like_number(value):
            return value
        return json.dumps(value, ensure_ascii=False)
    raise PatchError("That change would break the YAML structure.")


def _set_nested(doc: dict, segments: list[str | int], value: object, full_path: str) -> None:
    current: object = doc
    for seg in segments[:-1]:
        if isinstance(seg, int):
            if not isinstance(current, list) or seg < 0 or seg >= len(current):
                raise PatchError(f"The file has no entry at {full_path}.")
            current = current[seg]
        else:
            if isinstance(current, list):
                raise PatchError(f"The file has no entry at {full_path}.")
            if not isinstance(current, dict):
                raise PatchError(f"The file has no entry at {full_path}.")
            nxt = current.get(seg)
            if not isinstance(nxt, dict):
                nxt = {}
                current[seg] = nxt
            current = nxt
    last = segments[-1]
    if isinstance(last, int):
        if not isinstance(current, list) or last < 0 or last >= len(current):
            raise PatchError(f"The file has no entry at {full_path}.")
        current[last] = value
    else:
        if not isinstance(current, dict):
            raise PatchError(f"The file has no entry at {full_path}.")
        current[last] = value


def _delete_nested(doc: dict, segments: list[str | int], full_path: str) -> None:
    current: object = doc
    for seg in segments[:-1]:
        if isinstance(seg, int):
            if not isinstance(current, list) or seg < 0 or seg >= len(current):
                raise PatchError(f"The file has no entry at {full_path}.")
            current = current[seg]
        else:
            if not isinstance(current, dict) or seg not in current:
                return
            current = current[seg]
    last = segments[-1]
    if isinstance(last, int):
        if not isinstance(current, list) or last < 0 or last >= len(current):
            raise PatchError(f"The file has no entry at {full_path}.")
        del current[last]
    else:
        if isinstance(current, dict):
            current.pop(last, None)


def _reserialise(text: str, op: PatchOp) -> str:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        raise PatchError("That change would break the YAML structure.")
    if doc is None:
        doc = {}
    if not isinstance(doc, dict):
        raise PatchError("That change would break the YAML structure.")
    assert op.path is not None
    segments = parse_path(op.path)
    if not segments:
        raise PatchError(f"The file has no entry at {op.path}.")
    if op.op == "delete":
        _delete_nested(doc, segments, op.path)
    else:
        _set_nested(doc, segments, op.value, op.path)
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                          default_flow_style=False, width=1000)


def apply_ops(text: str, ops: Sequence[PatchOp]) -> str:
    """Apply in order. Raises PatchError."""
    try:
        original_doc = yaml.safe_load(text)
        original_parsed = True
    except yaml.YAMLError:
        original_doc = None
        original_parsed = False
    _ = original_doc
    for op in ops:
        if op.op == "replace":
            assert op.find is not None and op.replace_with is not None
            count = text.count(op.find)
            if count == 0:
                raise PatchError("I could not find that text exactly once in the file.")
            if count > 1:
                raise PatchError("That text appears more than once, so I cannot tell which one to change.")
            text = text.replace(op.find, op.replace_with, 1)
        elif op.op == "set" and op.path is not None and isinstance(op.value, (str, int, float, bool)):
            span = find_value_span(text, op.path)
            if span is not None:
                start, end, style = span
                text = text[:start] + _literal(op.value, style) + text[end:]
            else:
                text = _reserialise(text, op)
        elif op.op == "delete" and op.path is not None:
            text = _reserialise(text, op)
        elif op.op == "set":
            text = _reserialise(text, op)
        else:
            raise PatchError("Each edit must have an 'op' of 'set', 'delete' or 'replace'.")
    if original_parsed:
        try:
            yaml.safe_load(text)
        except yaml.YAMLError:
            raise PatchError("That change would break the YAML structure.")
    return text


_VALUE = r"[\"']?([^\s\"',;]+)[\"']?"
_IMG = r"([a-z0-9][a-z0-9._/\-]*)(?::([A-Za-z0-9._\-]+))?"


def deterministic_ops_from_request(user_text: str, doc_text: str) -> list[PatchOp]:
    """Regex-derived ops for 'change/set/update/make/use <field> to <value>'. [] when nothing matched."""
    kind = detect_kind_from_text(doc_text)
    if kind == "unknown":
        return []
    paths = FIELD_PATHS[kind]
    ops: list[PatchOp] = []

    m = re.search(
        r"\b(?:change|set|update|make|switch|use|put|modify)\b[^.]{0,40}?\b(port)\b[^\d]{0,12}(\d{1,5})",
        user_text, re.I,
    )
    if m:
        n = int(m.group(2))
        if 1 <= n <= 65535:
            for p in paths.get("port", []):
                ops.append(PatchOp(op="set", path=p, value=n))

    m = re.search(r"\b(?:rename|name)\b[^.]{0,30}?\b(?:to|as)\s+" + _VALUE, user_text, re.I)
    if m:
        from hyperion.dsl.slots import slug as _slug
        for p in paths.get("name", []):
            ops.append(PatchOp(op="set", path=p, value=_slug(m.group(1))))

    m = re.search(r"\bversion\b\s*(?:to|=|:)?\s*v?(\d+\.\d+\.\d+)", user_text, re.I)
    if m:
        for p in paths.get("version", []):
            ops.append(PatchOp(op="set", path=p, value=m.group(1)))

    m = re.search(r"\bowner\b\s*(?:to|=|:)\s*" + _VALUE, user_text, re.I)
    if m:
        for p in paths.get("owner", []):
            ops.append(PatchOp(op="set", path=p, value=m.group(1)))

    m = re.search(r"\b(?:lifecycle|phase|stage)\b[^.]{0,20}?\b(production|testing|development)\b", user_text, re.I)
    if m:
        for p in paths.get("lifecycle", []):
            ops.append(PatchOp(op="set", path=p, value=m.group(1).lower()))

    m = re.search(r"\bimage\b[^.]{0,20}?\b(?:to|=)\s*" + _IMG, user_text, re.I)
    if m:
        img, tag = m.group(1), m.group(2)
        if kind == "device":
            for p in paths.get("image", []):
                ops.append(PatchOp(op="set", path=p, value=f"{img}:{tag}" if tag else img))
        else:
            for p in paths.get("image", []):
                ops.append(PatchOp(op="set", path=p, value=img))
            if tag:
                for p in paths.get("tag", []):
                    ops.append(PatchOp(op="set", path=p, value=tag))

    m = re.search(r"\btag\b\s*(?:to|=)?\s*([A-Za-z0-9._\-]+)", user_text, re.I)
    if m and "tag" in paths:
        for p in paths.get("tag", []):
            ops.append(PatchOp(op="set", path=p, value=m.group(1)))

    m = re.search(r"\bcpu\b[^.]{0,20}?\b(?:to|=|of)?\s*(\d+)\s*(m|cores?)?", user_text, re.I)
    if m and "cpu" in paths:
        n = int(m.group(1))
        unit = (m.group(2) or "").lower()
        value = f"{n}m" if unit == "m" else f"{n * 1000}m"
        for p in paths.get("cpu", []):
            ops.append(PatchOp(op="set", path=p, value=value))

    m = re.search(r"\b(?:memory|ram)\b[^.]{0,20}?(\d+(?:\.\d+)?)\s*(mi|mib|mb|gi|gib|gb)\b", user_text, re.I)
    if m and "memory" in paths:
        from hyperion.dsl.slots import _normalise_size as _ns
        for p in paths.get("memory", []):
            ops.append(PatchOp(op="set", path=p, value=_ns(m.group(1), m.group(2))))

    m = re.search(r"\bstorage\b[^.]{0,20}?(\d+(?:\.\d+)?)\s*(mi|mib|mb|gi|gib|gb|ti|tb)\b", user_text, re.I)
    if m and "storage" in paths:
        from hyperion.dsl.slots import _normalise_size as _ns
        for p in paths.get("storage", []):
            ops.append(PatchOp(op="set", path=p, value=_ns(m.group(1), m.group(2))))

    return ops

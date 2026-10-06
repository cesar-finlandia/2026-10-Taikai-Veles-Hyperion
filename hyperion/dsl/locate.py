"""Dotted-path parsing and YAML source-location helpers."""
from __future__ import annotations

import re

import yaml

_SEGMENT = re.compile(r"([^\.\[\]]+)|(\[(\d+)\])")


def parse_path(path: str) -> list[str | int]:
    """'specs.network.ports[0].port' -> ['specs','network','ports',0,'port']."""
    out: list[str | int] = []
    for part in path.split("."):
        pos = 0
        while pos < len(part):
            m = _SEGMENT.match(part, pos)
            if m is None:  # pragma: no cover - defensive
                out.append(part[pos:])
                break
            if m.group(1) is not None:
                out.append(m.group(1))
            else:
                out.append(int(m.group(3)))
            pos = m.end()
    return out


def _compose(text: str):  # yaml.Node | None
    try:
        return yaml.compose(text)
    except yaml.YAMLError:
        return None


def _child(mapping, key: str):
    for k, v in mapping.value:
        if getattr(k, "value", None) == key:
            return k, v
    return None, None


def _walk(node, segments: list[str | int]) -> tuple[object | None, int | None]:
    """Follow segments; return (node, line) of deepest node found and its 1-based line."""
    current = node
    line: int | None = None
    if current is not None:
        mark = getattr(current, "start_mark", None)
        if mark is not None:
            line = mark.line + 1
    for seg in segments:
        if isinstance(seg, int):
            items = getattr(current, "value", None)
            if getattr(current, "id", "") != "sequence" or not isinstance(items, list):
                return None, line
            if seg < 0 or seg >= len(items):
                return None, line
            current = items[seg]
        else:
            if getattr(current, "id", "") != "mapping":
                return None, line
            _, child = _child(current, seg)
            if child is None:
                return None, line
            current = child
        mark = getattr(current, "start_mark", None)
        if mark is not None:
            line = mark.line + 1
    return current, line


def locate_line(text: str, path: str) -> int | None:
    """1-based line of the value (or, if the key is missing, of the nearest existing ancestor key) via yaml.compose; None if text does not parse."""
    node = _compose(text)
    if node is None:
        return None
    if not path:
        return None
    _, line = _walk(node, parse_path(path))
    return line


def find_value_span(text: str, path: str) -> tuple[int, int, str] | None:
    """(start_index, end_index, style) of the scalar value node at `path`; style is '"', "'" or '' (plain). None if absent or not a scalar."""
    node = _compose(text)
    if node is None or not path:
        return None
    target, _ = _walk(node, parse_path(path))
    if target is None or getattr(target, "id", "") != "scalar":
        return None
    start = getattr(target, "start_mark", None)
    end = getattr(target, "end_mark", None)
    if start is None or end is None:
        return None
    try:
        s, e = int(start.index), int(end.index)
    except (TypeError, ValueError):
        return None
    style = getattr(target, "style", None) or ""
    if style not in ('"', "'", ""):
        style = ""
    return (s, e, style)

"""Deterministic repair of validator findings."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Sequence

import yaml

from hyperion.dsl.detect import detect_kind_from_text
from hyperion.dsl.patch import PatchError, PatchOp, apply_ops
from hyperion.dsl.slots import slug
from hyperion.dsl.spec import (
    DEVICE_RULES,
    DEVICE_WORKLOAD_RULES,
    NATIVE_RULES,
    PATTERNS,
    REQUIRED_DEFAULTS,
)
from hyperion.issues import Issue

_INDEX = re.compile(r"\[\d+\]")


def _norm(path: str) -> str:
    return _INDEX.sub("[]", path)


def _leaf(path: str) -> str:
    last = path.split(".")[-1]
    return _INDEX.sub("", last)


@dataclass(frozen=True)
class RepairResult:
    text: str
    applied: tuple[str, ...]     # one human sentence per fix
    unfixed: tuple[Issue, ...]   # issues this function could not fix


def _pattern_name(pattern: str | None) -> str | None:
    for name, pat in PATTERNS.items():
        if pat == pattern:
            return name
    return None


def _fix_bad_format(name: str, value: object) -> object | None:
    s = value if isinstance(value, str) else str(value)
    st = s.strip()
    if name == "cpu_m":
        m = re.fullmatch(r"\d+", st)
        if m:
            n = int(m.group(0))
            return f"{n * 1000}m" if n <= 64 else f"{n}m"
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(cpu|cores?)\b", st, re.I)
        if m:
            return f"{int(round(float(m.group(1)) * 1000))}m"
        return None
    if name == "mem":
        m = re.fullmatch(r"(\d+)\s*(t|tb|tib)\b", st, re.I)
        if m:
            return f"{int(m.group(1))}Ti"
        m = re.fullmatch(r"(\d+)\s*(g|gb|gib)\b", st, re.I)
        if m:
            return f"{int(m.group(1))}Gi"
        m = re.fullmatch(r"(\d+)\s*(m|mb|mib)\b", st, re.I)
        if m:
            return f"{int(m.group(1))}Mi"
        if re.fullmatch(r"\d+", st):
            return f"{int(st)}Mi"
        return None
    if name == "pct":
        m = re.fullmatch(r"(\d+(?:\.\d+)?)", st)
        if m:
            f = float(m.group(1))
            if 0 < f < 1:
                return f"{int(round(f * 100))}%"
            return f"{m.group(1)}%"
        return None
    if name == "secs":
        if re.fullmatch(r"\d+", st):
            return f"{int(st)}s"
        return None
    if name == "ms":
        if re.fullmatch(r"\d+", st):
            return f"{int(st)}ms"
        return None
    if name == "semver":
        if re.fullmatch(r"\d+", st):
            return f"{st}.0.0"
        if re.fullmatch(r"\d+\.\d+", st):
            return f"{st}.0"
        return None
    if name == "bw":
        if re.fullmatch(r"\d+", st):
            return f"{int(st)}Mbps"
        return None
    return None


def _fix_wrong_type(rule_type: str, pattern: str | None, value: object) -> object | None:
    if rule_type == "int" and isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    if rule_type == "bool" and isinstance(value, str) and value.strip().lower() in ("true", "false", "yes", "no"):
        return value.strip().lower() in ("true", "yes")
    if rule_type == "str" and isinstance(value, (int, float, bool)) and not isinstance(value, bool):
        s = str(value)
        if _pattern_name(pattern) == "semver":
            fixed = _fix_bad_format("semver", s)
            if fixed is not None:
                s = str(fixed)
            elif isinstance(value, float) and float(value).is_integer():
                s = f"{int(value)}.0.0"
        return s
    if rule_type == "str" and isinstance(value, bool):
        return str(value)
    if rule_type == "list" and isinstance(value, (str, int, float, bool)):
        return [value]
    return None


def _image_base(doc: object, kind: str) -> str | None:
    try:
        if kind == "native" and isinstance(doc, dict):
            uri = doc["applicationProfile"]["specs"]["runtime"]["containerImage"].get("uri")
            if isinstance(uri, str) and uri:
                return uri.split(":")[0].split("/")[-1]
        if kind == "device" and isinstance(doc, dict):
            img = doc.get("spec", {}).get("workload", {}).get("dockerImage", {}).get("image")
            if isinstance(img, str) and img:
                return img.split(":")[0].split("/")[-1]
    except (KeyError, AttributeError, TypeError):
        return None
    return None


def deterministic_repair(text: str, issues: Sequence[Issue], *, kind_hint=None) -> RepairResult | None:
    """Fix what can be fixed deterministically. None when nothing changed."""
    kind = kind_hint or detect_kind_from_text(text)
    if kind == "unknown":
        return None
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict):
        return None
    rules = list(NATIVE_RULES if kind == "native" else DEVICE_RULES)
    for rs in DEVICE_WORKLOAD_RULES.values():
        rules.extend(rs)
    by_path = {_norm(r.path): r for r in rules}
    defaults = REQUIRED_DEFAULTS.get(kind, {})

    seen: set[tuple[str, str | None]] = set()
    ops: list[PatchOp] = []
    messages: list[str] = []
    unfixed: list[Issue] = []

    for issue in issues:
        if issue.severity != "error":
            unfixed.append(issue)
            continue
        key = (issue.code, issue.path)
        if key in seen:
            continue
        seen.add(key)
        path = issue.path or ""
        leaf = _leaf(path) if path else ""
        norm = _norm(path) if path else ""
        fixed = False

        if issue.code in ("missing_required", "missing_section"):
            if norm in defaults:
                value = defaults[norm]
                if leaf == "name" and isinstance(value, str):
                    base = _image_base(doc, kind)
                    value = slug(base) if base else value
                ops.append(PatchOp(op="set", path=path, value=value))
                msg = f"Filled `{leaf}` with the default {value!r}."
                if norm.endswith(".owner"):
                    msg += " - change it to your team name."
                messages.append(msg)
                fixed = True
        elif issue.code == "bad_enum" and path:
            rule = by_path.get(norm)
            if rule is not None and rule.enum:
                try:
                    cur = doc_value(doc, path)
                except KeyError:
                    cur = None
                lowered = [e.lower() for e in rule.enum]
                matches = difflib.get_close_matches(str(cur).lower(), lowered, n=1, cutoff=0.5)
                if matches:
                    new = rule.enum[lowered.index(matches[0])]
                    ops.append(PatchOp(op="set", path=path, value=new))
                    messages.append(f"Changed `{leaf}` from {cur!r} to {new!r}.")
                    fixed = True
        elif issue.code == "bad_format" and path:
            rule = by_path.get(norm)
            name = _pattern_name(rule.pattern if rule else None)
            if name is not None:
                try:
                    cur = doc_value(doc, path)
                except KeyError:
                    cur = None
                new = _fix_bad_format(name, cur)
                if new is not None:
                    ops.append(PatchOp(op="set", path=path, value=new))
                    messages.append(f"Changed `{leaf}` from {cur!r} to {new!r}.")
                    fixed = True
        elif issue.code == "wrong_type" and path:
            rule = by_path.get(norm)
            if rule is not None:
                try:
                    cur = doc_value(doc, path)
                except KeyError:
                    cur = None
                new = _fix_wrong_type(rule.type, rule.pattern, cur)
                if new is not None:
                    ops.append(PatchOp(op="set", path=path, value=new))
                    messages.append(f"Changed `{leaf}` from {cur!r} to {new!r}.")
                    fixed = True

        if not fixed:
            unfixed.append(issue)

    if not ops:
        return None
    try:
        new_text = apply_ops(text, ops)
    except PatchError:
        return RepairResult(text=text, applied=(), unfixed=tuple(issues))
    return RepairResult(text=new_text, applied=tuple(messages), unfixed=tuple(unfixed))


def doc_value(doc: object, path: str) -> object:
    """Resolve a concrete dotted path (with [i]) against plain dicts/lists."""
    from hyperion.dsl.locate import parse_path as _pp

    current = doc
    for seg in _pp(path):
        if isinstance(seg, int):
            if not isinstance(current, list) or seg < 0 or seg >= len(current):
                raise KeyError(path)
            current = current[seg]
        else:
            if not isinstance(current, dict) or seg not in current:
                raise KeyError(path)
            current = current[seg]
    return current

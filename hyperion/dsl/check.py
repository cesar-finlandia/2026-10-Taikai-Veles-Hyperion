"""Offline deterministic validator for HyperAI application profiles."""
from __future__ import annotations

import re

import yaml

from hyperion.dsl.detect import detect_kind_from_doc
from hyperion.dsl.locate import locate_line, parse_path
from hyperion.dsl.spec import (
    DEVICE_REQUIRED_SECTIONS,
    DEVICE_RULES,
    DEVICE_WORKLOAD_RULES,
    FREE_FORM_PATHS,
    NATIVE_REQUIRED_SECTIONS,
    PATTERNS,
    FORMAT_HINTS,
    NATIVE_RULES,
    FieldRule,
    ProfileKind,
)
from hyperion.issues import Issue

_ARTICLE = {
    "str": "a string",
    "int": "an integer",
    "num": "a number",
    "bool": "a boolean",
    "list": "a list",
    "map": "a mapping",
    "strnum": "a string or number",
}

_INDEX = re.compile(r"\[\d+\]")


def _norm(path: str) -> str:
    return _INDEX.sub("[]", path)


def _strip_indices(path: str) -> str:
    return _INDEX.sub("", path)


def _found_type(v: object) -> str:
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "mapping"
    if v is None:
        return "null"
    return type(v).__name__


def _type_ok(rule_type: str, v: object) -> bool:
    if rule_type == "str":
        return isinstance(v, str)
    if rule_type == "int":
        return isinstance(v, int) and not isinstance(v, bool)
    if rule_type == "num":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if rule_type == "bool":
        return isinstance(v, bool)
    if rule_type == "list":
        return isinstance(v, list)
    if rule_type == "map":
        return isinstance(v, dict)
    if rule_type == "strnum":
        return isinstance(v, (str, int, float)) and not isinstance(v, bool)
    return False  # pragma: no cover - unknown type


def _resolve(doc: object, path: str) -> tuple[bool, object]:
    """Resolve a dotted path (no '[]') against plain dicts/lists. Returns (found, value)."""
    current = doc
    for seg in parse_path(path):
        if isinstance(seg, int):
            if not isinstance(current, list) or seg < 0 or seg >= len(current):
                return False, None
            current = current[seg]
        else:
            if not isinstance(current, dict) or seg not in current:
                return False, None
            current = current[seg]
    return True, current


def _resolve_parents(doc: object, parent: str) -> list[tuple[str, dict]]:
    """Resolve a parent path that may contain '[]'; returns (concrete_path, dict_node) pairs."""
    if "[]" not in parent:
        found, node = _resolve(doc, parent)
        if found and isinstance(node, dict):
            return [(parent, node)]
        return []
    head, _, tail = parent.partition("[]")
    found, node = _resolve(doc, head) if head else (True, doc)
    if not found or not isinstance(node, list):
        return []
    out: list[tuple[str, dict]] = []
    for i, item in enumerate(node):
        if tail.startswith("."):
            sub = f"{head}[{i}]{tail}"
            out.extend(_resolve_parents(doc, sub))
        elif tail == "":
            if isinstance(item, dict):
                out.append((f"{head}[{i}]", item))
        # a non-dict list item under a '[]' parent cannot hold the leaf
    return out


def _check_value(rule: FieldRule, value: object, concrete: str) -> Issue | None:
    leaf = concrete.split(".")[-1]
    leaf = re.sub(r"\[\d+\]$", "", leaf)
    if not _type_ok(rule.type, value):
        return Issue(
            severity="error",
            code="wrong_type",
            message=f"`{leaf}` must be {_ARTICLE[rule.type]} (found {_found_type(value)}).",
            path=concrete,
        )
    if rule.pattern is not None and rule.type in ("str", "strnum"):
        text = value if isinstance(value, str) else str(value)
        if not re.match(rule.pattern, text):
            hint = None
            for name, pat in PATTERNS.items():
                if pat == rule.pattern:
                    hint = FORMAT_HINTS.get(name)
                    break
            expected = hint or "the required format"
            return Issue(
                severity="error",
                code="bad_format",
                message=f"`{leaf}` must be {expected} (found {value!r}).",
                path=concrete,
            )
    if rule.enum:
        if value not in rule.enum:
            return Issue(
                severity="error",
                code="bad_enum",
                message=f"`{leaf}` must be one of {', '.join(rule.enum)} (found {value!r}).",
                path=concrete,
            )
    if rule.lo is not None or rule.hi is not None:
        if isinstance(value, bool):
            num = None
        elif isinstance(value, (int, float)):
            num = value
        else:
            num = None
        if num is not None:
            lo_ok = rule.lo is None or num >= rule.lo
            hi_ok = rule.hi is None or num <= rule.hi
            if not (lo_ok and hi_ok):
                return Issue(
                    severity="error",
                    code="out_of_range",
                    message=f"`{leaf}` must be between {rule.lo} and {rule.hi} (found {value}).",
                    path=concrete,
                )
    return None


def _walk_collect_unknown(doc: object, prefix: str, is_known, out: list[str]) -> None:
    if isinstance(doc, dict):
        for key, val in doc.items():
            p = f"{prefix}.{key}" if prefix else str(key)
            stripped = _strip_indices(p)
            if is_known(stripped):
                pass
            elif any(stripped == f or stripped.startswith(f + ".") for f in FREE_FORM_PATHS):
                pass
            else:
                out.append(p)
                if len(out) >= 10:
                    return
            _walk_collect_unknown(val, p, is_known, out)
            if len(out) >= 10:
                return
    elif isinstance(doc, list):
        for i, item in enumerate(doc):
            _walk_collect_unknown(item, f"{prefix}[{i}]", is_known, out)
            if len(out) >= 10:
                return


def check_document(doc: object, kind: ProfileKind, *, text: str | None = None) -> list[Issue]:
    """Validate an already-parsed document; `text` is used only to attach line numbers."""
    issues: list[Issue] = []
    if kind == "unknown":
        return [
            Issue(
                severity="error",
                code="unknown_profile_kind",
                message="This is neither a native app profile (root key `applicationProfile`) "
                "nor a device app manifest (`apiVersion: hyper.ai/v1`, `kind: Application`).",
            )
        ]
    if not isinstance(doc, dict):
        return [
            Issue(severity="error", code="not_a_mapping", message="The document must be a mapping.")
        ]
    sections = NATIVE_REQUIRED_SECTIONS if kind == "native" else DEVICE_REQUIRED_SECTIONS
    missing_sections: list[str] = []
    for section in sections:
        found, node = _resolve(doc, section)
        if not found or not isinstance(node, dict):
            missing_sections.append(section)
            issues.append(
                Issue(
                    severity="error",
                    code="missing_section",
                    message=f"Missing required section `{section}`.",
                    path=section,
                )
            )

    def under_missing(rule_path: str) -> bool:
        return any(rule_path == s or rule_path.startswith(s + ".") for s in missing_sections)

    rules: list[FieldRule] = list(NATIVE_RULES if kind == "native" else DEVICE_RULES)
    if kind == "device":
        workload_kind = None
        spec = doc.get("spec")
        if isinstance(spec, dict):
            wl = spec.get("workload")
            if isinstance(wl, dict):
                k = wl.get("kind")
                if isinstance(k, str):
                    workload_kind = k
        if workload_kind in DEVICE_WORKLOAD_RULES:
            rules.extend(DEVICE_WORKLOAD_RULES[workload_kind])

    for rule in rules:
        if under_missing(rule.path):
            continue
        parent, _, leaf = rule.path.rpartition(".")
        if "[]" in leaf:
            continue  # no such rules; defensive
        parents = _resolve_parents(doc, parent) if parent else ([("", doc)] if isinstance(doc, dict) else [])
        for concrete_parent, node in parents:
            if leaf not in node:
                if rule.required:
                    issues.append(
                        Issue(
                            severity="error",
                            code="missing_required",
                            message=f"Missing required field `{leaf}` under `{concrete_parent or parent}`.",
                            path=f"{concrete_parent}.{leaf}" if concrete_parent else leaf,
                        )
                    )
                continue
            concrete = f"{concrete_parent}.{leaf}" if concrete_parent else leaf
            issue = _check_value(rule, node[leaf], concrete)
            if issue is not None:
                issues.append(issue)

    rule_paths = {_norm(r.path) for r in rules}
    if kind == "native":
        rule_paths.add("applicationProfile.status")
    else:
        rule_paths.add("status")

    def _is_known(stripped: str) -> bool:
        for r in rule_paths:
            if stripped == r:
                return True
            if r.startswith(stripped + "."):
                return True  # intermediate object on the way to a rule
            if stripped.startswith(r + "."):
                return True  # detail under a rule-typed leaf
        return False

    unknown_paths: list[str] = []
    _walk_collect_unknown(doc, "", _is_known, unknown_paths)
    for p in unknown_paths:
        issues.append(
            Issue(
                severity="warning",
                code="unknown_field",
                message=f"Unknown field `{p}` is not part of the specification.",
                path=p,
            )
        )

    if text is not None:
        located: list[Issue] = []
        for i in issues:
            if i.path:
                line = locate_line(text, i.path)
            else:
                line = None
            if line is None or i.line is not None:
                located.append(i)
            else:
                located.append(
                    Issue(severity=i.severity, code=i.code, message=i.message,
                          path=i.path, line=line, col=i.col, source=i.source)
                )
        issues = located
    issues.sort(key=lambda i: (i.line if i.line else 10**9, i.path or ""))
    return issues


def check_profile(text: str, *, kind_hint: ProfileKind | None = None) -> list[Issue]:
    """Parse and validate a profile. Returns issues sorted by (line or 10**9, path). Never raises."""
    if not text.strip():
        return [Issue(severity="error", code="empty_document", message="The file is empty.")]
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        problem = getattr(e, "problem", None)
        if mark is not None:
            msg = f"YAML syntax error: {problem.strip()}" if problem else "YAML syntax error."
            return [Issue(severity="error", code="yaml_syntax", message=msg,
                          line=mark.line + 1, col=mark.column + 1)]
        msg = f"YAML syntax error: {problem.strip()}" if problem else f"YAML syntax error: {e}"
        return [Issue(severity="error", code="yaml_syntax", message=msg)]
    if not isinstance(doc, dict):
        return [Issue(severity="error", code="not_a_mapping",
                      message="The document must be a mapping at the top level.")]
    kind = kind_hint or detect_kind_from_doc(doc)
    return check_document(doc, kind, text=text)

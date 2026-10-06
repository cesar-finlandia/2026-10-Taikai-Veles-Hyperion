"""IDE-shaped validation reports backed by the offline checker."""
from __future__ import annotations

import os

from hyperion.dsl.check import check_profile
from hyperion.dsl.detect import detect_kind_from_text
from hyperion.issues import Issue


def _item(issue: Issue) -> dict[str, object]:
    item: dict[str, object] = {"message": issue.message}
    if issue.line is not None:
        item["line"] = issue.line
    if issue.col is not None:
        item["col"] = issue.col
    if issue.path is not None:
        item["path"] = issue.path
    return item


def report_for(path: str, content: str) -> dict[str, object]:
    """IDE-shaped validation report: {'path','type','valid','errors','warnings'}."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".yaml", ".yml"):
        return {"path": path, "type": "unknown", "valid": True, "errors": [], "warnings": []}
    issues = check_profile(content)
    errors = [_item(i) for i in issues if i.severity == "error"]
    warnings = [_item(i) for i in issues if i.severity == "warning"]
    return {
        "path": path,
        "type": detect_kind_from_text(content),
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }

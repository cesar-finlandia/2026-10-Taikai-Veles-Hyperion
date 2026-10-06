"""Extract the first JSON object from messy model output."""
from __future__ import annotations
import json
import re
from typing import Any
from hyperion.llm.base import LLMBadJson


def _strip_fences(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1)
    return text


def _find_candidate(text: str, start: int) -> tuple[str, int] | None:
    """Find a {...} candidate starting search at `start`. Returns (candidate, next_search_pos)."""
    i = text.find("{", start)
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[i:j + 1], j + 1
    # Unclosed: append missing braces (up to 3)
    candidate = text[i:]
    missing = depth if 0 < depth <= 3 else 0
    if missing:
        candidate = candidate + "}" * missing
        return candidate, len(text)
    return None, len(text)


def _try_loads(s: str) -> dict[str, Any] | None:
    try:
        v = json.loads(s)
    except Exception:
        return None
    return v if isinstance(v, dict) else None


def extract_json(text: str) -> dict[str, Any]:
    """Return the first JSON object found in `text` (tolerating ``` fences, prose before/after, trailing
    commas, and single-quoted keys). Raises hyperion.llm.base.LLMBadJson when none parses to a dict."""
    work = _strip_fences(text.strip())
    pos = 0
    tried = 0
    while True:
        found = _find_candidate(work, pos)
        if found is None:
            break
        candidate, next_pos = found
        if candidate is None:
            break
        tried += 1
        r = _try_loads(candidate)
        if isinstance(r, dict):
            return r
        # Repair 1: trailing commas
        fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
        r = _try_loads(fixed)
        if isinstance(r, dict):
            return r
        # Repair 2: single quotes -> double quotes (only if no double quote present)
        if '"' not in candidate:
            sq = re.sub(r"'([^']*)'", r'"\1"', fixed)
            sq = re.sub(r"'([A-Za-z_][A-Za-z0-9_]*)'\s*:", r'"\1":', sq)
            r = _try_loads(sq)
            if isinstance(r, dict):
                return r
        pos = next_pos
        if tried > 20:
            break
    raise LLMBadJson("no JSON object in model output")

"""Deterministic fact extraction from user messages (DP-MEMORY §5.2)."""
from __future__ import annotations

import re

NAME_PATTERNS = [
    r"\bmy name(?:'s| is)\s+([A-Z][\w'’\-]{1,30}(?:\s+[A-Z][\w'’\-]{1,30})?)",
    r"\bcall me\s+([A-Z][\w'’\-]{1,30})",
    r"\bi(?:'m| am)\s+called\s+([A-Z][\w'’\-]{1,30})",
    r"\bi(?:'m| am)\s+named\s+([A-Z][\w'’\-]{1,30})",
]
NOTE_PATTERN = r"\bremember(?:\s+that)?\s+(.{3,200}?)(?:[.!?]|$)"

_COMPILED_NAMES = [re.compile(p, flags=re.IGNORECASE) for p in NAME_PATTERNS]
_COMPILED_NOTE = re.compile(NOTE_PATTERN, flags=re.IGNORECASE)
_ANCHORED_REMEMBER = re.compile(r"\s*remember\b", flags=re.IGNORECASE)


def _clean_name(raw: str) -> str:
    return raw.strip().rstrip(".,!?;:").strip()


def extract_facts(text: str) -> dict[str, str]:
    """Deterministic facts: {'name': ...} from name lead-ins, {'note': ...} from a
    leading 'remember (that) ...'. The store numbers notes as 'note:<n>'.

    The lead-in is matched case-insensitively but the captured name must start
    upper-case (a lower-case word is not a name). Returns {} when nothing matches.
    """
    found: dict[str, str] = {}
    if not text:
        return found
    for pat in _COMPILED_NAMES:
        m = pat.search(text)
        if m:
            name = _clean_name(m.group(1))
            if name and name[0].isupper():
                found["name"] = name
                break
    stripped = text.lstrip()
    if _ANCHORED_REMEMBER.match(text) is not None:
        m = _COMPILED_NOTE.search(stripped)
        if m and m.start() == 0:
            note = m.group(1).strip().rstrip(".,!?;:").strip()
            if note:
                found["note"] = note
    return found

"""Deterministic recall over a session: Q&A about the conversation itself and
pronoun / file-reference resolution (DP-MEMORY §5.3–§5.4)."""
from __future__ import annotations

import re

from hyperion.memory.budget import clip
from hyperion.memory.facts import NOTE_PATTERN
from hyperion.memory.models import Session

_NOTE_RE = re.compile(NOTE_PATTERN, flags=re.IGNORECASE)

_NAME_Q = [
    re.compile(r"\bwhat(?:'s| is)\s+my\s+name\b"),
    re.compile(r"\bdo you (?:remember|know)\s+my name\b"),
    re.compile(r"\bwho am i\b"),
]
_LAST_Q = [
    re.compile(
        r"\bwhat (?:did|was)\s+(?:i|my)\s+(?:just\s+)?"
        r"(?:ask|asked|say|said|type|typed|(?:last|previous)\s+(?:question|message))"
        r"(?!\s+you\s+to\s+remember\b)"
    ),
    re.compile(r"\bwhat did i (?:just )?(?:ask|say)(?!\s+you\s+to\s+remember\b)"),
]
_LAST_FILE_Q = [
    re.compile(r"\b(?:which|what)\s+file\s+(?:did|was|are|were)\s+(?:i|we)\b"),
    re.compile(r"\bwhat file (?:are we|am i) working on\b"),
]
_FILES_Q = [
    re.compile(r"\b(?:what|which)\s+files\b.{0,40}\b(?:created|create|made|make|touched|touch|edited|edit|changed|change)\b"),
    re.compile(r"\blist\s+(?:the\s+)?files\s+(?:we|you|i)\b"),
]
_NOTES_Q = [
    re.compile(r"\bwhat did i (?:ask|tell) you to remember\b"),
    re.compile(r"\bwhat do you remember\b"),
]
_RECAP_Q = re.compile(
    r"\b(?:summari[sz]e|recap|sum up)\b.{0,30}\b(?:conversation|chat|so far|we (?:said|did))\b"
)
_DECLINED_Q = re.compile(r"\bwhat did i (?:decline|reject|cancel)\b")

_EXPLICIT_FILE_RE = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,6}\b")
_EXPLICIT_NAMED_RE = re.compile(r"\b(?:folder named|file named|called)\b", flags=re.IGNORECASE)
_PRONOUN_RE = re.compile(
    r"\b(?:it|that file|this file|the file|that one|the same file|same file|"
    r"that folder|the folder|that directory)\b",
    flags=re.IGNORECASE,
)
_VERB_RE = re.compile(
    r"\b(?:delete|remove|edit|change|update|modify|open|show|read|validate|check|fix|"
    r"rename|add|set|make|rewrite|explain|why)\b",
    flags=re.IGNORECASE,
)
_TYPE_REF_RE = re.compile(
    r"\bthe\s+(?:yaml|yml|profile|manifest|app(?:lication)?(?: profile)?)(?:\s+file)?\b",
    flags=re.IGNORECASE,
)


def _note_keys(facts: dict[str, str]) -> list[str]:
    keys: list[str] = []

    def _num(k: str) -> int:
        try:
            return int(k.split(":", 1)[1])
        except (ValueError, IndexError):
            return 0

    keys = sorted([k for k in facts if k.startswith("note:")], key=_num)
    return keys


def answer_recall(session: Session, text: str) -> str | None:
    """A deterministic answer to a question about the conversation itself, or None
    when `text` is not such a question (§5.3; first match wins)."""
    t = " ".join(text.lower().split())
    # 1. Name question.
    if any(p.search(t) for p in _NAME_Q):
        name = session.facts.get("name")
        if name:
            return f"Your name is {name}."
        return "You have not told me your name yet."
    # 2. Last user message (the current message is not yet stored).
    if any(p.search(t) for p in _LAST_Q):
        if not session.turns:
            return "We have only just started - there is nothing earlier yet."
        return f"You asked: “{clip(session.turns[-1].user, 200)}”"
    # 3. Last file.
    if any(p.search(t) for p in _LAST_FILE_Q):
        if session.last_file:
            return f"The last file we worked on is `{session.last_file}`."
        return "We have not touched any file in this conversation yet."
    # 4. Files list.
    if any(p.search(t) for p in _FILES_Q):
        refs = sorted(session.files.values(), key=lambda r: r.ts, reverse=True)[:10]
        if not refs:
            return "We have not created or changed any files yet."
        lines = [
            f"- `{r.path}` ({r.kind}, {r.last_op}{' - deleted' if r.deleted else ''})"
            for r in refs
        ]
        return "\n".join(lines)
    # 5. Remembered notes.
    if any(p.search(t) for p in _NOTES_Q):
        lines: list[str] = []
        name = session.facts.get("name")
        if name:
            lines.append(f"- Your name is {name}.")
        for k in _note_keys(session.facts):
            lines.append(f"- {session.facts[k]}")
        if not lines:
            return "You have not asked me to remember anything yet."
        return "\n".join(lines)
    # 6. Remember acknowledgement (the store persists it in record_turn).
    if t.startswith("remember"):
        m = _NOTE_RE.search(text.strip())
        if m and m.start() == 0:
            note = m.group(1).strip().rstrip(".,!?;:").strip()
            if note:
                return f"Noted: {note}."
    # 7. Recap.
    if _RECAP_Q.search(t):
        out = ["Here is a recap of what you asked:"]
        for turn in session.turns[-5:]:
            out.append(f"- “{clip(turn.user, 120)}”")
        paths = [r.path for r in sorted(session.files.values(), key=lambda r: r.ts, reverse=True)]
        if paths:
            out.append(f"Files touched: {', '.join(paths)}.")
        return "\n".join(out)
    # 8. Declined.
    if _DECLINED_Q.search(t):
        if not session.declined:
            return "You have not declined anything."
        return "You declined:\n" + "\n".join(f"- {d}" for d in reversed(session.declined))
    return None


def pronoun_target(session: Session, text: str) -> str | None:
    """The file path an expression like 'it' / 'that file' / 'the yaml' refers to,
    or None (§5.4)."""
    # 1. An explicit path or name wins — nothing to resolve.
    if _EXPLICIT_FILE_RE.search(text):
        return None
    if _EXPLICIT_NAMED_RE.search(text):
        return None
    # 2. Pronoun + verb acting on a file.
    if _PRONOUN_RE.search(text) and _VERB_RE.search(text):
        last = session.last_file
        if not last:
            return None
        ref = session.files.get(last)
        if ref is not None and ref.deleted:
            return None
        return last
    # 3. Type reference: most recent non-deleted native/device file.
    if _TYPE_REF_RE.search(text):
        best: str | None = None
        best_ts = float("-inf")
        for ref in session.files.values():
            if ref.deleted or ref.kind not in ("native", "device"):
                continue
            if ref.ts >= best_ts:
                best_ts = ref.ts
                best = ref.path
        return best
    # 4. Otherwise None.
    return None

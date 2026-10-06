"""Free-writing control prompts for the headline ablation (DP-EVAL section 5.5)."""
from __future__ import annotations

import re

from hyperion.llm.base import Message

FREEWRITE_SYSTEM: str = (
    "You write HyperAI application profiles in YAML. Reply with the YAML only, in one ```yaml fenced block, no explanation."
)
FREEWRITE_RAG_SYSTEM: str = (
    "You write HyperAI application profiles in YAML using ONLY the field names and rules in the DOCUMENTATION. "
    "Reply with the YAML only, in one ```yaml fenced block, no explanation."
)

_FENCE_RE = re.compile(r"```(?:yaml|yml)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def freewrite_messages(request: str, context: str = "") -> list[Message]:
    """[system FREEWRITE_SYSTEM (or FREEWRITE_RAG_SYSTEM when context), user request (+ '\\n\\nDOCUMENTATION:\\n' + context)]."""
    if context:
        system = FREEWRITE_RAG_SYSTEM
        user = request + "\n\nDOCUMENTATION:\n" + context
    else:
        system = FREEWRITE_SYSTEM
        user = request
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def extract_yaml(text: str) -> str:
    """The first ```yaml / ```yml / ``` fenced block when present, else the whole text; stripped."""
    match = _FENCE_RE.search(text)
    if match is not None:
        return match.group(1).strip()
    return text.strip()

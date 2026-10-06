"""Injection filter (DP-GUARDRAILS §5.3)."""
from __future__ import annotations
import re

INJECTION_RULES = (
  ("ignore_instructions", r"\b(ignore|disregard|forget|override|bypass)\b.{0,30}\b(previous|prior|above|earlier|all|any|your|the|these)\b.{0,30}\b(instructions?|prompts?|rules?|guardrails?|guidelines?|restrictions?)\b"),
  ("forget_everything", r"\bforget\s+(everything|all)\b"),
  ("role_override", r"\b(you are now|from now on,? you are|you will now act|pretend (to be|you are|that you)|role-?play as)\b"),
  ("jailbreak", r"\b(jailbreak|developer mode|god mode|do anything now|dan mode|you are dan|unrestricted (ai|assistant|mode))\b"),
  ("reveal_prompt", r"\b(reveal|show|print|display|repeat|leak|dump|output|tell me|reply)\b.{0,25}\b(system|hidden|initial|original|internal)\s+(prompt|instructions?|message|rules?)\b"),
  ("your_prompt", r"\b(your|the)\s+(system prompt|initial prompt|hidden instructions)\b"),
  ("secret_exfil", r"\b(reveal|show|print|tell me|give me|leak|dump|display|disclose|what('s| is))\b.{0,20}\b(your|the team'?s?|legion1'?s?|the server'?s?)\s+(api[ _-]?key|secret|token|password|credentials?)\b"),
  ("env_exfil", r"\b(show|print|cat|read|open|display|dump|leak|reveal)\b.{0,30}(\.env\b|environment variables?|env vars?)"),
  ("shell", r"(\brm\s+-rf\b|\bsudo\s|\bchmod\s+777\b|curl\s+[^|]+\|\s*(ba)?sh|powershell\s+-enc)"),
)

_COMPILED = tuple((name, re.compile(pattern, re.IGNORECASE | re.DOTALL)) for name, pattern in INJECTION_RULES)


def injection_hit(text: str) -> str | None:
    """Name of the first matching rule (re.IGNORECASE | re.DOTALL on the text with whitespace collapsed), else None."""
    collapsed = " ".join(text.split())
    for name, rx in _COMPILED:
        if rx.search(collapsed):
            return name
    return None

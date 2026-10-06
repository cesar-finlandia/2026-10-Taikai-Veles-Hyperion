"""Smalltalk detection and replies (DP-GUARDRAILS §5.5)."""
from __future__ import annotations
import re
from typing import Literal

SmalltalkKind = Literal["greeting", "thanks", "bye", "identity", "help", "ack"]

_GREETING_RE = re.compile(r"^(hi|hello|hey|hola|howdy|yo|greetings|good (morning|afternoon|evening))( there| hyperion| everyone)?$")
_THANKS_RE = re.compile(r"^((ok|okay|great|cool|perfect)( )?)?(thanks|thank you|thx|ty|cheers|many thanks)( a lot| so much| very much)?( hyperion)?$")
_BYE_RE = re.compile(r"^(bye|goodbye|see you|see ya|cya|good night|talk later)( hyperion)?$")
_IDENTITY_RE = re.compile(r"\bwho are you\b|\bwhat are you\b|\bwhat('s| is) your name\b|\bintroduce yourself\b|\btell me about yourself\b|\bare you (an? )?(ai|bot|robot|human|chatbot)\b")
_HELP_RE = re.compile(r"^help( me)?$|\bwhat can you do\b|\bhow can you help\b|\bwhat do you do\b|\byour capabilities\b|\bhow do i use you\b|\bwhat are your (features|capabilities)\b")
_ACK_RE = re.compile(r"^(ok|okay|cool|nice|great|got it|alright|fine|perfect|sounds good|understood)$")


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


def is_smalltalk(text: str) -> SmalltalkKind | None:
    t = _normalize(text)
    if not t:
        return None
    if len(t.split()) > 10:
        return None
    if _GREETING_RE.search(t):
        return "greeting"
    if _THANKS_RE.search(t):
        return "thanks"
    if _BYE_RE.search(t):
        return "bye"
    if _IDENTITY_RE.search(t):
        return "identity"
    if _HELP_RE.search(t):
        return "help"
    if _ACK_RE.search(t):
        return "ack"
    return None


def smalltalk_reply(kind: SmalltalkKind, user_name: str | None = None) -> str:
    """Literal replies in §5.5; ', <Name>' inserted after the greeting word / thanks when user_name is given."""
    suffix = f", {user_name}" if user_name else ""
    if kind == "greeting":
        return f"Hello{suffix}! I'm Hyperion, the assistant for the HyperAI IDE. Ask me about HyperAI, or tell me what you'd like to create, edit, validate or delete in your workspace."
    if kind == "thanks":
        return f"You're welcome{suffix}! Tell me if you want to change anything else."
    if kind == "bye":
        return f"Goodbye{suffix}! Come back any time."
    if kind == "identity":
        return "I'm Hyperion, the AI assistant built into the HyperAI IDE. I answer questions about the HYPER-AI project from its documentation, and I can create, edit, validate and delete files in your workspace - always asking before I overwrite or delete anything."
    if kind == "help":
        return "Here is what I can do:\n- Answer questions about HyperAI, the IDE and the native and device app specifications, citing the documentation.\n- Create application profiles, for example: \"Create a deployment YAML for a service using the nginx Docker image\".\n- Validate a file, explain its errors and fix them.\n- Edit or delete files and folders - I always ask for your confirmation first.\n- Remember what we discussed in this session."
    if kind == "ack":
        return "Okay. What would you like to do next?"
    raise ValueError(f"unknown smalltalk kind: {kind}")

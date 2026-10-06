"""Deterministic first-stage routing for a turn (DP-AGENT-CORE §5.2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from hyperion.actions.confirm import parse_confirmation
from hyperion.actions.intents import ActIntent, detect_act_intent, resolve_intent
from hyperion.guard.engine import Verdict
from hyperion.guard.smalltalk import is_smalltalk
from hyperion.memory.facts import extract_facts
from hyperion.memory.models import Session
from hyperion.memory.recall import answer_recall

from hyperion.agent.texts import FACT_NAME_REPLY

RouteKind = Literal["confirm", "recall", "fact", "act", "validate", "read", "guard"]
PostRoute = Literal["refuse", "smalltalk", "ask"]


@dataclass(frozen=True)
class PreRoute:
    kind: RouteKind
    decision: Literal["yes", "no"] | None = None
    intent: ActIntent | None = None
    reply: str | None = None  # fixed reply for kinds 'recall' and 'fact'


def pre_route(text: str, session: Session, *, has_pending: bool, max_chars: int) -> PreRoute:
    """Deterministic first-stage routing. See §5.2. Never raises, never calls a model."""
    try:
        t = text.strip()
        # 1. Empty or over the limit: the guard owns the refusal sentences.
        if t == "" or len(text) > max_chars:
            return PreRoute("guard")
        # 2. Confirmation words bypass the guard.
        d = parse_confirmation(t)
        if d in ("yes", "no"):
            if has_pending:
                return PreRoute("confirm", decision=d)  # type: ignore[arg-type]
            if is_smalltalk(t) == "ack":
                pass  # an "ok" with nothing pending is small talk; continue below
            else:
                return PreRoute("confirm", decision=d)  # type: ignore[arg-type]
        # 3. Deterministic recall.
        r = answer_recall(session, t)
        if r is not None:
            return PreRoute("recall", reply=r)
        # 4. Short name statement.
        try:
            facts = extract_facts(t)
        except Exception:
            facts = {}
        if "name" in facts and len(t.split()) <= 8:
            return PreRoute("fact", reply=FACT_NAME_REPLY.format(name=facts["name"]))
        # 5. Act intent.
        try:
            intent = detect_act_intent(t)
        except Exception:
            intent = None
        if intent is not None:
            try:
                intent = resolve_intent(intent, session, t)
            except Exception:
                pass
            if intent is not None:
                if intent.verb == "validate":
                    return PreRoute("validate", intent=intent)
                if intent.verb in ("read", "explain_file"):
                    return PreRoute("read", intent=intent)
                return PreRoute("act", intent=intent)
        # 6. Everything else goes through the guard.
        return PreRoute("guard")
    except Exception:
        return PreRoute("guard")


def post_route(verdict: Verdict) -> PostRoute:
    """'refuse' for category off_topic / injection / empty / too_long; 'smalltalk' for category smalltalk; otherwise 'ask'."""
    try:
        if verdict.category in ("off_topic", "injection", "empty", "too_long"):
            return "refuse"
        if verdict.category == "smalltalk":
            return "smalltalk"
        if not verdict.allow:
            return "refuse"
        return "ask"
    except Exception:
        return "refuse"

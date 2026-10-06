"""Scope guard engine (DP-GUARDRAILS §5.4, §5.6)."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

from hyperion.config import Settings
from hyperion.context import TurnContext
from hyperion.llm.base import LLMBadJson, LLMLike, LLMUnavailable
from hyperion.guard.injection import injection_hit
from hyperion.guard.lexicon import ScopeScore, lexical_scope
from hyperion.guard.smalltalk import SmalltalkKind, is_smalltalk
from hyperion.guard import smalltalk as _smalltalk_mod

if TYPE_CHECKING:
    from hyperion.rag.retriever import Retriever

Category = Literal["in_scope", "smalltalk", "off_topic", "injection", "empty", "too_long"]
Via = Literal["rule", "retrieval", "followup", "llm", "fallback", "disabled"]


@dataclass(frozen=True)
class Verdict:
    allow: bool
    category: Category
    reason: str
    via: Via
    score: float
    smalltalk: SmalltalkKind | None = None


GUARD_SYSTEM = (
  "You are the scope guard of Hyperion, the assistant inside the HyperAI IDE.\n"
  "In scope: the HYPER-AI project and the HyperAI IDE; application profiles (native apps, device apps) and their YAML; containers, Docker, Kubernetes, deployment, edge, cloud and IoT computing; "
  "devices such as Android phones and ESP32 boards; creating, editing, validating and deleting files and folders in the IDE workspace; questions about this assistant itself.\n"
  "Out of scope: everything else, for example weather, news, sports, entertainment, recipes, jokes, poems, travel, shopping, health, legal or financial advice, politics, homework, general trivia, and general programming help unrelated to HyperAI.\n"
  "Reply with one JSON object: {\"in_scope\": true or false, \"reason\": \"<max 12 words>\"}\n"
  "Examples:\n"
  "Message: \"What is a lifecyclePhase?\" -> {\"in_scope\": true, \"reason\": \"HyperAI profile field\"}\n"
  "Message: \"Who won the football match yesterday?\" -> {\"in_scope\": false, \"reason\": \"sports\"}\n"
  "Message: \"How do I expose port 8080 for my container?\" -> {\"in_scope\": true, \"reason\": \"container networking\"}\n"
  "Message: \"Write me a poem about autumn\" -> {\"in_scope\": false, \"reason\": \"creative writing\"}\n"
  "Message: \"Can you explain what Docker Hub is?\" -> {\"in_scope\": true, \"reason\": \"container registry\"}\n"
  "Message: \"What should I cook tonight?\" -> {\"in_scope\": false, \"reason\": \"cooking\"}"
)

_FOLLOWUP_RE = re.compile(
    r"^(and|also|what about|how about|why|so|then|ok(ay)? but|but|what if|how|can you|could you|please|now|next|the|that|this|it)\b",
    re.IGNORECASE,
)


class Guard:
    def __init__(self, settings: Settings, llm: LLMLike, retriever: "Retriever | None" = None) -> None:
        self._settings = settings
        self._llm = llm
        self._retriever = retriever

    async def check(self, text: str, *, turn: TurnContext, followup_ok: bool = False) -> Verdict:
        """Decide whether the message is in scope. See §5.4. Never raises; always records turn.trace.add('guard', ...)."""
        lex = ScopeScore(domain_hits=(), offtopic_hits=())
        support = 0.0
        verdict: Verdict | None = None
        try:
            t = " ".join(text.split())
            if t == "":
                verdict = Verdict(False, "empty", "empty message", "rule", 1.0)
            elif len(text) > self._settings.max_text_chars:
                verdict = Verdict(False, "too_long", f"too long > {self._settings.max_text_chars}", "rule", 1.0)
            else:
                hit = injection_hit(t)
                if hit is not None:
                    verdict = Verdict(False, "injection", f"rule:{hit}", "rule", 1.0)
                else:
                    kind = is_smalltalk(t)
                    if kind is not None:
                        verdict = Verdict(True, "smalltalk", kind, "rule", 1.0, smalltalk=kind)
                    elif not self._settings.feature_guard:
                        verdict = Verdict(True, "in_scope", "guard disabled", "disabled", 1.0)
                    else:
                        lex = lexical_scope(t)
                        if lex.domain >= 1 and lex.offtopic == 0:
                            verdict = Verdict(True, "in_scope", "lexical in-scope", "rule", min(1.0, 0.6 + 0.1 * lex.domain))
                        elif lex.domain >= 2:
                            verdict = Verdict(True, "in_scope", "lexical in-scope", "rule", 0.7)
                        elif lex.offtopic >= 1 and lex.domain == 0:
                            verdict = Verdict(False, "off_topic", "lexical off-topic", "rule", min(1.0, 0.7 + 0.1 * lex.offtopic))
                        else:
                            if followup_ok and lex.offtopic == 0 and len(t.split()) <= 14 and _FOLLOWUP_RE.search(t):
                                verdict = Verdict(True, "in_scope", "followup", "followup", 0.6)
                            else:
                                try:
                                    support = self._retriever.lexical_support(t) if self._retriever is not None else 0.0
                                except Exception:
                                    support = 0.0
                                if support >= 0.6 and lex.offtopic == 0:
                                    verdict = Verdict(True, "in_scope", "retrieval support", "retrieval", support)
                                else:
                                    verdict = await self._borderline(t, turn, lex, support)
        except Exception:
            allow = lex.domain >= 1 or support >= 0.5
            verdict = Verdict(
                allow,
                "in_scope" if allow else "off_topic",
                "fallback",
                "fallback",
                0.5,
            )
        assert verdict is not None
        try:
            turn.trace.add(
                "guard",
                ok=True,
                allow=verdict.allow,
                category=verdict.category,
                via=verdict.via,
                score=verdict.score,
                domain=lex.domain,
                offtopic=lex.offtopic,
                support=support,
            )
        except Exception:
            pass
        return verdict

    async def _borderline(self, t: str, turn: TurnContext, lex: ScopeScore, support: float) -> Verdict:
        llm = self._llm
        usable = llm is not None and bool(getattr(llm, "available", True))
        if usable and turn.take_llm_call():
            try:
                data = await llm.chat_json(
                    [
                        {"role": "system", "content": GUARD_SYSTEM},
                        {"role": "user", "content": f'Message: "{t[:400]}"'},
                    ],
                    name="scope",
                    turn=turn,
                    required_keys=("in_scope",),
                    max_tokens=60,
                )
            except (LLMUnavailable, LLMBadJson):
                pass
            except Exception:
                pass
            else:
                raw = data.get("in_scope")
                if isinstance(raw, str):
                    low = raw.strip().lower()
                    if low in ("true", "1", "yes", "y"):
                        in_scope = True
                    elif low in ("false", "0", "no", "n"):
                        in_scope = False
                    else:
                        in_scope = bool(low)
                else:
                    in_scope = bool(raw)
                reason = str(data.get("reason", ""))[:40]
                return Verdict(
                    in_scope,
                    "in_scope" if in_scope else "off_topic",
                    reason,
                    "llm",
                    0.8,
                )
        allow = lex.domain >= 1 or support >= 0.5
        return Verdict(
            allow,
            "in_scope" if allow else "off_topic",
            "fallback",
            "fallback",
            0.5,
        )

    def refusal_message(self, verdict: Verdict) -> str:
        """The literal refusal text for off_topic / injection / empty / too_long (§5.6)."""
        if verdict.category == "off_topic":
            return "I'm Hyperion, the assistant for the HyperAI IDE, so I can't help with that. I can answer questions about HyperAI, explain native and device app profiles, and create, edit, validate or delete files in your workspace. For example: \"What is a native app?\" or \"Create a deployment YAML for nginx.\""
        if verdict.category == "injection":
            return "I can't do that - I won't change my instructions or reveal internal configuration or credentials. I'm happy to help with HyperAI questions or your workspace files, though."
        if verdict.category == "empty":
            return "I didn't catch a question. Ask me about HyperAI, or tell me which file you'd like to create or change."
        if verdict.category == "too_long":
            return f"That message is longer than I can handle ({self._settings.max_text_chars} characters at most). Please shorten it or split it into parts."
        return "I'm Hyperion, the assistant for the HyperAI IDE, so I can't help with that. I can answer questions about HyperAI, explain native and device app profiles, and create, edit, validate or delete files in your workspace. For example: \"What is a native app?\" or \"Create a deployment YAML for nginx.\""

    def smalltalk_reply(self, kind: SmalltalkKind, user_name: str | None = None) -> str:
        return _smalltalk_mod.smalltalk_reply(kind, user_name)

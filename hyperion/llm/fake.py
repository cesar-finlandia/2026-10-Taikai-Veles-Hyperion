"""Scripted LLM for tests and offline evals."""
from __future__ import annotations
import copy
import math
import re
import zlib
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Literal, Sequence
from hyperion.context import TurnContext
from hyperion.llm.base import LLMBadJson, LLMBudgetExceeded, LLMStats, LLMLike, LLMUnavailable, Message

_TOKEN_RE = re.compile(r"\w+")


@dataclass
class CallRecord:
    name: str
    kind: str  # 'chat'|'stream'|'json'|'embed'
    messages: list[Message]


def _resolve(table: dict[str, Any], name: str, messages: Sequence[Message]) -> Any:
    if name not in table:
        raise LLMUnavailable(f"fake: no reply for {name}")
    entry = table[name]
    if isinstance(entry, list):
        if len(entry) > 1:
            return entry.pop(0)
        return entry[0]
    if callable(entry):
        return entry(list(messages))
    if isinstance(entry, BaseException):
        raise entry
    return entry


class FakeLLM:
    """Scripted LLM for tests and offline evals. Satisfies LLMLike."""
    stats: LLMStats
    calls: list[CallRecord]
    available: bool

    def __init__(self, *, chat: dict[str, Any] | None = None, json: dict[str, Any] | None = None,
                 stream: dict[str, Any] | None = None, embed_dim: int = 64, down: bool = False) -> None:
        """chat/json/stream map a prompt `name` to: a str/dict (always returned), a list (consumed in order,
        the last element repeats), a callable(messages)->value, or an Exception instance (raised).
        A name with no entry raises LLMUnavailable('fake: no reply for <name>')."""
        self._chat = dict(chat) if chat else {}
        self._json = dict(json) if json else {}
        self._stream = dict(stream) if stream else {}
        self._embed_dim = embed_dim
        self._down = down
        self.stats = LLMStats()
        self.calls = []
        self.available = not down

    def set_down(self, down: bool) -> None:
        self._down = down
        self.available = not down

    def _guard(self, turn: TurnContext | None) -> None:
        if self._down:
            raise LLMUnavailable("fake: down")
        if turn is not None and not turn.take_llm_call():
            raise LLMBudgetExceeded("fake: budget exceeded")

    async def chat(self, messages: Sequence[Message], *, name: str, turn: TurnContext | None = None,
                   temperature: float = 0.0, max_tokens: int = 512) -> str:
        self._guard(turn)
        reply = _resolve(self._chat, name, messages)
        if isinstance(reply, BaseException):
            raise reply
        self.calls.append(CallRecord(name=name, kind="chat", messages=list(messages)))
        self.stats.calls += 1
        return str(reply)

    async def stream(self, messages: Sequence[Message], *, name: str, turn: TurnContext | None = None,
                     temperature: float = 0.2, max_tokens: int = 700) -> AsyncIterator[str]:
        self._guard(turn)
        reply = _resolve(self._stream, name, messages)
        if isinstance(reply, BaseException):
            raise reply
        self.calls.append(CallRecord(name=name, kind="stream", messages=list(messages)))
        self.stats.calls += 1
        text = str(reply)
        for i in range(0, len(text), 12):
            yield text[i:i + 12]

    async def chat_json(self, messages: Sequence[Message], *, name: str, turn: TurnContext | None = None,
                        required_keys: Sequence[str] = (), max_tokens: int = 400) -> dict[str, Any]:
        self._guard(turn)
        reply = _resolve(self._json, name, messages)
        if isinstance(reply, BaseException):
            raise reply
        self.calls.append(CallRecord(name=name, kind="json", messages=list(messages)))
        self.stats.calls += 1
        if not isinstance(reply, dict):
            raise LLMBadJson(f"fake: reply for {name} is not a JSON object")
        out = copy.deepcopy(reply)
        missing = [k for k in required_keys if k not in out]
        if missing:
            raise LLMBadJson(f"fake: missing keys {missing}")
        return out

    async def embed(self, texts: Sequence[str], *, kind: Literal["query", "document"],
                    turn: TurnContext | None = None) -> list[list[float]]:
        if self._down:
            raise LLMUnavailable("fake: down")
        self.calls.append(CallRecord(name=f"embed:{kind}", kind="embed", messages=[]))
        self.stats.calls += 1
        self.stats.embed_calls += 1
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        dim = self._embed_dim
        vec = [0.0] * dim
        for tok in _TOKEN_RE.findall(text.lower()):
            vec[zlib.crc32(tok.encode()) % dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    async def ping(self) -> bool:
        return not self._down

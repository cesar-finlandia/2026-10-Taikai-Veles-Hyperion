"""The interface every model client satisfies, so tests can swap in FakeLLM."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Protocol, Sequence
from hyperion.context import TurnContext


class LLMUnavailable(RuntimeError):
    """The model server cannot be used right now (network, 5xx after retries, auth, budget)."""


class LLMBudgetExceeded(LLMUnavailable):
    """The per-turn LLM call budget is spent."""


class LLMBadJson(ValueError):
    """The model did not return a JSON object that satisfies required_keys, even after one retry."""


@dataclass
class LLMStats:
    calls: int = 0
    failures: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    embed_calls: int = 0


Message = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": "..."}


class LLMLike(Protocol):
    stats: LLMStats
    async def chat(self, messages: Sequence[Message], *, name: str, turn: TurnContext | None = None,
                   temperature: float = 0.0, max_tokens: int = 512) -> str: ...
    def stream(self, messages: Sequence[Message], *, name: str, turn: TurnContext | None = None,
               temperature: float = 0.2, max_tokens: int = 700) -> AsyncIterator[str]: ...
    async def chat_json(self, messages: Sequence[Message], *, name: str, turn: TurnContext | None = None,
                        required_keys: Sequence[str] = (), max_tokens: int = 400) -> dict[str, Any]: ...
    async def embed(self, texts: Sequence[str], *, kind: Literal["query", "document"],
                    turn: TurnContext | None = None) -> list[list[float]]: ...
    async def ping(self) -> bool: ...

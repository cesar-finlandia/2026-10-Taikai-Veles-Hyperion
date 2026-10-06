"""OpenAI-compatible client with retry/backoff, a global concurrency cap, a per-turn budget and metering."""
from __future__ import annotations
import asyncio
import logging
import math
import random
import time
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, Sequence
from hyperion.config import Settings
from hyperion.context import TurnContext
from hyperion.llm.base import LLMBadJson, LLMBudgetExceeded, LLMStats, LLMLike, LLMUnavailable, Message
from hyperion.llm.jsonparse import extract_json

_log = logging.getLogger(__name__)

try:
    from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
    _OPENAI_OK = True
except Exception:  # pragma: no cover
    _OPENAI_OK = False


def _exc_status(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    resp = getattr(exc, "response", None)
    if resp is not None:
        sc = getattr(resp, "status_code", None)
        if isinstance(sc, int):
            return sc
    return None


def _exc_headers(exc: BaseException) -> dict[str, str]:
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            return {k.lower(): v for k, v in resp.headers.items()}
        except Exception:
            return {}
    return {}


def _exc_message(exc: BaseException) -> str:
    for attr in ("message",):
        v = getattr(exc, attr, None)
        if isinstance(v, str) and v:
            return v
    body = getattr(exc, "body", None)
    try:
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
            if isinstance(err, str):
                return err
    except Exception:
        pass
    return str(exc)[:300]


def _retry_after_s(exc: BaseException) -> float | None:
    headers = _exc_headers(exc)
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        v = float(str(raw).strip().split(",")[0])
    except ValueError:
        return None
    if v < 0:
        return None
    return min(v, 20.0)


def _is_retryable(exc: BaseException) -> bool:
    if not _OPENAI_OK:
        return False
    if isinstance(exc, (RateLimitError, APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        status = _exc_status(exc)
        return status is not None and status >= 500
    status = _exc_status(exc)
    return status is not None and status >= 500


class LLMClient:
    """OpenAI-compatible client with retry/backoff, a global concurrency cap, a per-turn budget and metering."""
    stats: LLMStats

    def __init__(self, settings: Settings, *, http_client: "Any | None" = None,
                 sleep: "Callable[[float], Awaitable[None]] | None" = None) -> None:
        from openai import AsyncOpenAI
        self._settings = settings
        self._client = AsyncOpenAI(
            api_key=(settings.api_key or "missing-key"),
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_s,
            max_retries=0,
            http_client=http_client,
        )
        self._sem = asyncio.Semaphore(settings.llm_concurrency if settings.llm_concurrency >= 1 else 1)
        self._sleep = sleep or asyncio.sleep
        self.stats = LLMStats()
        self._json_mode_ok = True
        self._max_completion_param = True
        self._auth_failed_until = 0.0
        self._ping_cached_at = 0.0
        self._ping_cached_value = False

    def _enter(self, turn: TurnContext | None, name: str) -> None:
        s = self._settings
        if not s.api_key and "legion1" in (s.llm_base_url or ""):
            raise LLMUnavailable("no api key")
        if time.monotonic() < self._auth_failed_until:
            raise LLMUnavailable("auth failed recently")
        if turn is not None and not turn.take_llm_call():
            raise LLMBudgetExceeded(f"budget exceeded for {name}")

    def _backoff(self, attempt: int) -> float:
        base = self._settings.llm_backoff_base_s
        return min(8.0, base * (2 ** attempt)) * random.uniform(0.7, 1.3)

    def _auth_fail(self, exc: BaseException, status: int) -> LLMUnavailable:
        self._auth_failed_until = time.monotonic() + 30.0
        return LLMUnavailable(f"auth: {status}")

    async def _run_with_retry(self, factory: Callable[[], Awaitable[Any]], *, op: str) -> Any:
        s = self._settings
        attempts = max(0, s.llm_max_retries) + 1
        last_summary = "unknown error"
        for attempt in range(attempts):
            try:
                return await factory()
            except LLMBudgetExceeded:
                raise
            except LLMUnavailable:
                raise
            except Exception as exc:  # openai errors
                status = _exc_status(exc)
                msg = _exc_message(exc)
                low = msg.lower()
                # Compat: max_tokens param rejected
                if status == 400 and self._max_completion_param and (
                        "max_completion_tokens" in low or "max_tokens" in low):
                    self._max_completion_param = False
                    self.stats.retries += 1
                    continue
                # Compat: response_format rejected
                if status == 400 and self._json_mode_ok and "response_format" in low:
                    self._json_mode_ok = False
                    self.stats.retries += 1
                    continue
                if status in (401, 403):
                    raise self._auth_fail(exc, status)
                if (_is_retryable(exc) or status == 429) and attempt < attempts - 1:
                    wait = _retry_after_s(exc)
                    if wait is None:
                        wait = self._backoff(attempt)
                    self.stats.retries += 1
                    try:
                        await self._sleep(wait)
                    except asyncio.CancelledError:
                        raise
                    last_summary = msg[:200]
                    continue
                if status is not None and 400 <= status < 500:
                    raise LLMUnavailable(f"bad request: {status} {msg[:200]}")
                if (_is_retryable(exc) or status == 429):
                    last_summary = (f"{status} {msg[:200]}" if status else msg[:200])
                    break
                last_summary = (f"{status} {msg[:200]}" if status else msg[:200])
                break
        self.stats.failures += 1
        raise LLMUnavailable(last_summary)

    async def chat(self, messages: Sequence[Message], *, name: str, turn: TurnContext | None = None,
                   temperature: float = 0.0, max_tokens: int = 512) -> str:
        self._enter(turn, name)
        t0 = time.perf_counter()

        async def _do() -> Any:
            kw: dict[str, Any] = {"model": self._settings.llm_model, "messages": list(messages),
                                  "temperature": temperature}
            if self._max_completion_param:
                kw["max_completion_tokens"] = max_tokens
            else:
                kw["max_tokens"] = max_tokens
            async with self._sem:
                return await self._client.chat.completions.create(**kw)

        resp = await self._run_with_retry(_do, op=f"chat:{name}")
        try:
            content = resp.choices[0].message.content or ""
        except Exception:
            content = ""
        try:
            usage = getattr(resp, "usage", None)
            pt = int(getattr(usage, "prompt_tokens", 0) or 0)
            ct = int(getattr(usage, "completion_tokens", 0) or 0)
        except Exception:
            pt, ct = 0, 0
        self.stats.calls += 1
        self.stats.prompt_tokens += pt
        self.stats.completion_tokens += ct
        if turn is not None:
            try:
                turn.trace.add("llm", name=name, ms=(time.perf_counter() - t0) * 1000.0,
                               prompt_tokens=pt, completion_tokens=ct)
            except Exception:
                pass
        return content

    async def stream(self, messages: Sequence[Message], *, name: str, turn: TurnContext | None = None,
                     temperature: float = 0.2, max_tokens: int = 700) -> AsyncIterator[str]:
        self._enter(turn, name)
        s = self._settings
        attempts = max(0, s.llm_max_retries) + 1
        await self._sem.acquire()
        try:
            yielded_any = False
            total_chars = 0
            for attempt in range(attempts):
                try:
                    kw: dict[str, Any] = {"model": s.llm_model, "messages": list(messages),
                                          "temperature": temperature, "stream": True}
                    if self._max_completion_param:
                        kw["max_completion_tokens"] = max_tokens
                    else:
                        kw["max_tokens"] = max_tokens
                    chunks = await self._client.chat.completions.create(**kw)
                    async for chunk in chunks:
                        try:
                            delta = chunk.choices[0].delta.content
                        except Exception:
                            delta = None
                        if delta:
                            yielded_any = True
                            total_chars += len(delta)
                            yield delta
                    self.stats.calls += 1
                    self.stats.completion_tokens += math.ceil(total_chars / 3.5) if total_chars else 0
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if yielded_any:
                        raise LLMUnavailable("stream interrupted")
                    status = _exc_status(exc)
                    msg = _exc_message(exc)
                    low = msg.lower()
                    if status == 400 and self._max_completion_param and (
                            "max_completion_tokens" in low or "max_tokens" in low):
                        self._max_completion_param = False
                        self.stats.retries += 1
                        continue
                    if status in (401, 403):
                        raise self._auth_fail(exc, status)
                    if (_is_retryable(exc) or status == 429) and attempt < attempts - 1:
                        wait = _retry_after_s(exc)
                        if wait is None:
                            wait = self._backoff(attempt)
                        self.stats.retries += 1
                        await self._sleep(wait)
                        continue
                    if status is not None and 400 <= status < 500:
                        raise LLMUnavailable(f"bad request: {status} {msg[:200]}")
                    self.stats.failures += 1
                    raise LLMUnavailable((f"{status} {msg[:200]}" if status else msg[:200]))
        finally:
            self._sem.release()

    async def chat_json(self, messages: Sequence[Message], *, name: str, turn: TurnContext | None = None,
                        required_keys: Sequence[str] = (), max_tokens: int = 400) -> dict[str, Any]:
        msgs = list(messages)
        # Append trailing system line
        appended = "Reply with one JSON object only. No prose."
        found_system = None
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "system":
                found_system = i
                break
        if found_system is not None:
            m = dict(msgs[found_system])
            m["content"] = (m.get("content", "") + "\n" + appended).strip()
            msgs[found_system] = m
        else:
            msgs = [{"role": "system", "content": appended}] + msgs

        async def _once(ms: Sequence[Message]) -> str:
            kw: dict[str, Any] = {"model": self._settings.llm_model, "messages": list(ms),
                                  "temperature": 0.0}
            if self._max_completion_param:
                kw["max_completion_tokens"] = max_tokens
            else:
                kw["max_tokens"] = max_tokens
            if self._json_mode_ok:
                kw["response_format"] = {"type": "json_object"}
            async with self._sem:
                resp = await self._client.chat.completions.create(**kw)
            try:
                return resp.choices[0].message.content or ""
            except Exception:
                return ""

        last_error = "no JSON object in model output"
        for trial in (0, 1):
            if trial == 0:
                self._enter(turn, name)
                t0 = time.perf_counter()
                text = await self._run_with_retry(lambda: _once(msgs), op=f"chat_json:{name}")
                self.stats.calls += 1
                if turn is not None:
                    try:
                        turn.trace.add("llm", name=name, ms=(time.perf_counter() - t0) * 1000.0)
                    except Exception:
                        pass
            else:
                self._enter(turn, name)
                retry_msgs = list(msgs)
                # append user correction
                retry_msgs.append({"role": "user",
                                   "content": f"Your last reply was not valid. Problem: {last_error}. Reply with the JSON object only."})
                t0 = time.perf_counter()
                text = await self._run_with_retry(lambda ms2=retry_msgs: _once(ms2), op=f"chat_json:{name}")
                self.stats.calls += 1
                if turn is not None:
                    try:
                        turn.trace.add("llm", name=name, ms=(time.perf_counter() - t0) * 1000.0)
                    except Exception:
                        pass
            try:
                data = extract_json(text)
            except LLMBadJson as e:
                last_error = str(e)[:200]
                if trial == 1:
                    raise LLMBadJson(last_error)
                continue
            missing = [k for k in required_keys if k not in data]
            if missing:
                last_error = f"missing keys: {missing}"
                if trial == 1:
                    raise LLMBadJson(last_error)
                continue
            return data
        raise LLMBadJson(last_error)

    async def embed(self, texts: Sequence[str], *, kind: Literal["query", "document"],
                    turn: TurnContext | None = None) -> list[list[float]]:
        # Embeds do not consume the per-turn budget but do take the semaphore.
        out: list[list[float]] = []
        use_prefix = "nomic" in (self._settings.embed_model or "").lower()
        prefix = "search_query: " if kind == "query" else "search_document: "
        items = list(texts)
        for i in range(0, max(1, len(items)), 32):
            batch = items[i:i + 32] if items else []
            payload = [(prefix + t) if use_prefix else t for t in batch]

            async def _do(b=payload) -> Any:
                # NOTE: openai>=3 injects encoding_format=base64 into embeddings
                # requests even when omitted, which the legion1 litellm->ollama
                # proxy rejects. Use the low-level post() so only model+input
                # are sent.
                from openai.types import CreateEmbeddingResponse
                async with self._sem:
                    return await self._client.post(
                        "/embeddings",
                        body={"model": self._settings.embed_model, "input": b},
                        cast_to=CreateEmbeddingResponse,
                    )

            resp = await self._run_with_retry(_do, op=f"embed:{kind}")
            try:
                vecs = [list(d.embedding) for d in resp.data]
            except Exception:
                vecs = []
            self.stats.embed_calls += 1
            out.extend(vecs)
        return out

    async def ping(self) -> bool:
        now = time.monotonic()
        if now - self._ping_cached_at < 30.0 and (self._ping_cached_at > 0):
            return self._ping_cached_value
        try:
            await asyncio.wait_for(self._client.models.list(), timeout=5.0)
            ok = True
        except Exception:
            ok = False
        self._ping_cached_at = now
        self._ping_cached_value = ok
        return ok

    async def aclose(self) -> None:
        try:
            await self._client.close()
        except Exception:
            pass


def build_llm(settings: Settings) -> LLMLike:
    """LLMClient(settings). Always returns a client even with an empty key (calls then raise LLMUnavailable('no api key'))."""
    return LLMClient(settings)

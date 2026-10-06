"""LLMClient tests using httpx.MockTransport."""
import asyncio
import json
import httpx
import pytest
from hyperion.config import Settings
from hyperion.context import new_turn
from hyperion.llm.base import LLMBadJson, LLMBudgetExceeded, LLMUnavailable
from hyperion.llm.client import LLMClient
from tests.conftest import chat_ok, embed_ok, sse_ok


def _client(settings, transport, sleep=None):
    http = httpx.AsyncClient(transport=transport)
    kw = {}
    if sleep is not None:
        kw["sleep"] = sleep
    return LLMClient(settings, http_client=http, **kw)


def test_chat_ok_counts_tokens(settings):
    async def go():
        c = _client(settings, chat_ok("hello", prompt_tokens=3, completion_tokens=5))
        out = await c.chat([{"role": "user", "content": "hi"}], name="t")
        assert out == "hello"
        assert c.stats.calls == 1
        assert c.stats.prompt_tokens == 3
        assert c.stats.completion_tokens == 5
        await c.aclose()
    asyncio.run(go())


def test_429_then_ok_retries(settings):
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited", "type": "rate_limit"}})
        body = {"id": "x", "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
        return httpx.Response(200, json=body)
    async def go():
        c = _client(settings, httpx.MockTransport(handler))
        assert await c.chat([{"role": "user", "content": "hi"}], name="t") == "ok"
        assert c.stats.retries == 1
        await c.aclose()
    asyncio.run(go())


def test_retry_after_header_used(settings):
    seen = []
    async def sleep(v):
        seen.append(v)
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "slow", "type": "rate_limit"}},
                                  headers={"retry-after": "2"})
        body = {"id": "x", "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
        return httpx.Response(200, json=body)
    async def go():
        c = _client(settings, httpx.MockTransport(handler), sleep=sleep)
        assert await c.chat([{"role": "user", "content": "hi"}], name="t") == "ok"
        await c.aclose()
    asyncio.run(go())
    assert seen and abs(seen[0] - 2.0) < 1e-6


def test_401_no_retry_and_short_circuit(settings):
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"message": "bad key", "type": "auth"}})
    async def go():
        c = _client(settings, httpx.MockTransport(handler))
        with pytest.raises(LLMUnavailable):
            await c.chat([{"role": "user", "content": "hi"}], name="t")
        with pytest.raises(LLMUnavailable, match="auth failed recently"):
            await c.chat([{"role": "user", "content": "hi"}], name="t")
        assert calls["n"] == 1
        await c.aclose()
    asyncio.run(go())


def test_500_exhausts_to_unavailable(settings):
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(500, json={"error": {"message": "boom", "type": "server"}})
    async def go():
        c = _client(settings, httpx.MockTransport(handler))
        with pytest.raises(LLMUnavailable):
            await c.chat([{"role": "user", "content": "hi"}], name="t")
        assert calls["n"] == 3  # retries=2 -> 3 attempts
        assert c.stats.failures == 1
        await c.aclose()
    asyncio.run(go())


def test_json_mode_fallback_on_400(settings):
    def handler(request):
        payload = json.loads(request.content.decode() or "{}")
        if "response_format" in payload:
            return httpx.Response(400, json={"error": {"message": "response_format not supported", "type": "invalid"}})
        body = {"id": "x", "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"a": 1}'}, "finish_reason": "stop"}]}
        return httpx.Response(200, json=body)
    async def go():
        c = _client(settings, httpx.MockTransport(handler))
        assert await c.chat_json([{"role": "user", "content": "hi"}], name="t") == {"a": 1}
        assert c._json_mode_ok is False
        await c.aclose()
    asyncio.run(go())


def test_json_retry_once_on_bad_json(settings):
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        content = "not json at all" if calls["n"] == 1 else '{"a": 1}'
        body = {"id": "x", "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}
        return httpx.Response(200, json=body)
    async def go():
        c = _client(settings, httpx.MockTransport(handler))
        assert await c.chat_json([{"role": "user", "content": "hi"}], name="t", required_keys=["a"]) == {"a": 1}
        assert calls["n"] == 2
        await c.aclose()
    asyncio.run(go())


def test_stream_yields_deltas(settings):
    async def go():
        c = _client(settings, sse_ok(["hel", "lo"]))
        parts = [p async for p in c.stream([{"role": "user", "content": "hi"}], name="t")]
        assert "".join(parts) == "hello"
        await c.aclose()
    asyncio.run(go())


def test_embed_batches_and_nomic_prefix(settings):
    seen_inputs = []
    def handler(request):
        payload = json.loads(request.content.decode() or "{}")
        seen_inputs.append(list(payload.get("input", [])))
        data = [{"object": "embedding", "index": i, "embedding": [0.1, 0.2]} for i in range(len(payload.get("input", [])))]
        return httpx.Response(200, json={"object": "list", "data": data})
    async def go():
        c = _client(settings, httpx.MockTransport(handler))
        vecs = await c.embed([f"t{i}" for i in range(40)], kind="query")
        assert len(vecs) == 40
        assert c.stats.embed_calls == 2
        assert all(x.startswith("search_query: ") for batch in seen_inputs for x in batch)
        await c.aclose()
    asyncio.run(go())


def test_embed_no_prefix_for_other_model(settings):
    s = Settings(api_key="test-key", llm_base_url="http://llm.test/v1", embed_model="text-embedding-3-small")
    seen = []
    def handler(request):
        payload = json.loads(request.content.decode() or "{}")
        seen.append(list(payload.get("input", [])))
        data = [{"object": "embedding", "index": i, "embedding": [0.1]} for i in range(len(payload.get("input", [])))]
        return httpx.Response(200, json={"object": "list", "data": data})
    async def go():
        c = _client(s, httpx.MockTransport(handler))
        await c.embed(["hello"], kind="query")
        assert seen[0] == ["hello"]
        await c.aclose()
    asyncio.run(go())


def test_budget_exceeded(settings):
    async def go():
        c = _client(settings, chat_ok("hi"))
        s = Settings(api_key="k", llm_base_url="http://llm.test/v1", turn_llm_budget=1)
        turn = new_turn(s, "u", "hi")
        assert await c.chat([{"role": "user", "content": "x"}], name="t", turn=turn) == "hi"
        with pytest.raises(LLMBudgetExceeded):
            await c.chat([{"role": "user", "content": "x"}], name="t", turn=turn)
        await c.aclose()
    asyncio.run(go())


def test_no_key_unavailable():
    async def go():
        s = Settings(api_key="", llm_base_url="https://legion1.di.uoa.gr/v1")
        c = _client(s, chat_ok("hi"))
        with pytest.raises(LLMUnavailable, match="no api key"):
            await c.chat([{"role": "user", "content": "x"}], name="t")
        await c.aclose()
    asyncio.run(go())


def test_concurrency_cap():
    async def go():
        s = Settings(api_key="k", llm_base_url="http://llm.test/v1", llm_concurrency=2,
                     llm_max_retries=0, llm_backoff_base_s=0.0)
        in_flight = {"cur": 0, "max": 0}
        def handler(request):
            return None  # unused; async below
        async def ahandler(request):
            in_flight["cur"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["cur"])
            await asyncio.sleep(0.05)
            in_flight["cur"] -= 1
            body = {"id": "x", "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}
            return httpx.Response(200, json=body)
        transport = httpx.MockTransport(ahandler)
        c = _client(s, transport)
        outs = await asyncio.gather(*[c.chat([{"role": "user", "content": "x"}], name="t") for _ in range(3)])
        assert outs == ["ok"] * 3
        assert in_flight["max"] <= 2
        await c.aclose()
    asyncio.run(go())


def test_ping_cached(settings):
    calls = {"n": 0}
    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"object": "list", "data": []})
    async def go():
        c = _client(settings, httpx.MockTransport(handler))
        assert await c.ping() is True
        assert await c.ping() is True
        assert calls["n"] == 1
        await c.aclose()
    asyncio.run(go())

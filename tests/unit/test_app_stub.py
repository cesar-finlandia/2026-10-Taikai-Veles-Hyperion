"""App stub transport tests (framing only, never the stub sentence)."""
import json
import httpx
import pytest
from hyperion.app import create_app
from hyperion.config import Settings


def _app():
    return create_app(Settings(api_key=""))


async def _read(app, method="POST", path="/chat", json_body=None, raw=None, headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        if raw is not None:
            resp = await client.request(method, path, content=raw, headers=headers or {})
        elif json_body is not None:
            resp = await client.request(method, path, json=json_body, headers=headers or {})
        else:
            resp = await client.request(method, path, headers=headers or {})
        return resp


def test_health_ok():
    import asyncio
    async def go():
        resp = await _read(_app(), method="GET", path="/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "hyperion"
    asyncio.run(go())


def test_chat_stream_format_and_done():
    import asyncio
    async def go():
        resp = await _read(_app(), json_body={"user_id": "u1", "text": "hello"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = [ln for ln in resp.text.split("\n") if ln.strip()]
        assert lines
        for ln in lines:
            assert ln.startswith("data: ")
        assert lines[-1] == "data: [DONE]"
        assert sum(1 for ln in lines if ln == "data: [DONE]") == 1
        first = json.loads(lines[0][len("data: "):])
        assert "response" in first
    asyncio.run(go())


def test_chat_bad_json_still_done():
    import asyncio
    async def go():
        resp = await _read(_app(), raw=b"{not json", headers={"content-type": "application/json"})
        assert resp.status_code == 200
        assert resp.text.rstrip().endswith("data: [DONE]")
        assert resp.text.count("data: [DONE]") == 1
    asyncio.run(go())


def test_chat_cors_preflight():
    import asyncio
    async def go():
        resp = await _read(_app(), method="OPTIONS", path="/chat",
                           headers={"Origin": "http://localhost:5000",
                                    "Access-Control-Request-Method": "POST",
                                    "Access-Control-Request-Headers": "content-type"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "*"
    asyncio.run(go())


def test_chat_headers_no_cache():
    import asyncio
    async def go():
        resp = await _read(_app(), json_body={"user_id": "u", "text": "hi"})
        assert resp.headers.get("cache-control") == "no-cache"
        assert resp.headers.get("x-accel-buffering") == "no"
    asyncio.run(go())

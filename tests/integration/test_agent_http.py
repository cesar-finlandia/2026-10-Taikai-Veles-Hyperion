"""HTTP shell, debug endpoints and smoke script (DP-AGENT-CORE WU-AGENT-04)."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from hyperion.app import create_app
from hyperion.testing.agent_env import make_agent_env
from hyperion.testing.ide_simulator import parse_sse_lines
from hyperion.testing.serve import serve_in_thread

ROOT = Path(__file__).resolve().parents[2]
NGINX = "Create a deployment YAML for a service using the nginx Docker image"
EMPTY_REFUSAL = "I didn't catch a question. Ask me about HyperAI, or tell me which file you'd like to create or change."


def _app(**kw):
    ae = make_agent_env(**kw)
    return ae, create_app(ae.env.settings, agent=ae.agent)


async def _post(app, path="/chat", json_body=None, raw=None, headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        if raw is not None:
            return await client.post(path, content=raw, headers=headers or {})
        return await client.post(path, json=json_body or {})


async def test_chat_streams_nginx_create_over_http():
    ae, app = _app()
    with serve_in_thread(app) as url:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream(
                "POST", url + "/chat", json={"user_id": "u1", "text": NGINX}
            ) as resp:
                assert resp.status_code == 200
                chunks: list[str] = []
                first_before_done = False
                async for chunk in resp.aiter_text():
                    chunks.append(chunk)
                    if "data:" in "".join(chunks) and "[DONE]" not in "".join(chunks):
                        first_before_done = True
                body = "".join(chunks)
        assert first_before_done
        text, actions, done = parse_sse_lines(body)
        assert done
        creates = [a for a in actions if a.get("action") == "create_file"]
        assert len(creates) == 1


async def test_chat_sse_headers_and_cors():
    _ae, app = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post("/chat", json={"user_id": "u", "text": "hello"})
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["cache-control"] == "no-cache"
        assert resp.headers["x-accel-buffering"] == "no"
        pre = await client.options(
            "/chat",
            headers={
                "Origin": "http://localhost:5000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert pre.status_code == 200
        assert pre.headers.get("access-control-allow-origin") == "*"


async def test_chat_bad_json_and_missing_user_id():
    _ae, app = _app()
    resp = await _post(app, raw=b"not json", headers={"content-type": "application/json"})
    assert resp.status_code == 200
    assert resp.text.rstrip().endswith("data: [DONE]")
    assert resp.text.count("data: [DONE]") == 1
    resp2 = await _post(app, json_body={"text": "hello"})
    assert resp2.status_code == 200
    text, _actions, done = parse_sse_lines(resp2.text)
    assert done and "Hello" in text


async def test_chat_missing_text_is_polite():
    _ae, app = _app()
    resp = await _post(app, json_body={"user_id": "u"})
    text, _actions, done = parse_sse_lines(resp.text)
    assert done
    assert text == EMPTY_REFUSAL


async def test_two_concurrent_requests_different_users():
    _ae, app = _app()
    r1, r2 = await asyncio.gather(
        _post(app, json_body={"user_id": "u1", "text": "hello"}),
        _post(app, json_body={"user_id": "u2", "text": "hello"}),
    )
    for resp in (r1, r2):
        assert resp.status_code == 200
        assert resp.text.count("data: [DONE]") == 1
        assert resp.text.rstrip().endswith("data: [DONE]")


async def test_debug_turns_returns_trace_without_key():
    ae, app = _app(api_key="sk-test-123456")
    await _post(app, json_body={"user_id": "u1", "text": "hello"})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/debug/turns", params={"user_id": "u1", "limit": "5"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["turns"][0]["user_id"] == "u1"
        assert "sk-test-123456" not in resp.text


async def test_debug_disabled_returns_404():
    import dataclasses

    ae = make_agent_env()
    off = dataclasses.replace(ae.env.settings, debug_endpoints=False)
    app_debug_off = create_app(off, agent=ae.agent)
    transport = httpx.ASGITransport(app=app_debug_off)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        assert (await client.get("/debug/turns")).status_code == 404
        assert (await client.get("/debug/status")).status_code == 404


async def test_debug_status_shape():
    _ae, app = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/debug/status")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("llm", "ide", "index", "sessions", "features"):
            assert key in body
        assert body["index"]["mode"] == "bm25"
        assert "sk-test" not in resp.text
        assert "test-key" not in json.dumps(body["llm"])


async def test_health_still_ok():
    _ae, app = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


async def test_smoke_script_against_served_app():
    _ae, app = _app()
    with serve_in_thread(app) as url:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "smoke_chat.py"), "--base", url],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(ROOT),
        )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ALL CHECKS PASSED" in proc.stdout

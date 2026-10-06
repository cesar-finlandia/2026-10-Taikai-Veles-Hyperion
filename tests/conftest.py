"""Shared fixtures for foundation tests."""
from __future__ import annotations
import json
import os
import pytest
import httpx
from hyperion.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        api_key="test-key",
        llm_base_url="http://llm.test/v1",
        landing_timeout_s=0.3,
        landing_interval_s=0.05,
        llm_max_retries=2,
        llm_backoff_base_s=0.0,
    )


@pytest.fixture
def ide_settings() -> Settings:
    return Settings(
        api_key="test-key",
        llm_base_url="http://llm.test/v1",
        ide_backend_url="http://ide.test/api",
        landing_timeout_s=0.3,
        landing_interval_s=0.05,
        llm_max_retries=2,
        llm_backoff_base_s=0.0,
    )


def pytest_collection_modifyitems(items):
    skip_live = pytest.mark.skip(reason="needs a real LLM key (API_KEY is empty)")
    if not os.environ.get("API_KEY"):
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_live)


def chat_ok(content: str, *, prompt_tokens: int = 3, completion_tokens: int = 5) -> httpx.MockTransport:
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                  "total_tokens": prompt_tokens + completion_tokens},
    }
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)
    return httpx.MockTransport(handler)


def sse_ok(parts: list[str]) -> httpx.MockTransport:
    lines = ""
    for p in parts:
        chunk = {"id": "chatcmpl-test", "object": "chat.completion.chunk",
                 "choices": [{"index": 0, "delta": {"content": p}, "finish_reason": None}]}
        lines += "data: " + json.dumps(chunk) + "\n\n"
    lines += "data: [DONE]\n\n"
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=lines.encode(), headers={"content-type": "text/event-stream"})
    return httpx.MockTransport(handler)


def embed_ok(n: int, *, dim: int = 4) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        try:
            payload = json.loads(request.content.decode() or "{}")
        except Exception:
            payload = {}
        inputs = payload.get("input", [])
        data = [{"object": "embedding", "index": i, "embedding": [0.5] * dim}
                for i, _ in enumerate(inputs if isinstance(inputs, list) else [])]
        return httpx.Response(200, json={"object": "list", "data": data, "model": "m"})
    return httpx.MockTransport(handler)

"""Robustness suite: twelve hostile or degraded conditions over HTTP (DP-EVAL section 5.4)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from hyperion.app import create_app
from hyperion.llm.fake import FakeLLM
from hyperion.testing.ide_simulator import parse_sse_lines
from hyperion.testing.serve import serve_in_thread

from evals.harness import EvalContext, SuiteResult, make_env


def _scenarios() -> list[dict[str, Any]]:
    return [
        {"name": "empty_text", "body": {"user_id": "r1", "text": ""}},
        {"name": "whitespace_text", "body": {"user_id": "r2", "text": "   \n\t "}},
        {"name": "huge_text", "body": {"user_id": "r3", "text": "word " * 3000}},
        {
            "name": "non_english",
            "body": {"user_id": "r4", "text": "¿Puedes crear un YAML de despliegue para nginx?"},
        },
        {"name": "cjk", "body": {"user_id": "r5", "text": "HyperAIとは何ですか"}},
        {"name": "malformed_json", "raw": "{not json"},
        {"name": "missing_user_id", "body": {"text": "hello"}},
        {"name": "numeric_fields", "body": {"user_id": 123, "text": 456}},
        {"name": "control_chars", "body": {"user_id": "r9", "text": "hello\x00\x07world"}},
        {
            "name": "emoji_create",
            "body": {"user_id": "r10", "text": "hello 👋 create a yaml for nginx"},
        },
        {"name": "same_user_parallel", "parallel": True},
        {
            "name": "llm_and_ide_down",
            "body": {"user_id": "r12", "text": "create a yaml for nginx"},
            "down": True,
        },
    ]


def _body_ok(status: int, raw: str) -> bool:
    if status != 200:
        return False
    if raw.count("data: [DONE]") != 1:
        return False
    text, _actions, saw_done = parse_sse_lines(raw)
    _ = text
    if not saw_done:
        return False
    if "Traceback" in raw or "Exception" in raw:
        return False
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        line = block.strip()
        if not line.startswith("data: "):
            continue
        data = line[len("data: ") :].strip()
        if data == "[DONE]":
            continue
        try:
            json.loads(data)
        except ValueError:
            return False
    return True


async def _post(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any]
) -> tuple[int, str]:
    if "raw" in payload:
        response = await client.post(
            url + "/chat",
            content=payload["raw"].encode("utf-8"),
            headers={"content-type": "application/json"},
        )
    else:
        response = await client.post(url + "/chat", json=payload.get("body", {}))
    return response.status_code, response.text


async def run(ctx: EvalContext) -> SuiteResult:
    """PASS iff all 12 scenarios return a well-formed stream ending in [DONE]."""
    ae = make_env(ctx.cfg, files={})
    app = create_app(ae.env.settings, agent=ae.agent)
    scenarios = _scenarios()
    passed = 0
    failures: list[dict[str, Any]] = []
    with serve_in_thread(app) as url:
        async with httpx.AsyncClient(timeout=120) as client:
            for scenario in scenarios:
                try:
                    ok = await asyncio.wait_for(
                        _run_scenario(client, url, ae, scenario), timeout=30.0
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    ok = False
                except Exception:
                    ok = False
                passed += 1 if ok else 0
                if not ok:
                    failures.append({"scenario": scenario["name"]})
    summary = f"scenarios={passed}/{len(scenarios)}"
    return SuiteResult(
        name="robustness",
        status="PASS" if passed == len(scenarios) else "FAIL",
        summary=summary,
        metrics={"scenarios": passed, "scenarios_n": len(scenarios)},
        details=failures,
        hard=True,
    )


async def _run_scenario(
    client: httpx.AsyncClient, url: str, ae: Any, scenario: dict[str, Any]
) -> bool:
    if scenario.get("down"):
        ae.env.backend.down = True
        llm = ae.env.llm
        if isinstance(llm, FakeLLM):
            llm.set_down(True)
        else:
            for holder in (ae.agent, ae.guard, ae.ask, ae.env.planner, ae.env.executor):
                try:
                    if getattr(holder, "_llm", None) is llm:
                        holder._llm = FakeLLM(down=True)  # noqa: SLF001 (eval wiring)
                except Exception:
                    pass
    if scenario.get("parallel"):
        first = {"user_id": "r11", "text": "create a yaml for nginx"}
        second = {"user_id": "r11", "text": "what is a native app?"}
        (s1, b1), (s2, b2) = await asyncio.gather(
            _post(client, url, {"body": first}),
            _post(client, url, {"body": second}),
        )
        ok = _body_ok(s1, b1) and _body_ok(s2, b2)
        # The shared workspace is untouched by HTTP turns; keep parity with soak.
        for body in (b1, b2):
            _text, actions, _done = parse_sse_lines(body)
            for payload in actions:
                try:
                    ae.env.workspace.apply(payload)
                except Exception:
                    pass
        return ok
    status, body = await _post(client, url, scenario)
    return _body_ok(status, body)

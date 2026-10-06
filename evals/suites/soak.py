"""Soak suite: concurrent multi-user script over HTTP, checking isolation (DP-EVAL section 5.4)."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

from hyperion.app import create_app
from hyperion.testing.ide_simulator import parse_sse_lines
from hyperion.testing.serve import serve_in_thread

from evals.harness import EvalContext, SuiteResult, make_env
from evals.metrics import percentile


def _script(i: int) -> list[str]:
    return [
        "hello",
        f"My name is User{i}",
        f"Create web{i}.yaml for nginx on port {8000 + i}",
        "what's my name?",
        f"delete web{i}.yaml",
        "yes",
        "What is a native app?",
    ]


def _has_name(text: str, i: int, users: int) -> tuple[bool, bool]:
    """(mentions own name, mentions another user's name) with word boundaries."""
    own = re.search(rf"\bUser{i}\b", text) is not None
    other = any(
        re.search(rf"\bUser{j}\b", text) is not None for j in range(users) if j != i
    )
    return own, other


async def _run_user(
    client: httpx.AsyncClient,
    url: str,
    ae: Any,
    i: int,
    turns: int,
    lock: asyncio.Lock,
) -> dict[str, Any]:
    user_id = f"soak-{i}"
    script = _script(i)[:turns] if turns < 7 else list(_script(i))
    done = 0
    turn4_text = ""
    durations: list[float] = []
    for step, text in enumerate(script):
        t0 = time.perf_counter()
        try:
            response = await client.post(url + "/chat", json={"user_id": user_id, "text": text})
            status, body = response.status_code, response.text
        except Exception:
            durations.append(time.perf_counter() - t0)
            continue
        durations.append(time.perf_counter() - t0)
        _txt, actions, saw_done = parse_sse_lines(body)
        stream_ok = (
            status == 200
            and saw_done
            and body.count("data: [DONE]") == 1
            and "Traceback" not in body
            and "Exception" not in body
        )
        if stream_ok:
            done += 1
        if step == 3:
            turn4_text = _txt
        async with lock:
            for payload in actions:
                try:
                    ae.env.workspace.apply(dict(payload))
                except Exception:
                    pass
    return {"done": done, "total": len(script), "turn4": turn4_text, "durations": durations}


async def run(ctx: EvalContext) -> SuiteResult:
    """PASS iff every stream completes, no cross-user leaks and no files are left."""
    users = ctx.cfg.users
    turns = ctx.cfg.turns
    ae = make_env(ctx.cfg, files={})
    app = create_app(ae.env.settings, agent=ae.agent)
    lock = asyncio.Lock()
    with serve_in_thread(app) as url:
        async with httpx.AsyncClient(timeout=120) as client:
            results = await asyncio.gather(
                *(_run_user(client, url, ae, i, turns, lock) for i in range(users))
            )
    completed = sum(r["done"] for r in results)
    total = users * turns
    leaks = 0
    for i, r in enumerate(results):
        if turns < 4:
            continue
        own, other = _has_name(r["turn4"], i, users)
        if not own or other:
            leaks += 1
    files_left = 0
    if turns >= 6:
        for i in range(users):
            if f"web{i}.yaml" in ae.env.workspace.files:
                files_left += 1
    durations = [d for r in results for d in r["durations"]]
    p95 = percentile(durations, 95)
    summary = (
        f"users={users} turns={turns} completed={completed}/{total} "
        f"leaks={leaks} files_left={files_left} p95_turn_s={p95:.2f}"
    )
    passed = completed == total and leaks == 0 and files_left == 0
    return SuiteResult(
        name="soak",
        status="PASS" if passed else "FAIL",
        summary=summary,
        metrics={
            "users": users,
            "turns": turns,
            "completed": completed,
            "completed_n": total,
            "leaks": leaks,
            "files_left": files_left,
            "p95_turn_s": p95,
        },
        details=[],
        hard=True,
    )

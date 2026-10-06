"""Simulates the IDE frontend: consumes a turn's events and applies the actions to a FakeWorkspace after a delay."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import AsyncIterator

from hyperion.events import ActionEvent, Event, TextEvent
from hyperion.testing.fake_ide import FakeWorkspace


@dataclass
class SimulatedTurn:
    text: str  # concatenated 'response' increments
    actions: list[dict[str, str]]  # action payloads in order
    events: list[tuple[str, object]]  # ('text', str) | ('action', dict) in arrival order
    done: bool = True
    duration_s: float = 0.0


class IdeSimulator:
    def __init__(self, workspace: FakeWorkspace, *, apply_delay_s: float = 0.15) -> None:
        self._workspace = workspace
        self._apply_delay_s = apply_delay_s

    async def _apply_later(self, payload: dict[str, str]) -> None:
        await asyncio.sleep(self._apply_delay_s)
        self._workspace.apply(payload)

    async def run_events(self, events: AsyncIterator[Event]) -> SimulatedTurn:
        """Iterate `events`; for every ActionEvent schedule workspace.apply(payload) after apply_delay_s (concurrently with the
        rest of the stream, so the agent's await_landing really races); await all scheduled applications before returning."""
        t0 = perf_counter()
        tasks: list[asyncio.Task[None]] = []
        parts: list[str] = []
        actions: list[dict[str, str]] = []
        ordered: list[tuple[str, object]] = []
        async for ev in events:
            if isinstance(ev, TextEvent):
                parts.append(ev.text)
                ordered.append(("text", ev.text))
            elif isinstance(ev, ActionEvent):
                actions.append(ev.payload)
                ordered.append(("action", ev.payload))
                tasks.append(asyncio.create_task(self._apply_later(ev.payload)))
        await asyncio.gather(*tasks)
        return SimulatedTurn(
            text="".join(parts),
            actions=actions,
            events=ordered,
            done=True,
            duration_s=perf_counter() - t0,
        )


def parse_sse_lines(raw: str) -> tuple[str, list[dict[str, str]], bool]:
    """Parse a full SSE body into (text, action_payloads, saw_done). Used by HTTP-level tests."""
    parts: list[str] = []
    actions: list[dict[str, str]] = []
    done = False
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        line = block.strip()
        if not line.startswith("data: "):
            continue
        data = line[len("data: "):].strip()
        if data == "[DONE]":
            done = True
            continue
        try:
            payload = json.loads(data)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("response"), str):
            parts.append(payload["response"])
        if "action" in payload:
            actions.append({k: v if isinstance(v, str) else str(v) for k, v in payload.items()})
    return ("".join(parts), actions, done)

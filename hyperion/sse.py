"""Server-Sent Events encoding for the Hyperion protocol."""
from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import AsyncIterator, Iterator
from hyperion.events import ActionEvent, Event, TextEvent

SSE_DONE: str = "data: [DONE]\n\n"

_log = logging.getLogger(__name__)


def sse_text(text: str) -> str:
    """'data: {"response": <text>}\\n\\n' using json.dumps(ensure_ascii=False)."""
    return "data: " + json.dumps({"response": text}, ensure_ascii=False) + "\n\n"


def sse_action(payload: dict[str, str]) -> str:
    """'data: <json of payload>\\n\\n' (payload already has the 'action' key)."""
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def event_to_sse(event: Event) -> str:
    """Dispatch TextEvent -> sse_text, ActionEvent -> sse_action."""
    if isinstance(event, TextEvent):
        return sse_text(event.text)
    if isinstance(event, ActionEvent):
        return sse_action(event.payload)
    raise TypeError(f"unknown event type: {type(event)!r}")


def chunk_text(text: str, max_chars: int = 40) -> list[str]:
    """Split on whitespace boundaries into pieces <= max_chars; concatenation of the pieces == text exactly."""
    if not text:
        return []
    pieces = re.findall(r"\S+\s*|\s+", text)
    out: list[str] = []
    cur = ""
    for p in pieces:
        if len(p) > max_chars:
            if cur:
                out.append(cur)
                cur = ""
            for i in range(0, len(p), max_chars):
                out.append(p[i:i + max_chars])
        elif len(cur) + len(p) > max_chars:
            out.append(cur)
            cur = p
        else:
            cur += p
    if cur:
        out.append(cur)
    return out


def text_events(text: str, max_chars: int = 40) -> Iterator[TextEvent]:
    """Yield one TextEvent per chunk_text() piece; yields nothing for an empty string."""
    for chunk in chunk_text(text, max_chars):
        yield TextEvent(text=chunk)


async def sse_stream(events: AsyncIterator[Event], *, on_error_text: str) -> AsyncIterator[str]:
    """Convert events to SSE strings. Never raises: on any exception log it, emit sse_text(on_error_text)
    once, then always finish with SSE_DONE exactly once. Empty text events are skipped."""
    try:
        try:
            async for ev in events:
                if isinstance(ev, TextEvent) and ev.text == "":
                    continue
                yield event_to_sse(ev)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("turn failed")
            yield sse_text(on_error_text)
    except asyncio.CancelledError:
        raise
    else:
        yield SSE_DONE

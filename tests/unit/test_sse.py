"""SSE encoding tests."""
import json
import pytest
from hyperion.events import ActionEvent, TextEvent
from hyperion.sse import (SSE_DONE, chunk_text, event_to_sse, sse_action, sse_stream, sse_text, text_events)


def test_sse_text_format():
    assert sse_text("hi") == 'data: {"response": "hi"}\n\n'


def test_sse_text_unicode_not_escaped():
    out = sse_text("γειά")
    assert "γειά" in out
    assert out.startswith("data: ")
    assert out.endswith("\n\n")
    assert json.loads(out[len("data: "):]) == {"response": "γειά"}


def test_sse_action_format():
    p = {"action": "create_file", "path": "a.yaml", "content": "x"}
    assert sse_action(p) == "data: " + json.dumps(p, ensure_ascii=False) + "\n\n"
    assert event_to_sse(ActionEvent(payload=p)) == sse_action(p)
    assert event_to_sse(TextEvent(text="t")) == sse_text("t")


def test_chunk_text_roundtrip():
    text = "hello world this is a test of chunking logic here"
    for mc in (5, 10, 40):
        assert "".join(chunk_text(text, mc)) == text
        assert all(len(c) <= mc for c in chunk_text(text, mc))


def test_chunk_text_hard_slice():
    chunks = chunk_text("abcdefghij", 3)
    assert chunks == ["abc", "def", "ghi", "j"]
    assert "".join(chunks) == "abcdefghij"


def test_text_events_empty():
    assert list(text_events("")) == []


async def _collect(gen):
    return [x async for x in gen]


async def _aiter(evs):
    for e in evs:
        yield e


def test_sse_stream_appends_done_once():
    async def go():
        return await _collect(sse_stream(_aiter([TextEvent(text="a"), TextEvent(text="")]),
                                        on_error_text="sorry"))
    import asyncio
    out = asyncio.run(go())
    assert out[-1] == SSE_DONE == "data: [DONE]\n\n"
    assert sum(1 for x in out if x == SSE_DONE) == 1
    assert out[0] == sse_text("a")
    assert len(out) == 2  # empty skipped, one text + DONE


def test_sse_stream_error_emits_apology_then_done():
    async def boom():
        yield TextEvent(text="ok")
        raise RuntimeError("bad")
    import asyncio
    async def go():
        return await _collect(sse_stream(boom(), on_error_text="sorry"))
    out = asyncio.run(go())
    assert out[0] == sse_text("ok")
    assert out[1] == sse_text("sorry")
    assert out[2] == SSE_DONE
    assert len(out) == 3

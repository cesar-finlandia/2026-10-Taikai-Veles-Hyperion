"""User locks, built-in overview and inputs (DP-AGENT-CORE WU-AGENT-01)."""
from __future__ import annotations

import asyncio

from hyperion.agent.inputs import parse_chat_body, sanitize_text
from hyperion.agent.locks import UserLocks
from hyperion.agent.overview import BUILTIN_CHUNKS, select_builtin_hits


async def test_same_user_serialised():
    locks = UserLocks()
    events: list[tuple[str, int]] = []

    async def worker(i: int) -> None:
        async with locks.hold("u"):
            events.append(("start", i))
            await asyncio.sleep(0.05)
            events.append(("end", i))

    t0 = asyncio.create_task(worker(0))
    await asyncio.sleep(0.01)
    t1 = asyncio.create_task(worker(1))
    await asyncio.gather(t0, t1)
    assert events == [("start", 0), ("end", 0), ("start", 1), ("end", 1)]


async def test_different_users_parallel():
    locks = UserLocks()
    inside_a = False
    inside_b = False

    async def worker_a() -> None:
        nonlocal inside_a
        async with locks.hold("a"):
            inside_a = True
            await asyncio.sleep(0.05)
            assert inside_b

    async def worker_b() -> None:
        nonlocal inside_b
        async with locks.hold("b"):
            inside_b = True
            await asyncio.sleep(0.01)
            assert inside_a

    await asyncio.gather(worker_a(), worker_b())
    assert inside_a and inside_b


async def test_lock_table_bounded():
    locks = UserLocks(max_users=3)
    for i in range(6):
        async with locks.hold(f"u{i}"):
            pass
    assert locks.size() <= 3
    # A held lock is never dropped to make room.
    async with locks.hold("keep"):
        assert "keep" in locks._locks  # noqa: SLF001 (test inspects the table)
        for i in range(6, 12):
            async with locks.hold(f"u{i}"):
                pass
        assert "keep" in locks._locks  # noqa: SLF001 (test inspects the table)


async def test_acquire_timeout_returns_false():
    locks = UserLocks()

    async def holder() -> None:
        async with locks.hold("u"):
            await asyncio.sleep(0.2)

    task = asyncio.create_task(holder())
    await asyncio.sleep(0.02)
    async with locks.hold("u", timeout=0.05) as got:
        assert got is False
    await task


def test_builtin_chunks_cover_core_topics():
    assert len(BUILTIN_CHUNKS) == 6
    for chunk in BUILTIN_CHUNKS:
        assert len(chunk.text) <= 700
    joined = "\n".join(c.text for c in BUILTIN_CHUNKS)
    assert "applicationProfile" in joined
    assert "hyper.ai/v1" in joined
    assert "Hyperion" in joined
    assert "Telefónica" in joined


def test_select_builtin_hits_ranks_native_question():
    hits = select_builtin_hits("What is in a native app profile?")
    assert hits
    assert hits[0].chunk.title == "Native apps"
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))


def test_select_builtin_hits_none_for_unrelated():
    assert select_builtin_hits("who won the football match") == []


def test_sanitize_text_strips_control_chars_and_truncates():
    assert sanitize_text("a\x00b\r\nc\x07d﻿e") == "ab\ncde"
    assert sanitize_text("a\x00b\r\nc\x07d﻿e", limit=3) == "ab\n"
    assert sanitize_text("a﻿b") == "ab"


def test_parse_chat_body_variants():
    assert parse_chat_body(b'{"user_id": "u1", "text": "hi"}') == ("u1", "hi")
    assert parse_chat_body(b'{"text": "hi"}') == ("anonymous", "hi")
    assert parse_chat_body(b"not json") == ("anonymous", "")
    assert parse_chat_body('"hello"') == ("anonymous", "hello")
    assert parse_chat_body("[1, 2]") == ("anonymous", "")
    assert parse_chat_body(b'{"message": "hi", "userId": 7}') == ("7", "hi")
    uid, _text = parse_chat_body(b'{"user_id": "' + b"u" * 200 + b'", "text": "hi"}')
    assert len(uid) <= 128

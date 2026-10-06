"""FakeLLM tests."""
import math
import pytest
from hyperion.config import Settings
from hyperion.context import new_turn
from hyperion.llm.base import LLMBadJson, LLMBudgetExceeded, LLMUnavailable
from hyperion.llm.fake import FakeLLM


def test_json_table_dict():
    async def go():
        f = FakeLLM(json={"n": {"a": 1}})
        out = await f.chat_json([{"role": "user", "content": "x"}], name="n")
        assert out == {"a": 1}
        out["a"] = 99  # copies
        assert (await f.chat_json([{"role": "user", "content": "x"}], name="n")) == {"a": 1}
    import asyncio
    asyncio.run(go())


def test_list_consumed_in_order():
    async def go():
        f = FakeLLM(chat={"n": ["one", "two", "two"]})
        msgs = [{"role": "user", "content": "x"}]
        assert await f.chat(msgs, name="n") == "one"
        assert await f.chat(msgs, name="n") == "two"
        assert await f.chat(msgs, name="n") == "two"  # last repeats
    import asyncio
    asyncio.run(go())


def test_callable_reply():
    async def go():
        f = FakeLLM(chat={"n": lambda msgs: f"got:{msgs[0]['content']}"})
        assert await f.chat([{"role": "user", "content": "hi"}], name="n") == "got:hi"
    import asyncio
    asyncio.run(go())


def test_exception_reply_raised():
    async def go():
        f = FakeLLM(chat={"n": LLMUnavailable("boom")})
        with pytest.raises(LLMUnavailable):
            await f.chat([{"role": "user", "content": "x"}], name="n")
    import asyncio
    asyncio.run(go())


def test_missing_name_unavailable():
    async def go():
        f = FakeLLM()
        with pytest.raises(LLMUnavailable, match="no reply"):
            await f.chat([{"role": "user", "content": "x"}], name="missing")
    import asyncio
    asyncio.run(go())


def test_required_keys_enforced():
    async def go():
        f = FakeLLM(json={"n": {"a": 1}})
        with pytest.raises(LLMBadJson):
            await f.chat_json([{"role": "user", "content": "x"}], name="n", required_keys=["zzz"])
    import asyncio
    asyncio.run(go())


def test_stream_slices():
    async def go():
        f = FakeLLM(stream={"n": "abcdefghijklmnopqrstuvwxyz"})
        parts = [p async for p in f.stream([{"role": "user", "content": "x"}], name="n")]
        assert "".join(parts) == "abcdefghijklmnopqrstuvwxyz"
        assert all(len(p) <= 12 for p in parts)
    import asyncio
    asyncio.run(go())


def test_embed_deterministic_and_normalised():
    async def go():
        f = FakeLLM(embed_dim=16)
        v1 = (await f.embed(["Hello world"], kind="query"))[0]
        v2 = (await f.embed(["Hello world"], kind="document"))[0]
        assert v1 == v2
        assert len(v1) == 16
        assert math.isclose(sum(x * x for x in v1), 1.0, rel_tol=1e-6)
    import asyncio
    asyncio.run(go())


def test_budget_enforced():
    async def go():
        f = FakeLLM(chat={"n": "hi"})
        s = Settings(turn_llm_budget=1)
        turn = new_turn(s, "u", "hi")
        assert await f.chat([{"role": "user", "content": "x"}], name="n", turn=turn) == "hi"
        with pytest.raises(LLMBudgetExceeded):
            await f.chat([{"role": "user", "content": "x"}], name="n", turn=turn)
    import asyncio
    asyncio.run(go())


def test_down_mode():
    async def go():
        f = FakeLLM(chat={"n": "hi"}, down=True)
        assert await f.ping() is False
        with pytest.raises(LLMUnavailable, match="down"):
            await f.chat([{"role": "user", "content": "x"}], name="n")
        f.set_down(False)
        assert await f.ping() is True
        assert await f.chat([{"role": "user", "content": "x"}], name="n") == "hi"
    import asyncio
    asyncio.run(go())

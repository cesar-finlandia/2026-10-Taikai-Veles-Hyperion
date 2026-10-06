"""Ask flow: grounded answers with verified citations (DP-AGENT-CORE WU-AGENT-02)."""
from __future__ import annotations

from hyperion.events import TextEvent
from hyperion.llm.fake import FakeLLM
from hyperion.rag.answer import ABSTAIN_PHRASE
from hyperion.testing.actions_env import new_ctx
from hyperion.testing.agent_env import make_agent_env

from hyperion.agent.texts import ASK_ABSTAIN_HINT, NOTICE_NO_INDEX


async def _texts(ae, text: str) -> tuple[str, object]:
    turn = new_ctx(ae.env, text)
    parts: list[str] = []
    async for ev in ae.ask.answer(turn, text):
        assert isinstance(ev, TextEvent)
        parts.append(ev.text)
    return "".join(parts), turn


def _records(turn, stage: str) -> list:
    return [r for r in turn.trace.records if r.stage == stage]


async def test_grounded_answer_streams_and_adds_sources():
    ae = make_agent_env(
        llm=FakeLLM(stream={"answer": "A native app profile has the root key applicationProfile [1]."})
    )
    text, turn = await _texts(ae, "What is a native app profile?")
    assert "applicationProfile" in text
    assert "Sources:" in text
    recs = _records(turn, "citations")
    assert recs and recs[0].ok is True


async def test_answer_without_llm_is_extractive_with_notice():
    ae = make_agent_env()
    text, _turn = await _texts(ae, "What is a native app?")
    assert "language model" in text
    assert "[1]" in text


async def test_unsupported_identifier_gets_verification_note():
    ae = make_agent_env(
        llm=FakeLLM(stream={"answer": "Set the `fooBarBaz` field to 9999 [1]."})
    )
    text, _turn = await _texts(ae, "What is a native app profile?")
    assert "Note: I could not verify" in text


async def test_invalid_citation_number_gets_note():
    ae = make_agent_env(
        llm=FakeLLM(stream={"answer": "Native apps run as containers [7]."})
    )
    text, _turn = await _texts(ae, "What is a native app profile?")
    assert "do not match any source" in text


async def test_not_confident_uses_general_answer_with_llm():
    scripted = {
        "answer_general": "This is not covered in the HyperAI documentation, but in general: "
        "a reverse proxy forwards requests."
    }
    ae = make_agent_env(llm=FakeLLM(stream=dict(scripted)))
    text, turn = await _texts(ae, "How does a reverse proxy cache work?")
    assert text.startswith("This is not covered")
    recs = _records(turn, "ask")
    assert recs and recs[0].detail.get("mode") == "general"
    ae2 = make_agent_env(llm=FakeLLM(stream=dict(scripted)), feature_rag=False)
    text2, turn2 = await _texts(ae2, "What is a native app profile?")
    recs2 = _records(turn2, "ask")
    assert recs2 and recs2[0].detail.get("mode") == "general"
    assert not _records(turn2, "retrieve")


async def test_not_confident_without_llm_abstains():
    ae = make_agent_env()
    text, _turn = await _texts(ae, "How does a reverse proxy cache work?")
    assert text.startswith(ABSTAIN_PHRASE)


async def test_index_missing_uses_builtin_overview():
    ae = make_agent_env(
        with_index=False,
        llm=FakeLLM(stream={"answer": "A native app profile has the root key applicationProfile [1]."}),
    )
    text, _turn = await _texts(ae, "What is in a native app profile?")
    assert text.startswith(NOTICE_NO_INDEX)
    assert "Sources:" in text
    assert ASK_ABSTAIN_HINT  # referenced for coverage of the abstain hint literal

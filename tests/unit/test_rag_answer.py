"""Answer + citation tests (DP-RAG WU-RAG-04)."""
from __future__ import annotations
from hyperion.llm.fake import FakeLLM
from hyperion.rag.answer import (
    ABSTAIN_PHRASE,
    ANSWER_SYSTEM,
    GENERAL_SYSTEM,
    build_answer_messages,
    extractive_answer,
    sources_footer,
    stream_answer,
)
from hyperion.rag.chunker import chunk_document
from hyperion.rag.citations import check_citations
from hyperion.rag.ingest import RawDoc
from hyperion.rag.types import Chunk, Hit


def _chunk(text: str, *, title: str = "Doc", source: str = "seed/doc.md", url: str | None = None) -> Chunk:
    return Chunk(id="doc#001", doc_id="doc", title=title, section="", text=text, source=source, url=url)


def _hit(i: int, text: str, *, title: str = "Doc", source: str = "seed/doc.md", url: str | None = None) -> Hit:
    return Hit(chunk=_chunk(text, title=title, source=source, url=url), score=10.0 - i, bm25=5.0 - i, dense=None, rank=i)


def _seed_hits() -> list[Hit]:
    doc = RawDoc(
        doc_id="seed-native-apps",
        title="HyperAI Native Applications Specification",
        source="seed/native-apps.md",
        url="https://ide-tutorial.hyperai.di.uoa.gr/dsl/native-apps/",
        text=(
            "# HyperAI Native Applications Specification\n\n"
            "## Resource Requirements\n\n"
            "Every native application profile declares its resource needs in specs.resources. "
            "The cpu field gives the number of CPU cores in millicores, for example 2000m for two cores. "
            "The memory field uses Mi or Gi units, for example 10Gi of RAM for training jobs.\n"
            "\n## Network Requirements\n\n"
            "Every native application profile declares its network needs in specs.network. "
            "The ports list exposes container ports to the cluster network for traffic. "
            "The ports port field is an integer such as 8000 for plain HTTP traffic.\n"
        ),
    )
    chunks = chunk_document(doc)
    return [Hit(chunk=c, score=10.0 - i, bm25=5.0 - i, dense=None, rank=i + 1) for i, c in enumerate(chunks)]


def test_messages_have_numbered_context_and_question():
    hits = _seed_hits()
    msgs = build_answer_messages("What CPU field does a native app need?", hits)
    assert msgs[0] == {"role": "system", "content": ANSWER_SYSTEM}
    assert msgs[1]["role"] == "user"
    body = msgs[1]["content"]
    assert body.startswith("CONTEXT:\n[1] ")
    assert "[2]" in body
    assert body.rstrip().endswith("QUESTION: What CPU field does a native app need?")


def test_context_budget_drops_lowest_ranked():
    hits = [_hit(i, f"Title\nLong context passage number {i}. " + "filler words here. " * 110) for i in range(1, 6)]
    msgs = build_answer_messages("What is HyperAI?", hits)
    body = msgs[1]["content"]
    context = body.split("QUESTION:")[0]
    assert len(context) <= 6300 + len("CONTEXT:\n")
    assert "[1]" in body and "[2]" in body
    assert "[5]" not in body  # lowest-ranked dropped to fit the budget


def test_history_block_included():
    hits = _seed_hits()
    msgs = build_answer_messages("And the memory?", hits, history_text="We discussed the cpu field.")
    assert "CONVERSATION SO FAR:\nWe discussed the cpu field." in msgs[1]["content"]


def test_general_prompt_has_no_context():
    msgs = build_answer_messages("What is a container?", _seed_hits(), general=True)
    assert msgs[0]["content"] == GENERAL_SYSTEM
    assert "CONTEXT" not in msgs[1]["content"]
    assert "QUESTION: What is a container?" in msgs[1]["content"]


async def test_stream_answer_uses_fake_llm_name():
    hits = _seed_hits()
    llm = FakeLLM(stream={"answer": "The cpu field is 2000m [1]."})
    parts = [p async for p in stream_answer(llm, "What CPU?", hits)]
    assert "".join(parts) == "The cpu field is 2000m [1]."
    assert llm.calls and llm.calls[0].name == "answer"

    llm_general = FakeLLM(stream={"answer_general": "General knowledge reply."})
    parts = [p async for p in stream_answer(llm_general, "What is a container?", hits, general=True)]
    assert "".join(parts) == "General knowledge reply."
    assert llm_general.calls and llm_general.calls[0].name == "answer_general"


def test_extractive_answer_cites_and_notes_offline():
    hits = _seed_hits()
    out = extractive_answer("What CPU field does a native app need?", hits)
    assert out.startswith("I cannot reach the language model right now, so here is what the documentation says:")
    assert "[1]" in out
    assert "Sources:" in out
    assert "cpu" in out.lower()


def test_extractive_answer_no_hits():
    assert extractive_answer("Anything?", []) == (
        "I cannot reach the language model right now and found nothing relevant in the documentation."
    )


def test_check_citations_valid():
    hits = _seed_hits()
    report = check_citations("The cpu field is 2000m for two cores [1].", hits,
                             question="What CPU field does a native app need?")
    assert report.cited_ids == [1]
    assert report.invalid_ids == []
    assert report.unsupported_tokens == []
    assert report.ok is True


def test_check_citations_invalid_id():
    hits = [_hit(i, f"Title\nPassage {i} with enough words to be useful here.") for i in range(1, 6)]
    report = check_citations("See the docs [7].", hits)
    assert report.invalid_ids == [7]
    assert report.ok is False


def test_check_citations_unsupported_number():
    hits = [_hit(1, "Title\nThe service exposes a port for traffic to flow through.")]
    report = check_citations("It uses port 9999 [1].", hits)
    assert "9999" in report.unsupported_tokens
    assert report.ok is False


def test_check_citations_abstain_ok():
    report = check_citations(ABSTAIN_PHRASE, _seed_hits())
    assert report.abstained is True
    assert report.ok is True


def test_sources_footer_cited_only():
    hits = [_hit(1, "Title\nFirst passage text here for testing."), _hit(2, "Title\nSecond passage text here for testing.")]
    footer = sources_footer(hits, "Only the second matters [2].")
    assert "- [2]" in footer
    assert "- [1]" not in footer


def test_sources_footer_default_top_two():
    hits = [
        _hit(1, "Title\nFirst passage text here for testing.", title="Alpha"),
        _hit(2, "Title\nSecond passage text here for testing.", title="Beta"),
        _hit(3, "Title\nThird passage text here for testing.", title="Gamma"),
    ]
    footer = sources_footer(hits, "No citations here.")
    assert "- [1] Alpha" in footer and "- [2] Beta" in footer
    assert "[3]" not in footer

"""BM25 + index + retriever tests (DP-RAG WU-RAG-01 and WU-RAG-03)."""
from __future__ import annotations
import json
from pathlib import Path
import pytest
from hyperion.config import Settings
from hyperion.context import new_turn
from hyperion.llm.fake import FakeLLM
from hyperion.rag.bm25 import BM25Index
from hyperion.rag.chunker import chunk_document
from hyperion.rag.index import IndexMissing, RagIndex
from hyperion.rag.ingest import load_corpus
from hyperion.rag.retriever import Retriever, load_retriever
from hyperion.rag.text import sentences, tokenize

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "corpus" / "seed"


# ---- WU-RAG-01: text utilities and BM25 ----

def test_tokenize_camel_and_dotted():
    toks = tokenize("containerImage.uri")
    assert "containerimage.uri" not in toks  # '.' is a boundary
    assert "containerimage" in toks  # unsplit form kept
    assert "container" in toks and "image" in toks and "uri" in toks


def test_tokenize_stems_and_stopwords():
    toks = tokenize("The containers are running")
    assert "container" in toks
    assert "the" not in toks and "are" not in toks
    assert "containers" not in toks


def test_sentences_split_and_filter():
    text = "Hi. This is a much longer sentence here! Short.\n- Another bullet point here ok"
    sents = sentences(text)
    assert "Hi." not in sents  # under 20 chars
    assert any(s.startswith("This is a much longer") for s in sents)
    assert all(len(s) >= 20 for s in sents)
    assert not any(s.startswith("- ") for s in sents)


def test_bm25_ranks_exact_term_first():
    index = BM25Index([
        ["hyperai", "ide", "guide"],
        ["weather", "forecast", "rain"],
        ["hyperai", "hyperai", "hyperai", "native", "app"],
    ])
    top = index.top(["hyperai"], 3)
    assert top[0][0] == 2


def test_bm25_idf_downweights_common_terms():
    index = BM25Index([
        ["app", "app", "app", "profile"],
        ["app", "guide", "install"],
        ["app", "hypervisor", "kvm"],
    ])
    top = index.top(["app", "hypervisor"], 3)
    assert top[0][0] == 2


def test_bm25_top_tiebreak_stable():
    index = BM25Index([["same", "words"], ["same", "words"], ["same", "words"]])
    top = index.top(["same"], 3)
    assert [i for i, _ in top] == [0, 1, 2]


# ---- WU-RAG-03: index and retriever ----

def _seed_chunks():
    docs = load_corpus(SEED)
    assert len(docs) == 6
    return [c for d in docs for c in chunk_document(d)]


async def _seed_index(llm: FakeLLM):
    chunks = _seed_chunks()
    vectors = await llm.embed([c.text for c in chunks], kind="document")
    return RagIndex.build(chunks, vectors, "fake-model"), chunks


def test_index_save_load_roundtrip(tmp_path):
    async def go():
        llm = FakeLLM(embed_dim=16)
        index, _ = await _seed_index(llm)
        path = tmp_path / "index.json"
        index.save(path)
        loaded = RagIndex.load(path)
        assert [c.text for c in loaded.chunks] == [c.text for c in index.chunks]
        assert [c.id for c in loaded.chunks] == [c.id for c in index.chunks]
        assert loaded.embed_model == "fake-model"
        assert len(loaded.vectors) == len(index.vectors)
        for row_new, row_old in zip(loaded.vectors, index.vectors):
            assert row_new == pytest.approx(row_old, abs=1e-3)
    import asyncio
    asyncio.run(go())


def test_index_missing_raises(tmp_path):
    with pytest.raises(IndexMissing):
        RagIndex.load(tmp_path / "does-not-exist.json")


def test_index_wrong_version_raises(tmp_path):
    path = tmp_path / "index.json"
    path.write_text(json.dumps({"version": 999, "chunks": [], "vectors": None}), encoding="utf-8")
    with pytest.raises(IndexMissing):
        RagIndex.load(path)


def test_dense_top_cosine_order():
    from hyperion.rag.types import Chunk
    chunks = [
        Chunk(id="d#001", doc_id="d", title="T", section="", text="T\nfirst", source="s", url=None),
        Chunk(id="d#002", doc_id="d", title="T", section="", text="T\nsecond", source="s", url=None),
        Chunk(id="d#003", doc_id="d", title="T", section="", text="T\nthird", source="s", url=None),
    ]
    index = RagIndex.build(chunks, [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]], None)
    top = index.dense_top([1.0, 0.0], 3)
    assert [i for i, _ in top] == [0, 2, 1]
    assert top[0][1] == pytest.approx(1.0)


async def test_retrieve_hybrid_finds_native_cpu_field(settings):
    llm = FakeLLM(embed_dim=64)
    index, _ = await _seed_index(llm)
    retriever = Retriever(index, llm, settings)
    result = await retriever.retrieve("cpu millicores 2000m")
    assert result.mode == "hybrid"
    assert result.hits
    assert result.hits[0].chunk.doc_id == "native-apps"
    assert "cpu" in result.hits[0].chunk.text.lower()
    assert result.hits[0].dense is not None


async def test_retrieve_bm25_when_llm_down(settings):
    llm = FakeLLM(embed_dim=64, down=True)
    index, _ = await _seed_index(FakeLLM(embed_dim=64))
    retriever = Retriever(index, llm, settings)
    result = await retriever.retrieve("What CPU field does a native application need?")
    assert result.mode == "bm25"
    assert result.hits
    assert all(h.dense is None for h in result.hits)


async def test_retrieve_caps_three_per_doc(settings):
    llm = FakeLLM(embed_dim=64)
    index, _ = await _seed_index(llm)
    retriever = Retriever(index, llm, settings)
    result = await retriever.retrieve("HyperAI application profile", k=10)
    assert result.hits
    counts: dict[str, int] = {}
    for hit in result.hits:
        counts[hit.chunk.doc_id] = counts.get(hit.chunk.doc_id, 0) + 1
    assert all(v <= 3 for v in counts.values())


async def test_lexical_support_domain_vs_offtopic(settings):
    llm = FakeLLM(embed_dim=64)
    index, _ = await _seed_index(llm)
    retriever = Retriever(index, llm, settings)
    assert retriever.lexical_support("What is HyperAI?") >= 0.6
    assert retriever.lexical_support("which fields does a native app profile need") >= 0.6
    assert retriever.lexical_support("What is the weather today?") == 0.0
    assert retriever.lexical_support("tell me a joke") == 0.0


async def test_confident_flags(settings):
    llm = FakeLLM(embed_dim=64)
    index, _ = await _seed_index(llm)
    retriever = Retriever(index, llm, settings)
    domain = await retriever.retrieve("which fields does a native app profile need")
    assert domain.confident is True
    off = await retriever.retrieve("tell me a joke")
    assert off.support == 0.0
    assert off.confident is False
    down = Retriever(index, FakeLLM(embed_dim=64, down=True), settings)
    off_bm25 = await down.retrieve("What is the weather today?")
    assert off_bm25.mode == "bm25"
    assert off_bm25.confident is False


def test_load_retriever_missing_returns_none(tmp_path):
    settings = Settings(index_path=tmp_path / "missing.json")
    assert load_retriever(settings, FakeLLM()) is None


async def test_retrieve_records_trace(settings):
    llm = FakeLLM(embed_dim=64)
    index, _ = await _seed_index(llm)
    retriever = Retriever(index, llm, settings)
    turn = new_turn(settings, "u", "which fields does a native app profile need")
    result = await retriever.retrieve("which fields does a native app profile need", turn=turn)
    stages = [r.stage for r in turn.trace.records]
    assert "retrieve" in stages
    record = next(r for r in turn.trace.records if r.stage == "retrieve")
    assert record.detail["mode"] == result.mode
    assert record.detail["ids"] == [h.chunk.id for h in result.hits]

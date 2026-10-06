"""Ingest + chunker tests (DP-RAG WU-RAG-02)."""
from __future__ import annotations
from pathlib import Path
from hyperion.rag.chunker import chunk_document
from hyperion.rag.ingest import RawDoc, load_corpus, read_file_text

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "corpus" / "seed"


def _rawdoc(doc_id: str = "doc", title: str = "Doc Title", text: str = "") -> RawDoc:
    return RawDoc(doc_id=doc_id, title=title, source="seed/doc.md", url=None, text=text)


def test_load_corpus_reads_seed_and_titles():
    docs = load_corpus(ROOT / "corpus")
    assert len(docs) == 6
    by_id = {d.doc_id: d for d in docs}
    assert by_id["seed-native-apps"].title == "HyperAI Native Applications Specification"
    assert by_id["seed-hyperai-overview"].title == "HYPER-AI Project Overview"
    assert all(d.title.strip() for d in docs)
    assert all(d.source.startswith("seed/") and d.source.endswith(".md") for d in docs)


def test_load_corpus_source_url_extracted():
    docs = load_corpus(ROOT / "corpus")
    native = next(d for d in docs if d.doc_id == "seed-native-apps")
    assert native.url == "https://ide-tutorial.hyperai.di.uoa.gr/dsl/native-apps/"


def test_load_corpus_skips_unsupported_and_tiny(tmp_path):
    (tmp_path / "good.md").write_text("# Good Doc\n\n" + "Meaningful content words. " * 10, encoding="utf-8")
    (tmp_path / "tiny.md").write_text("# Tiny\n", encoding="utf-8")
    (tmp_path / "binary.exe").write_text("MZ" + "x" * 100, encoding="utf-8")
    docs = load_corpus(tmp_path)
    assert [d.doc_id for d in docs] == ["good"]
    assert docs[0].title == "Good Doc"


def test_read_file_text_html(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(
        "<html><head><script>evil();</script></head><body>"
        "<h1>Welcome Here</h1><style>.x{color:red}</style>"
        "<p>This paragraph carries more than forty characters of text.</p>"
        "</body></html>",
        encoding="utf-8",
    )
    text = read_file_text(page)
    assert "# Welcome Here" in text
    assert "evil" not in text
    assert ".x" not in text


def test_chunk_sections_and_headers():
    docs = load_corpus(ROOT / "corpus")
    native = next(d for d in docs if d.doc_id == "seed-native-apps")
    chunks = chunk_document(native)
    assert len(chunks) > 3
    first = chunks[0]
    assert first.section == ""
    assert first.text.startswith(native.title + "\n")
    profile = [c for c in chunks if c.section == "Profile Structure"]
    assert profile
    assert profile[0].text.startswith(f"{native.title} › Profile Structure\n")


def test_chunk_code_block_kept_whole():
    code_lines = [f"  key{i}: value{i}" for i in range(10)]
    text = "# T\n\n## Config\n\nSome intro prose here for context.\n\n```yaml\n" + "\n".join(code_lines) + "\n```\n"
    chunks = chunk_document(_rawdoc(text=text))
    fenced = [c for c in chunks if "```yaml" in c.text]
    assert len(fenced) == 1
    for line in code_lines:
        assert line in fenced[0].text


def test_chunk_table_split_repeats_header():
    rows = ["| Field | Type | Required |", "|-------|------|----------|"]
    rows += [f"| `field{i}` | string | yes | description number {i} here today |" for i in range(60)]
    text = "# T\n\n## Big Table\n\n" + "\n".join(rows) + "\n"
    chunks = chunk_document(_rawdoc(text=text))
    assert len(chunks) >= 2
    for chunk in chunks:
        assert "| Field | Type | Required |" in chunk.text
        assert "|-------|------|----------|" in chunk.text


def test_chunk_prose_overlap():
    sents = [
        f"This is sentence number {i} about HyperAI native applications and containers."
        for i in range(30)
    ]
    text = "# Doc Title\n\n## Section One\n\n" + " ".join(sents) + "\n"
    chunks = chunk_document(_rawdoc(title="Doc Title", text=text))
    assert len(chunks) >= 2
    tail = " ".join(chunks[0].text.split()[-30:])
    assert tail in chunks[1].text


def test_chunk_tiny_section_merged():
    prose = " ".join(f"Word{i} filler prose for the big section here." for i in range(12))
    text = f"# T\n\n## Big Section\n\n{prose}\n\n## Tiny\n\nFew words here.\n"
    chunks = chunk_document(_rawdoc(text=text))
    assert not [c for c in chunks if c.section == "Tiny"]
    assert any("Few words here." in c.text and c.section == "Big Section" for c in chunks)


def test_chunk_ids_stable_and_unique():
    docs = load_corpus(ROOT / "corpus")
    native = next(d for d in docs if d.doc_id == "seed-native-apps")
    first = [c.id for c in chunk_document(native)]
    second = [c.id for c in chunk_document(native)]
    assert first == second
    assert len(set(first)) == len(first)
    assert first[0] == "seed-native-apps#001"


def test_chunk_native_spec_contains_containerimage_row():
    docs = load_corpus(ROOT / "corpus")
    native = next(d for d in docs if d.doc_id == "seed-native-apps")
    chunks = chunk_document(native)
    assert any("containerImage.uri" in c.text for c in chunks)

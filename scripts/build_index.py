import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
"""Build the RAG index from a corpus directory (DP-RAG §5.10)."""
import argparse
import asyncio
import dataclasses
from pathlib import Path
from hyperion.config import load_settings
from hyperion.llm.base import LLMUnavailable
from hyperion.llm.client import build_llm
from hyperion.rag.chunker import chunk_document
from hyperion.rag.index import RagIndex
from hyperion.rag.ingest import load_corpus


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the RAG index.")
    parser.add_argument("--src", required=True, help="Corpus directory (e.g. corpus)")
    parser.add_argument("--out", required=True, help="Output index JSON (e.g. data/index.json)")
    parser.add_argument("--no-embed", action="store_true", help="Build a BM25-only index")
    parser.add_argument("--embed-model", default=None, help="Override EMBED_MODEL")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    if args.embed_model:
        settings = dataclasses.replace(settings, embed_model=args.embed_model)
    src = Path(args.src)
    out = Path(args.out)
    docs = load_corpus(src)
    if not docs:
        print(f"no documents found in {args.src}")
        return 2
    chunks = [chunk for doc in docs for chunk in chunk_document(doc)]
    vectors: list[list[float]] | None = None
    embed_model: str | None = None
    if not args.no_embed:
        llm = build_llm(settings)
        texts = [c.text for c in chunks]

        async def _embed_all() -> list[list[float]]:
            out_vecs: list[list[float]] = []
            for i in range(0, len(texts), 32):
                out_vecs.extend(await llm.embed(texts[i:i + 32], kind="document"))
            return out_vecs

        try:
            vectors = asyncio.run(_embed_all())
        except LLMUnavailable as exc:
            print(f"embed unavailable: {exc}")
            return 3
        embed_model = settings.embed_model
    index = RagIndex.build(chunks, vectors, embed_model)
    index.save(out)
    total_words = sum(len(c.text.split()) for c in chunks)
    avg_words = (total_words / len(chunks)) if chunks else 0.0
    dim = len(vectors[0]) if vectors else None
    size = out.stat().st_size
    print(f"docs={len(docs)} chunks={len(chunks)} avg_words={avg_words:.1f} dim={dim} out={out} bytes={size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

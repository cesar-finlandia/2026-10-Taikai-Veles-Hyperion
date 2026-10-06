"""Grounded answer construction: prompts, streaming and extractive fallback (DP-RAG §5.6–§5.7, §5.9)."""
from __future__ import annotations
import re
from typing import AsyncIterator, Sequence
from hyperion.context import TurnContext
from hyperion.llm.base import LLMLike, Message
from hyperion.rag.text import content_terms, sentences, tokenize
from hyperion.rag.types import Hit

ABSTAIN_PHRASE: str = "I could not find that in the HyperAI documentation."

ANSWER_SYSTEM: str = (
    "You are Hyperion, the assistant built into the HyperAI IDE. "
    "Answer the user's question using ONLY the numbered CONTEXT passages in the user message.\n"
    "Rules:\n"
    "1. Cite the passages you use with their numbers in square brackets, like [1] or [2][3], right after the sentence they support.\n"
    "2. If the context does not contain the answer, reply exactly: \"I could not find that in the HyperAI documentation.\" and stop. Do not guess.\n"
    "3. Copy field names, values, ports and commands exactly as written in the context.\n"
    "4. Be concise: at most 6 sentences or 8 short lines. Plain text. Use a short code block only when the user asks for YAML or a command.\n"
    "5. Reply in the language of the user's question."
)

GENERAL_SYSTEM: str = (
    "You are Hyperion, the assistant built into the HyperAI IDE. "
    "The HyperAI documentation has no passage for this question, but it is about software, containers, edge or cloud computing. "
    "Answer in at most 5 sentences from general knowledge. Begin with: \"This is not covered in the HyperAI documentation, but in general:\" "
    "Do not invent HyperAI-specific facts, field names or commands. Reply in the language of the question."
)

_CITE_RE = re.compile(r"\[(\d{1,2})\]")


def _truncate_words(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut if cut else text[:limit]


def build_answer_messages(question: str, hits: Sequence[Hit], *, history_text: str = "", general: bool = False) -> list[Message]:
    """System + one user message (§5.6). When general=True uses GENERAL_SYSTEM and no CONTEXT block."""
    if general:
        user = ""
        if history_text:
            user += f"CONVERSATION SO FAR:\n{history_text}\n\n"
        user += f"QUESTION: {question}"
        return [
            {"role": "system", "content": GENERAL_SYSTEM},
            {"role": "user", "content": user},
        ]
    survivors = list(hits[:5])
    while len(survivors) > 1 and sum(len(h.chunk.text) for h in survivors) > 6300:
        survivors.pop()
    blocks = [f"[{i}] {_truncate_words(h.chunk.text, 1100)}" for i, h in enumerate(survivors, start=1)]
    context = "\n\n".join(blocks)
    user = f"CONTEXT:\n{context}\n\n"
    if history_text:
        user += f"CONVERSATION SO FAR:\n{history_text}\n\n"
    user += f"QUESTION: {question}"
    return [
        {"role": "system", "content": ANSWER_SYSTEM},
        {"role": "user", "content": user},
    ]


async def stream_answer(llm: LLMLike, question: str, hits: Sequence[Hit], *, history_text: str = "", turn: TurnContext | None = None,
                        general: bool = False) -> AsyncIterator[str]:
    """llm.stream(build_answer_messages(...), name='answer' | 'answer_general', temperature=0.1, max_tokens=700)."""
    messages = build_answer_messages(question, hits, history_text=history_text, general=general)
    name = "answer_general" if general else "answer"
    async for part in llm.stream(messages, name=name, turn=turn, temperature=0.1, max_tokens=700):
        yield part


def extractive_answer(question: str, hits: Sequence[Hit], *, max_sentences: int = 3) -> str:
    """Model-free answer: the best sentences from the top hits with [n] markers, prefixed with the offline notice (§5.7)."""
    if not hits:
        return "I cannot reach the language model right now and found nothing relevant in the documentation."
    terms = set(content_terms(question))
    candidates: list[tuple[float, int, int, str, int]] = []  # (score, hit_rank, pos, sentence, marker)
    for marker, hit in enumerate(hits[:3], start=1):
        sents = sentences(hit.chunk.text)
        header = hit.chunk.text.split("\n", 1)[0].strip()
        if sents and sents[0].strip() == header:
            sents = sents[1:]
        for pos, sent in enumerate(sents):
            score = float(len(terms & set(tokenize(sent))))
            if marker == 1:
                score += 0.5
            candidates.append((score, marker, pos, sent, marker))
    scored = [c for c in candidates if c[0] > 0]
    if not scored:
        first: list[str] = []
        for marker, hit in enumerate(hits[:3], start=1):
            sents = sentences(hit.chunk.text)
            header = hit.chunk.text.split("\n", 1)[0].strip()
            if sents and sents[0].strip() == header:
                sents = sents[1:]
            if sents:
                first.append((marker, sents[0]))
                break
        if not first:
            return "I cannot reach the language model right now and found nothing relevant in the documentation."
        marker, sent = first[0]
        picked = [(0.0, marker, 0, sent, marker)]
    else:
        scored.sort(key=lambda c: (-c[0], c[1], c[2]))
        picked = scored[:max_sentences]
        picked.sort(key=lambda c: (c[1], c[2]))
    lines = "\n".join(f"- {sent} [{marker}]" for _, _, _, sent, marker in picked)
    return (
        "I cannot reach the language model right now, so here is what the documentation says:\n"
        + lines
        + sources_footer(hits, lines)
    )


def sources_footer(hits: Sequence[Hit], answer_text: str) -> str:
    """'\n\nSources:\n- [1] <title> (<source>)\n...' for cited ids, else the top 2 hits; '' when hits is empty."""
    if not hits:
        return ""
    cited = sorted({int(m) for m in _CITE_RE.findall(answer_text)})
    used = [i for i in cited if 1 <= i <= len(hits)]
    if not used:
        used = [1, 2][:len(hits)]
    lines: list[str] = []
    for i in used:
        chunk = hits[i - 1].chunk
        if chunk.url:
            lines.append(f"- [{i}] {chunk.title} - {chunk.url}")
        else:
            lines.append(f"- [{i}] {chunk.title} ({chunk.source})")
    return "\n\nSources:\n" + "\n".join(lines)

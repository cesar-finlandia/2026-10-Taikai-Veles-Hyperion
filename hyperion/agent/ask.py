"""Answer flow for documentation / general in-scope questions (DP-AGENT-CORE §5.4)."""
from __future__ import annotations

import logging
from typing import AsyncIterator

from hyperion.config import Settings
from hyperion.context import TurnContext
from hyperion.events import Event, TextEvent
from hyperion.llm.base import LLMLike, LLMUnavailable
from hyperion.memory.store import SessionStore
from hyperion.rag.answer import ABSTAIN_PHRASE, extractive_answer, sources_footer, stream_answer
from hyperion.rag.citations import check_citations
from hyperion.rag.retriever import Retriever

from hyperion.agent.overview import select_builtin_hits
from hyperion.agent.texts import ASK_ABSTAIN_HINT, ASK_INCOMPLETE, NOTICE_NO_INDEX

_log = logging.getLogger(__name__)


class AskFlow:
    def __init__(self, settings: Settings, llm: LLMLike, store: SessionStore, retriever: Retriever | None) -> None:
        self._settings = settings
        self._llm = llm
        self._store = store
        self._retriever = retriever

    async def answer(self, turn: TurnContext, text: str) -> AsyncIterator[Event]:
        """Answer a documentation / general in-scope question. See §5.4. Yields TextEvents only. Never raises for model or retrieval failures."""
        # 1. History for the prompt.
        try:
            history = self._store.history_for_prompt(turn.user_id)
        except Exception:
            history = ""
        # 2. Retrieve.
        hits: list = []
        confident = False
        support = 0.0
        notice = ""
        if not self._settings.feature_rag:
            hits = []
            confident = False
            notice = ""
        elif self._retriever is not None:
            try:
                result = await self._retriever.retrieve(text, turn=turn)
            except Exception as exc:
                _log.debug("retrieve failed: %s", exc)
                hits = []
                confident = False
                try:
                    turn.trace.add("retrieve", ok=False, error=type(exc).__name__)
                except Exception:
                    pass
            else:
                hits = result.hits
                confident = result.confident and bool(hits)
                support = result.support
                notice = ""
        else:
            hits = select_builtin_hits(text)
            confident = bool(hits)
            notice = NOTICE_NO_INDEX if hits else ""
        # 3. Notice first.
        if notice:
            yield TextEvent(notice + "\n\n")
        mode = "general"
        used_llm = False
        if confident:
            # 4. Grounded answer, streamed; verification follows.
            full = ""
            started = False
            try:
                async for part in stream_answer(
                    self._llm, text, hits, history_text=history, turn=turn
                ):
                    started = True
                    full += part
                    yield TextEvent(part)
            except (LLMUnavailable, Exception) as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                    raise
                if not started:
                    try:
                        ex = extractive_answer(text, hits)
                    except Exception:
                        ex = "I cannot reach the language model right now and found nothing relevant in the documentation."
                    yield TextEvent(ex)
                    mode = "extractive"
                    used_llm = False
                else:
                    yield TextEvent(ASK_INCOMPLETE)
                    mode = "grounded_partial"
                    used_llm = True
            else:
                mode = "grounded"
                used_llm = True
                try:
                    report = check_citations(full, hits, question=text)
                except Exception:
                    report = None
                if report is not None:
                    try:
                        turn.trace.add(
                            "citations",
                            ok=report.ok,
                            cited=report.cited_ids,
                            invalid=report.invalid_ids,
                            unsupported=report.unsupported_tokens,
                            abstained=report.abstained,
                        )
                    except Exception:
                        pass
                    if not (report.ok and not report.abstained):
                        if report.unsupported_tokens:
                            yield TextEvent(
                                "\n\nNote: I could not verify these terms in the documentation: "
                                + ", ".join(report.unsupported_tokens[:5])
                                + "."
                            )
                        if report.invalid_ids:
                            yield TextEvent(
                                "\n\nNote: citation number(s) "
                                + ", ".join(str(i) for i in report.invalid_ids)
                                + " do not match any source."
                            )
                    if not report.abstained and ABSTAIN_PHRASE not in full:
                        try:
                            footer = sources_footer(hits, full)
                        except Exception:
                            footer = ""
                        if footer:
                            yield TextEvent(footer)
        else:
            # 5. Not confident: general answer or abstention.
            started = False
            try:
                async for part in stream_answer(
                    self._llm, text, [], history_text=history, turn=turn, general=True
                ):
                    started = True
                    yield TextEvent(part)
                mode = "general"
                used_llm = True
            except (LLMUnavailable, Exception) as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                    raise
                if not started:
                    # No model: quote the documents when they cover the question,
                    # otherwise abstain (degraded-mode matrix, blueprint §2.5).
                    fallback = ""
                    if hits and support >= self._settings.rag_lexical_min:
                        try:
                            fallback = extractive_answer(text, hits)
                        except Exception:
                            fallback = ""
                    if fallback:
                        try:
                            footer = sources_footer(hits, fallback)
                        except Exception:
                            footer = ""
                        yield TextEvent(fallback + (footer or ""))
                        mode = "extractive"
                        used_llm = False
                    else:
                        yield TextEvent(ABSTAIN_PHRASE + " " + ASK_ABSTAIN_HINT)
                        mode = "abstain"
                        used_llm = False
                else:
                    yield TextEvent(ASK_INCOMPLETE)
                    mode = "general"
                    used_llm = True
        # 6. One ask record per answer.
        try:
            turn.trace.add(
                "ask",
                mode=mode,
                n_hits=len(hits),
                confident=confident,
                notice=bool(notice),
                used_llm=used_llm,
            )
        except Exception:
            pass

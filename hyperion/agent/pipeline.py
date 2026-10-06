"""Turn orchestrator: pre-route, guard stage, ask flow, act dispatch, scrubbed streaming (DP-AGENT-CORE §5.3)."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

from hyperion.actions.flow import ActionsFlow
from hyperion.actions.intents import ActIntent
from hyperion.agent.ask import AskFlow
from hyperion.agent.inputs import sanitize_text
from hyperion.agent.locks import UserLocks
from hyperion.agent.router import PostRoute, PreRoute, post_route, pre_route
from hyperion.agent.texts import (
    BUSY_TEXT,
    ERROR_AFTER_ACTIONS_TEXT,
    ERROR_TEXT,
    TIMEOUT_TEXT,
)
from hyperion.config import Settings
from hyperion.context import TurnContext, new_turn
from hyperion.events import ActionEvent, Event, TextEvent
from hyperion.guard.engine import Guard, Verdict
from hyperion.guard.injection import injection_hit
from hyperion.guard.lexicon import lexical_scope
from hyperion.guard.scrub import StreamScrubber
from hyperion.ide.gateway import IdeGateway
from hyperion.llm.base import LLMLike
from hyperion.llm.client import build_llm
from hyperion.memory.store import SessionStore
from hyperion.rag.retriever import Retriever, load_retriever
from hyperion.sse import chunk_text
from hyperion.trace import TraceStore

_log = logging.getLogger(__name__)

USER_LOCK_TIMEOUT_S: float = 60.0
SPLIT_CHARS: int = 60


@dataclass
class _Out:
    text: list[str] = field(default_factory=list)
    actions: int = 0
    route: str = ""
    summary: str = ""


class Agent:
    def __init__(
        self,
        settings: Settings,
        *,
        llm: LLMLike,
        gateway: IdeGateway,
        store: SessionStore,
        guard: Guard,
        flow: ActionsFlow,
        ask: AskFlow,
        traces: TraceStore,
        locks: UserLocks,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._gateway = gateway
        self._store = store
        self._guard = guard
        self._flow = flow
        self._ask = ask
        self._traces = traces
        self._locks = locks
        self._retriever: Retriever | None = None
        self._tasks: set[asyncio.Task] = set()

    async def handle(self, user_id: str, text: str) -> AsyncIterator[Event]:
        """The whole turn for one user message. See §5.3. Never raises; serialised per user_id; always ends (the caller
        adds [DONE]). Records the turn in memory and in the trace store."""
        user_id = (user_id or "anonymous")[:128]
        try:
            text = sanitize_text(text, limit=self._settings.max_text_chars + 1)
        except Exception:
            text = ""
        try:
            async with self._locks.hold(user_id, timeout=USER_LOCK_TIMEOUT_S) as got:
                if not got:
                    yield TextEvent(BUSY_TEXT)
                    return
                turn = new_turn(self._settings, user_id, text)
                session = self._store.get(user_id)
                out = _Out()
                secrets = [self._settings.api_key] if self._settings.api_key else []
                scrubber = StreamScrubber(secrets)
                t0 = time.monotonic()
                try:
                    async for ev in self._bounded(self._run(turn, session, out), turn):
                        if isinstance(ev, TextEvent):
                            try:
                                safe = scrubber.feed(ev.text)
                            except Exception:
                                safe = ev.text
                            if safe:
                                out.text.append(safe)
                                for piece in chunk_text(safe, SPLIT_CHARS):
                                    yield TextEvent(piece)
                        elif isinstance(ev, ActionEvent):
                            try:
                                tail = scrubber.flush()
                            except Exception:
                                tail = ""
                            if tail:
                                out.text.append(tail)
                                yield TextEvent(tail)
                            scrubber = StreamScrubber(secrets)
                            out.actions += 1
                            if out.actions > self._settings.max_actions_per_turn + 3:
                                try:
                                    turn.trace.add(
                                        "action_overflow", ok=False, actions=out.actions
                                    )
                                except Exception:
                                    pass
                                continue
                            yield ev
                        else:
                            yield ev
                    try:
                        tail = scrubber.flush()
                    except Exception:
                        tail = ""
                    if tail:
                        out.text.append(tail)
                        yield TextEvent(tail)
                except Exception as exc:
                    _log.exception("turn failed")
                    try:
                        turn.trace.add("error", ok=False, error=type(exc).__name__)
                    except Exception:
                        pass
                    if out.actions:
                        yield TextEvent(
                            ERROR_AFTER_ACTIONS_TEXT.format(n=out.actions)
                        )
                    else:
                        yield TextEvent(ERROR_TEXT)
                finally:
                    assistant = "".join(out.text)
                    try:
                        turn.trace.add(
                            "outcome", route=out.route, actions=out.actions
                        )
                    except Exception:
                        pass
                    try:
                        self._store.record_turn(
                            user_id, user=text, assistant=assistant, intent=out.route
                        )
                    except Exception:
                        _log.exception("record_turn failed")
                    try:
                        self._traces.put(
                            turn.trace, user_text=text, summary=out.summary or out.route
                        )
                    except Exception:
                        _log.exception("trace put failed")
                    try:
                        self._compress_later(user_id)
                    except Exception:
                        pass
                    try:
                        elapsed_ms = int((time.monotonic() - t0) * 1000.0)
                    except Exception:
                        elapsed_ms = 0
                    _log.info(
                        "turn=%s route=%s actions=%d ms=%d",
                        turn.turn_id,
                        out.route,
                        out.actions,
                        elapsed_ms,
                    )
        except Exception:
            _log.exception("turn failed")
            yield TextEvent(ERROR_TEXT)

    async def _run(
        self, turn: TurnContext, session: Any, out: _Out
    ) -> AsyncIterator[Event]:
        user_id = turn.user_id
        pre = pre_route(
            turn.text,
            session,
            has_pending=self._store.peek_pending(user_id) is not None,
            max_chars=self._settings.max_text_chars,
        )
        try:
            verb = pre.intent.verb if pre.intent else None
        except Exception:
            verb = None
        try:
            turn.trace.add("route", kind=pre.kind, verb=verb)
        except Exception:
            pass
        if pre.kind == "confirm":
            out.route = f"confirm_{pre.decision}"
            async for ev in self._flow.handle_confirmation(turn, pre.decision):  # type: ignore[arg-type]
                yield ev
        elif pre.kind == "recall":
            out.route = "recall"
            yield TextEvent(pre.reply or "")
            r = self._flow.pending_reminder(user_id)
            if r:
                yield TextEvent("\n\n" + r)
        elif pre.kind == "fact":
            out.route = "fact"
            yield TextEvent(pre.reply or "")
            r = self._flow.pending_reminder(user_id)
            if r:
                yield TextEvent("\n\n" + r)
        elif pre.kind in ("act", "validate", "read"):
            assert pre.intent is not None
            act_verb = pre.intent.verb
            out.route = f"act:{act_verb}"
            verdict = await self._scope_for_act(turn, session, pre.intent)
            if not verdict.allow:
                out.route = f"refuse:{verdict.category}"
                _log.info(
                    "guard refused category=%s reason=%s via=%s",
                    verdict.category,
                    verdict.reason,
                    verdict.via,
                )
                yield TextEvent(self._guard.refusal_message(verdict))
                return
            if act_verb in (
                "create",
                "edit",
                "fix",
                "delete",
                "create_folder",
                "delete_folder",
            ):
                async for ev in self._flow.handle_request(turn, pre.intent):
                    yield ev
            elif act_verb == "validate":
                async for ev in self._flow.handle_validate(turn, pre.intent):
                    yield ev
            else:  # read, explain_file
                async for ev in self._flow.handle_read(turn, pre.intent):
                    yield ev
        else:  # guard
            verdict = await self._guard.check(
                turn.text, turn=turn, followup_ok=session.turn_count > 0
            )
            pr = post_route(verdict)
            if pr == "refuse":
                out.route = f"refuse:{verdict.category}"
                _log.info(
                    "guard refused category=%s reason=%s via=%s",
                    verdict.category,
                    verdict.reason,
                    verdict.via,
                )
                yield TextEvent(self._guard.refusal_message(verdict))
                return
            if pr == "smalltalk":
                out.route = "smalltalk"
                try:
                    name = session.facts.get("name")
                except Exception:
                    name = None
                yield TextEvent(self._guard.smalltalk_reply(verdict.smalltalk, name))  # type: ignore[arg-type]
                r = self._flow.pending_reminder(user_id)
                if r:
                    yield TextEvent("\n\n" + r)
            else:
                out.route = "ask"
                async for ev in self._ask.answer(turn, turn.text):
                    yield ev
                r = self._flow.pending_reminder(user_id)
                if r:
                    yield TextEvent("\n\n" + r)
        try:
            out.summary = f"{out.route}: {''.join(out.text)[:120]}"
        except Exception:
            pass

    async def _scope_for_act(
        self, turn: TurnContext, session: Any, intent: ActIntent
    ) -> Verdict:
        # 1. Injection rules always apply, even on the act shortcut.
        try:
            name = injection_hit(turn.text)
        except Exception:
            name = None
        if name is not None:
            try:
                turn.trace.add(
                    "guard",
                    ok=True,
                    allow=False,
                    category="injection",
                    via="act_shortcut",
                    score=1.0,
                )
            except Exception:
                pass
            return Verdict(False, "injection", f"rule:{name}", "rule", 1.0)
        # 2. File-targeted or profile requests skip the guard's model call.
        try:
            lex = lexical_scope(turn.text)
        except Exception:
            lex = None
        if (
            lex is not None
            and (intent.target or intent.is_profile)
            and lex.offtopic == 0
        ):
            try:
                turn.trace.add(
                    "guard",
                    ok=True,
                    allow=True,
                    category="in_scope",
                    via="act_shortcut",
                    score=0.9,
                )
            except Exception:
                pass
            return Verdict(True, "in_scope", "act request", "rule", 0.9)
        # 3. Otherwise the full guard decides.
        try:
            followup_ok = session.turn_count > 0
        except Exception:
            followup_ok = False
        return await self._guard.check(turn.text, turn=turn, followup_ok=followup_ok)

    async def _bounded(
        self, agen: AsyncIterator[Event], turn: TurnContext
    ) -> AsyncIterator[Event]:
        while True:
            try:
                timeout = max(1.0, turn.time_left() + 10.0)
            except Exception:
                timeout = 10.0
            try:
                ev = await asyncio.wait_for(agen.__anext__(), timeout=timeout)
            except StopAsyncIteration:
                return
            except (asyncio.TimeoutError, TimeoutError):
                try:
                    await agen.aclose()
                except Exception:
                    pass
                try:
                    turn.trace.add("timeout", ok=False)
                except Exception:
                    pass
                yield TextEvent(TIMEOUT_TEXT)
                return
            else:
                yield ev

    def _compress_later(self, user_id: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _job() -> None:
            try:
                async with self._locks.hold(user_id, timeout=5.0) as got:
                    if got:
                        await self._store.compress_if_needed(user_id, self._llm)
            except Exception:
                _log.debug("compress later failed", exc_info=True)

        try:
            task = loop.create_task(_job())
        except Exception:
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def status(self) -> dict[str, Any]:
        """Data for GET /debug/status. See §5.8. Never raises."""
        settings = self._settings
        try:
            configured = bool(settings.api_key)
        except Exception:
            configured = False
        try:
            model = settings.llm_model
        except Exception:
            model = ""
        try:
            embed_model = settings.embed_model
        except Exception:
            embed_model = ""
        try:
            reachable = await self._llm.ping()
            reachable = bool(reachable)
        except Exception:
            reachable = False
        try:
            stats = dataclasses.asdict(self._llm.stats)
        except Exception:
            stats = {}
        try:
            ide = await self._gateway.probe()
        except Exception:
            try:
                ide = {"reachable": False, "url": settings.ide_backend_url, "status": None}
            except Exception:
                ide = {"reachable": False, "url": "", "status": None}
        retriever = getattr(self, "_retriever", None)
        try:
            if retriever is None:
                index: dict[str, Any] = {"loaded": False, "chunks": 0, "mode": "builtin"}
            else:
                idx = retriever._index  # noqa: SLF001 (same package wiring)
                n = len(idx.chunks)
                mode = "hybrid" if idx.vectors is not None else "bm25"
                index = {"loaded": True, "chunks": n, "mode": mode}
        except Exception:
            index = {"loaded": retriever is not None, "chunks": 0, "mode": "bm25"}
        try:
            sessions = self._store.stats()
        except Exception:
            sessions = {"sessions": 0, "pending": 0, "turns": 0}
        try:
            features = {
                "rag": bool(settings.feature_rag),
                "guard": bool(settings.feature_guard),
                "memory": bool(settings.feature_memory),
                "templates": bool(settings.feature_templates),
                "repair": bool(settings.feature_repair),
                "hitl": bool(settings.feature_hitl),
                "hitl_mode": settings.hitl_mode,
            }
        except Exception:
            features = {}
        return {
            "service": "hyperion",
            "version": "0.1.0",
            "llm": {
                "configured": configured,
                "model": model,
                "embed_model": embed_model,
                "reachable": reachable,
                "stats": stats,
            },
            "ide": ide,
            "index": index,
            "sessions": sessions,
            "features": features,
        }

    async def aclose(self) -> None:
        """Await pending background tasks (≤ 2 s), then close the LLM client and the gateway when they have aclose()."""
        tasks = [t for t in list(self._tasks) if not t.done()]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), timeout=2.0
                )
            except (asyncio.TimeoutError, TimeoutError, Exception):
                pass
        for obj in (self._llm, self._gateway):
            fn = getattr(obj, "aclose", None)
            if callable(fn):
                try:
                    await fn()
                except Exception:
                    pass


def build_agent(
    settings: Settings,
    *,
    llm: LLMLike | None = None,
    gateway: IdeGateway | None = None,
    retriever: Retriever | None | Literal["auto"] = "auto",
    store: SessionStore | None = None,
) -> Agent:
    """Production wiring: llm = build_llm(settings); gateway = IdeGateway(settings); retriever = load_retriever(settings, llm)
    when 'auto'; store = SessionStore(settings); guard = Guard(settings, llm, retriever); flow = ActionsFlow(settings, llm, gateway, store);
    ask = AskFlow(settings, llm, store, retriever); traces = TraceStore(); locks = UserLocks()."""
    from hyperion.guard.engine import Guard

    llm = llm if llm is not None else build_llm(settings)
    gateway = gateway if gateway is not None else IdeGateway(settings)
    if retriever == "auto":
        try:
            retriever = load_retriever(settings, llm)
        except Exception:
            retriever = None
    store = store if store is not None else SessionStore(settings)
    guard = Guard(settings, llm, retriever)
    flow = ActionsFlow(settings, llm, gateway, store)
    ask = AskFlow(settings, llm, store, retriever)
    traces = TraceStore()
    locks = UserLocks()
    agent = Agent(
        settings,
        llm=llm,
        gateway=gateway,
        store=store,
        guard=guard,
        flow=flow,
        ask=ask,
        traces=traces,
        locks=locks,
    )
    agent._retriever = retriever  # noqa: SLF001 (same package wiring)
    return agent

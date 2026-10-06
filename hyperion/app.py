"""HTTP shell for the Hyperion agent (DP-AGENT-CORE §5.8)."""
from __future__ import annotations

import asyncio
import datetime
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from hyperion.agent.inputs import parse_chat_body
from hyperion.agent.pipeline import Agent, build_agent
from hyperion.agent.texts import ERROR_TEXT, SERVICE_STARTING_TEXT
from hyperion.config import Settings, get_settings
from hyperion.events import Event, TextEvent
from hyperion.sse import sse_stream

SSE_HEADERS: dict[str, str] = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
MAX_BODY_BYTES: int = 1_000_000

_log = logging.getLogger(__name__)


async def _read_body(request: Request) -> bytes:
    try:
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            if total >= MAX_BODY_BYTES:
                break
            piece = chunk[: MAX_BODY_BYTES - total]
            chunks.append(piece)
            total += len(piece)
            if total >= MAX_BODY_BYTES:
                break
        return b"".join(chunks)
    except Exception:
        return b""


async def _starting_events() -> AsyncIterator[Event]:
    yield TextEvent(SERVICE_STARTING_TEXT)


def create_app(settings: Settings | None = None, *, agent: Agent | None = None) -> FastAPI:
    """Routes: GET /health, POST /chat (and /chat/), GET /debug/turns, GET /debug/status. CORS allow-all stays. See §5.8."""
    s = settings or get_settings()
    logging.basicConfig(
        level=getattr(s, "log_level", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    async def get_agent() -> Agent | None:
        ag = app.state.agent
        if ag is not None:
            return ag
        async with app.state.agent_lock:
            ag = app.state.agent
            if ag is not None:
                return ag
            built = build_agent(s)
            app.state.agent = built
            app.state.own_agent = True
            return built

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await get_agent()
        except Exception:
            _log.exception("agent startup failed")
        yield
        if getattr(app.state, "own_agent", False):
            ag = getattr(app.state, "agent", None)
            if ag is not None:
                try:
                    await ag.aclose()
                except Exception:
                    pass

    app = FastAPI(title="Hyperion", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.agent = agent
    app.state.own_agent = False
    app.state.agent_lock = asyncio.Lock()

    @app.get("/health")
    async def health() -> JSONResponse:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return JSONResponse(
            {
                "status": "ok",
                "service": "hyperion",
                "version": "0.1.0",
                "llm_configured": bool(s.api_key),
                "time": now,
            }
        )

    async def _chat_impl(request: Request) -> StreamingResponse:
        raw = await _read_body(request)
        user_id, text = parse_chat_body(raw)
        try:
            ag = await get_agent()
        except Exception:
            _log.exception("agent unavailable")
            ag = None
        if ag is None:
            events = _starting_events()
        else:
            events = ag.handle(user_id, text)
        return StreamingResponse(
            sse_stream(events, on_error_text=ERROR_TEXT),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    @app.post("/chat")
    async def chat(request: Request) -> StreamingResponse:
        return await _chat_impl(request)

    @app.post("/chat/")
    async def chat_slash(request: Request) -> StreamingResponse:
        return await _chat_impl(request)

    @app.get("/debug/turns")
    async def debug_turns(request: Request) -> JSONResponse:
        if not s.debug_endpoints:
            return JSONResponse({"error": "debug endpoints are disabled"}, status_code=404)
        try:
            ag = await get_agent()
        except Exception:
            _log.exception("agent unavailable")
            ag = None
        if ag is None:
            return JSONResponse({"turns": []})
        uid = request.query_params.get("user_id") or None
        raw_limit = request.query_params.get("limit")
        try:
            limit = int(raw_limit) if raw_limit is not None else 20
        except (TypeError, ValueError):
            limit = 20
        if limit < 1 or limit > 100:
            limit = 20
        return JSONResponse({"turns": ag._traces.recent(uid, limit)})  # noqa: SLF001 (same package)

    @app.get("/debug/status")
    async def debug_status() -> JSONResponse:
        if not s.debug_endpoints:
            return JSONResponse({"error": "debug endpoints are disabled"}, status_code=404)
        try:
            ag = await get_agent()
        except Exception:
            _log.exception("agent unavailable")
            ag = None
        if ag is None:
            return JSONResponse({"error": "service starting"}, status_code=503)
        return JSONResponse(await ag.status())

    return app


app = create_app()

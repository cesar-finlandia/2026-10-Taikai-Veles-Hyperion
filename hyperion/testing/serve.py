"""Background-thread HTTP server for streaming tests (httpx.ASGITransport buffers whole responses)."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import uvicorn


@contextmanager
def serve_in_thread(app: Any, *, host: str = "127.0.0.1", port: int = 0) -> Iterator[str]:
    """Run `app` with uvicorn in a background thread on a free port; yield the base URL ('http://127.0.0.1:PORT'); stop on exit.
    Needed because httpx.ASGITransport buffers whole responses and cannot test real streaming."""
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not server.started:
        if time.monotonic() > deadline:
            server.should_exit = True
            thread.join(5)
            raise RuntimeError("test server did not start within 5s")
        time.sleep(0.02)
    bound_port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://{host}:{bound_port}"
    finally:
        server.should_exit = True
        thread.join(5)

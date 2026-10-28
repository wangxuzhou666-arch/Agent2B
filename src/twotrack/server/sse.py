"""Server-sent events for the query stream.

Wire format (the console reads it with fetch + ReadableStream, not EventSource, so it can send
the bearer header):

    event: trace      data: TraceStep           one per step, as it happens
    event: response   data: QueryOut            last frame on success
    event: error      data: ErrorEnvelope       last frame on failure
    : heartbeat                                 comment line while the server is busy

The engine is synchronous and can block for as long as a model call takes, so it runs in a
worker thread and hands frames to the event loop through a queue. The loop wakes up every
``heartbeat_seconds`` even when nothing arrived, which keeps proxies and the client's idle
timer from cutting a slow query.
"""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from pydantic import BaseModel

from .errors import ErrorBody, ErrorEnvelope

log = logging.getLogger("twotrack.server")

HEARTBEAT = b": heartbeat\n\n"


def frame(event: str, payload: BaseModel) -> bytes:
    # model_dump_json never contains a raw newline, so one data line is a complete message
    return f"event: {event}\ndata: {payload.model_dump_json()}\n\n".encode()


def error_frame(code: str, message: str) -> bytes:
    return frame("error", ErrorEnvelope(error=ErrorBody(code=code, message=message)))


async def relay(
    produce: Callable[[], Iterator[Any]],
    render: Callable[[Any], bytes],
    heartbeat_seconds: float,
) -> AsyncIterator[bytes]:
    """Run ``produce()`` in a thread and yield ``render(item)`` for each item it yields."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    stop = threading.Event()

    def put(kind: str, payload: Any) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, (kind, payload))
        except RuntimeError:
            pass  # the event loop is closed: the client left and the server is shutting down

    def work() -> None:
        try:
            for item in produce():
                if stop.is_set():
                    return
                put("item", item)
        except Exception as exc:  # a worker thread cannot raise into the response: whatever
            # failed must reach the client as an `error` frame
            log.error("query stream failed: %s", type(exc).__name__, exc_info=exc)
            put("failed", None)
            return
        put("done", None)

    loop.run_in_executor(None, work)  # fire and forget: `work` reports through the queue
    try:
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), heartbeat_seconds)
            except TimeoutError:
                yield HEARTBEAT
                continue
            if kind == "item":
                try:
                    chunk = render(payload)
                except Exception as exc:  # headers are already sent, so the only way to report a
                    # rendering failure is an `error` frame
                    log.error("rendering a stream frame failed: %s", type(exc).__name__,
                              exc_info=exc)
                    yield error_frame("query_failed", "The request failed while running.")
                    return
                yield chunk
            elif kind == "failed":
                yield error_frame("query_failed", "The request failed while running.")
                return
            else:
                return
    finally:
        stop.set()  # a disconnected client stops the stream after the current graph step


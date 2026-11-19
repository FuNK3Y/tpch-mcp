"""Keep a tool call's response stream active while slow work runs.

Some MCP clients (e.g. ones built on httpx with its default 5 s read timeout) drop a Streamable
HTTP response that stays silent for a few seconds, and report "SSE stream ended without a
response". Our tools often take 5-25 s (polite pacing + real page loads). While a tool runs we
send a log notification every few seconds; it is tied to the request, so it travels on that
call's response stream and keeps the connection active. Clients ignore it.
"""
import asyncio
import logging
from collections.abc import Awaitable
from typing import Any, TypeVar

T = TypeVar("T")
log = logging.getLogger(__name__)

HEARTBEAT_S = 2.0


async def with_heartbeat(ctx: Any, work: Awaitable[T], interval: float = HEARTBEAT_S) -> T:
    """Await `work`, sending a log notification through `ctx` every `interval` seconds."""
    task = asyncio.ensure_future(work)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if done:
                return task.result()
            try:
                await ctx.info("working on toppreise.ch …")
            except Exception as e:  # noqa: BLE001 - a failed heartbeat must not fail the tool
                log.debug("heartbeat not sent: %s", e)
    finally:
        if not task.done():
            task.cancel()

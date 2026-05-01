"""Shared channel queue for routing high-signal CI failure events to MCP sessions."""

from __future__ import annotations

import asyncio

# asyncio.Queue() at module level is safe in Python 3.10+ — the queue no longer
# binds to the running event loop at instantiation time (event-loop binding was
# removed from queue types in the Python 3.10 asyncio refactor).
_channel_queue: asyncio.Queue[dict] = asyncio.Queue()


async def push_channel_event(event: dict) -> None:
    """Enqueue a high-signal CI failure event for MCP session consumers."""
    await _channel_queue.put(event)

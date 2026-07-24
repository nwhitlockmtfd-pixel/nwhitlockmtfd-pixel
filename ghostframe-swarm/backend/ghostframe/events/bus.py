"""Event bus.

v0.1 ships the in-memory implementation (dev mode / single process).
The Redis Streams implementation targets the same interface.

Subscribers never block publishers: each subscriber gets its own unbounded
queue drained by its own task. A slow consumer delays itself only.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from .envelope import Event

log = logging.getLogger("ghostframe.events")

Handler = Callable[[Event], Awaitable[None]]


class EventBus(Protocol):
    async def publish(self, event: Event) -> None: ...

    def subscribe(self, kinds: list[str], handler: Handler, *, name: str = "") -> None: ...

    async def close(self) -> None: ...


class _Subscription:
    def __init__(self, kinds: list[str], handler: Handler, name: str) -> None:
        self.kinds = kinds
        self.handler = handler
        self.name = name
        self.queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self.task: asyncio.Task | None = None

    def matches(self, kind: str) -> bool:
        return any(fnmatch.fnmatch(kind, pat) for pat in self.kinds)

    async def run(self) -> None:
        while True:
            event = await self.queue.get()
            if event is None:
                return
            try:
                await self.handler(event)
            except Exception:  # a broken consumer must not stop the bus
                log.exception("subscriber %r failed on event %s (%s)",
                              self.name, event.id, event.kind)


class InMemoryEventBus:
    def __init__(self) -> None:
        self._subs: list[_Subscription] = []
        self._closed = False

    def subscribe(self, kinds: list[str], handler: Handler, *, name: str = "") -> None:
        # Consumer task starts lazily on first publish, so subscribing is
        # legal before any event loop is running (e.g. at wiring time).
        self._subs.append(_Subscription(kinds, handler, name or handler.__qualname__))

    async def publish(self, event: Event) -> None:
        if self._closed:
            raise RuntimeError("bus is closed")
        for sub in self._subs:
            if sub.task is None:
                sub.task = asyncio.ensure_future(sub.run())
            if sub.matches(event.kind):
                sub.queue.put_nowait(event)

    async def drain(self) -> None:
        """Wait until every subscriber has processed everything published so far."""
        while any(not sub.queue.empty() for sub in self._subs):
            await asyncio.sleep(0.01)

    async def close(self) -> None:
        self._closed = True
        await self.drain()
        for sub in self._subs:
            if sub.task is not None:
                sub.queue.put_nowait(None)
        for sub in self._subs:
            if sub.task is not None:
                await sub.task
        self._subs.clear()

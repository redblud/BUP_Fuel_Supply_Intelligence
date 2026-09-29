"""Background synchronization: SSE hints plus a slow REST poll keep the cached snapshot current. Owner: Developer 1.

Two loops share one wake-up event:

- listener: reads `/v1/stream`; any change event wakes the refresher. On disconnect it reconnects with jittered
  backoff, and every (re)connect wakes the refresher so a full REST resync follows (there is no event replay).
- refresher: runs `StateService.refresh()` whenever woken, and on a timer when SSE is silent or down. After an
  untrusted or failed read it retries on the shorter resync interval instead of waiting out the slow poll.

SSE is a hint, REST is truth: nothing here builds state from event payloads.
"""

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable

from app.simulator.errors import SimulatorError
from app.simulator.sse import CHANGE_EVENTS, SseEvent, is_reset_notice, tick_of
from app.state.service import StateService

log = logging.getLogger(__name__)


class StateSync:
    def __init__(
        self,
        service: StateService,
        *,
        sse_enabled: bool = True,
        fallback_interval: float = 5.0,
        resync_interval: float = 1.0,
        min_refresh_gap: float = 0.25,
        backoff_base: float = 0.5,
        backoff_max: float = 10.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._service = service
        self._sse_enabled = sse_enabled
        self._fallback_interval = fallback_interval
        self._resync_interval = resync_interval
        self._min_gap = min_refresh_gap
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._sleep = sleep
        self._jitter = jitter
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        """Start the loops. Must be called from a running event loop."""
        self._service.sync_active = True
        self._tasks.append(asyncio.create_task(self._refresh_loop(), name="state-refresh"))
        if self._sse_enabled:
            self._service.sse_status = "connecting"
            self._tasks.append(asyncio.create_task(self._listen_loop(), name="sse-listen"))

    async def stop(self) -> None:
        self._service.sync_active = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def request_refresh(self) -> None:
        """Ask for a full REST resync as soon as possible."""
        self._wake.set()

    # ------------------------------------------------------------------ loops

    async def _refresh_loop(self) -> None:
        while True:
            self._wake.clear()  # cleared before the read so an event arriving during it triggers another
            await self._refresh_once()
            interval = self._resync_interval if self._service.needs_resync else self._fallback_interval
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), interval)
            await self._sleep(self._min_gap)  # coalesce a burst of tick events into one refresh

    async def _refresh_once(self) -> None:
        try:
            await self._service.refresh()
        except Exception:  # noqa: BLE001 - the loop must survive anything a refresh throws
            log.exception("state refresh crashed")
            self._service.needs_resync = True

    async def _listen_loop(self) -> None:
        service = self._service
        attempt = 0
        while True:
            try:
                async for event in service.client.stream_events():
                    if service.sse_status != "connected":
                        service.sse_status = "connected"
                        attempt = 0
                        self._wake.set()  # (re)connected: full REST resync, events in between were lost
                    self._handle(event)
                log.warning("SSE stream ended")
            except SimulatorError as exc:
                log.warning("SSE stream failed: %s", exc)
            service.sse_status = "reconnecting"
            self._wake.set()  # keep REST fresh while the stream is down
            await self._sleep(self._jitter() * min(self._backoff_max, self._backoff_base * 2**attempt))
            attempt += 1

    def _handle(self, event: SseEvent) -> None:
        tick = tick_of(event)
        if tick is not None:
            self._service.note_sse_tick(tick)
        if is_reset_notice(event):
            self._service.note_reset_notice()
            self._wake.set()
        elif event.event in CHANGE_EVENTS:
            self._wake.set()

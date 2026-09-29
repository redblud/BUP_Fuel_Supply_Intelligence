"""Server-Sent Events from the simulator `/v1/stream`. Owner: Developer 1.

SSE is a hint, REST is truth: an event only tells `StateSync` that something changed, and a full REST
refresh follows. There is no replay and slow consumers are dropped, so a reconnect always triggers a resync.

Wire protocol (integration guide §6.1): `: connected` and keepalive comments, then `event:` / `data:` lines
ended by a blank line.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

# Events after which the world may have changed (trigger a REST refresh).
CHANGE_EVENTS = frozenset({"simulation.tick", "allocation.status_changed", "inventory.updated"})


@dataclass(frozen=True)
class SseEvent:
    """`event` is the SSE event name, or "comment" for `: ...` lines (connected / keepalive). `data` is decoded JSON when possible."""

    event: str
    data: Any = None


async def parse_sse_lines(lines: AsyncIterator[str]) -> AsyncIterator[SseEvent]:
    """Turn raw text lines into events. Comments are yielded as event "comment"; malformed JSON is passed through as text."""
    name = "message"
    data: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            if data:
                yield SseEvent(name, _decode("\n".join(data)))
            name, data = "message", []
        elif line.startswith(":"):
            yield SseEvent("comment", line[1:].strip())
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        # other fields (id:, retry:) are not used by the simulator


def _decode(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def tick_of(event: SseEvent) -> int | None:
    """The simulator tick a `simulation.tick` event reports, if it says so."""
    if event.event == "simulation.tick" and isinstance(event.data, dict) and isinstance(event.data.get("tick"), int):
        return event.data["tick"]
    return None


def is_reset_notice(event: SseEvent) -> bool:
    """`simulator.notice` announcing a reset (`POST /admin/reset` publishes "Simulation reset")."""
    return event.event == "simulator.notice" and isinstance(event.data, dict) and "reset" in str(event.data.get("message", "")).lower()

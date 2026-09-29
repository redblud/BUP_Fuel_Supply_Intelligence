"""Test doubles: a scriptable simulator client built on the recorded fixtures."""

import copy
import json
from collections import Counter
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

from app.domain.models import Allocation
from app.simulator.client import SimResponse
from app.simulator.errors import SimulatorError
from app.simulator.sse import SseEvent
from tests.conftest import FIXTURES


def fixture_data(scenario: str = "route-disruption") -> dict[str, Any]:
    return json.loads((FIXTURES / f"{scenario}.json").read_text(encoding="utf-8"))


class ScriptedClient:
    """A simulator whose responses the test controls: tick, staleness, failures, allocations, and an SSE script."""

    def __init__(self, scenario: str = "route-disruption") -> None:
        self.data = copy.deepcopy(fixture_data(scenario))
        self._allocation_template = copy.deepcopy(self.data["allocations"][0]) if self.data["allocations"] else {}
        self.stale = False
        self.error: SimulatorError | None = None
        self.instance_ticks: list[int] = []  # consumed one per instance read, to simulate the tick moving mid-snapshot
        self.reads: Counter[str] = Counter()
        self.stream_scripts: list[list[SseEvent] | Exception] = []  # one entry per connection attempt
        self.stream_attempts = 0
        self.stream_queue: Any = None  # asyncio.Queue: a connection stays open after its script and yields queued events/raises queued errors

    def advance(self, tick: int) -> None:
        """Move the simulator to `tick`, adding that tick's demand rows (12 per tick, newest first)."""
        instance = self.data["instance"]
        sim_time = datetime.fromisoformat(instance["sim_time"]) + timedelta(minutes=instance["tick_minutes"] * (tick - instance["tick"]))
        instance.update(tick=tick, sim_time=sim_time.isoformat())
        template = [row for row in self.data["demand-history"] if row["tick"] == max(r["tick"] for r in self.data["demand-history"])]
        fresh = [{**row, "tick": tick, "sim_time": sim_time.isoformat()} for row in template]
        self.data["demand-history"] = (fresh + self.data["demand-history"])[:200]

    def reset(self) -> None:
        """Simulate /admin/reset: back to tick 0, no allocations, no history (same scenario and seed)."""
        self.data["instance"].update(tick=0, sim_time="2026-01-01T00:00:00+00:00")
        self.data["allocations"] = []
        self.data["demand-history"] = []

    def set_allocation_status(self, allocation_id: int, status: str, **fields: Any) -> None:
        for allocation in self.data["allocations"]:
            if allocation["id"] == allocation_id:
                allocation.update(status=status, **fields)

    def add_allocation(self, allocation_id: int, key: str, status: str = "PENDING", **fields: Any) -> None:
        base = copy.deepcopy(self._allocation_template)
        base.update(id=allocation_id, idempotency_key=key, status=status, created_tick=self.data["instance"]["tick"], **fields)
        self.data["allocations"].append(base)

    async def get_health(self) -> dict:
        if self.error:
            raise self.error
        return self.data["health"]

    async def get(self, resource: str, **params: Any) -> SimResponse:
        if self.error:
            raise self.error
        self.reads[resource] += 1
        body = copy.deepcopy(self.data[resource])
        if resource == "instance" and self.instance_ticks:
            body["tick"] = self.instance_ticks.pop(0)
        return SimResponse(body, self.stale)

    async def create_allocation(self, body: dict) -> Allocation:
        raise SimulatorError("FAKE_READ_ONLY", "not scripted", 501)

    async def cancel_allocation(self, allocation_id: int) -> Allocation:
        raise SimulatorError("FAKE_READ_ONLY", "not scripted", 501)

    async def admin(self, action: str) -> dict:
        return {"status": action}

    async def stream_events(self) -> AsyncIterator[SseEvent]:
        self.stream_attempts += 1
        script = self.stream_scripts.pop(0) if self.stream_scripts else []
        if isinstance(script, Exception):
            raise script
        for event in script:
            yield event
        if self.stream_queue is not None:
            while True:
                item = await self.stream_queue.get()
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item

    async def close(self) -> None:
        return None

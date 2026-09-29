"""DemoSimulatorClient: a small stateful stand-in for the real simulator, for demos and offline development.

Starts from a fixture scenario, then behaves like a simulator: `admin("step")` advances a tick (stations burn fuel from
the newest demand rows, allocations move PENDING -> IN_TRANSIT -> ARRIVED and deliver their fuel), `admin("run")` ticks
in the background, `create_allocation` is idempotent by key and draws down the depot. It is not the real simulator and
does not model its full validation; the real one is used with SIMULATOR_MODE=real.
"""

import asyncio
import contextlib
import copy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.domain.models import Allocation
from app.simulator.errors import SimulatorError
from app.simulator.fake import FakeSimulatorClient, load_fixture

TICK_SECONDS = 2.0  # wall clock per tick while running


class DemoSimulatorClient(FakeSimulatorClient):
    def __init__(self, fixture_dir: Path, scenario: str, tick_seconds: float = TICK_SECONDS) -> None:
        super().__init__(fixture_dir, scenario)
        self._d: dict[str, Any] = copy.deepcopy(load_fixture(fixture_dir, scenario))
        self._d.pop("stale", None)
        self._tick_seconds = tick_seconds
        self._runner: asyncio.Task[None] | None = None
        self._sync_status()

    def _data(self) -> dict[str, Any]:
        return self._d

    def _sync_status(self) -> None:
        sim = self._d["instance"]
        self._d["health"]["simulation"] = {"status": sim["status"], "tick": sim["tick"]}

    def _tick_once(self) -> dict[str, Any]:
        d = self._d
        inst = d["instance"]
        tick = inst["tick"] + 1
        inst["tick"] = tick
        inst["sim_time"] = (datetime.fromisoformat(inst["sim_time"]) + timedelta(minutes=inst["tick_minutes"])).isoformat()
        history: list[dict[str, Any]] = d["demand-history"]
        if history:
            newest = max(r["tick"] for r in history)
            rows = [{**r, "tick": tick, "sim_time": inst["sim_time"]} for r in history if r["tick"] == newest]
            d["demand-history"] = (rows + history)[:200]
            burn = {(r["station_id"], r["fuel_type"]): r["served_liters"] for r in rows}
            for station in d["stations"]:
                for fuel in station["inventory"]:
                    station["inventory"][fuel] = max(station["inventory"][fuel] - burn.get((station["id"], fuel), 0.0), 0.0)
        for alloc in d["allocations"]:
            if alloc["status"] == "PENDING":
                alloc.update(status="IN_TRANSIT", departure_tick=tick)
            if alloc["status"] == "IN_TRANSIT" and tick >= (alloc["expected_arrival_tick"] or tick):
                station = next(s for s in d["stations"] if s["id"] == alloc["destination_station_id"])
                cap = station["capacity"][alloc["fuel_type"]]
                station["inventory"][alloc["fuel_type"]] = min(station["inventory"][alloc["fuel_type"]] + alloc["quantity"], cap)
                alloc.update(status="ARRIVED", actual_arrival_tick=tick)
        self._sync_status()
        return {"tick": tick, "sim_time": inst["sim_time"]}

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._tick_seconds)
            self._tick_once()

    async def create_allocation(self, body: dict) -> Allocation:
        d = self._d
        existing = next((a for a in d["allocations"] if a["idempotency_key"] == body["idempotency_key"]), None)
        if existing is not None:
            return Allocation.model_validate(existing)
        route = next((r for r in d["routes"] if r["id"] == body["route_id"]), None)
        depot = next((x for x in d["depots"] if x["id"] == body["source_depot_id"]), None)
        if route is None or depot is None:
            raise SimulatorError("NOT_FOUND", "Unknown route or depot.", 404)
        if route["status"] != "AVAILABLE":
            raise SimulatorError("ROUTE_DISRUPTED", f"{route['id']} is disrupted.", 409)
        fuel, qty = body["fuel_type"], float(body["quantity"])
        if depot["inventory"][fuel] < qty:
            raise SimulatorError("INSUFFICIENT_INVENTORY", "The depot cannot cover this quantity.", 422)
        depot["inventory"][fuel] -= qty
        tick = d["instance"]["tick"]
        alloc = {
            "id": max([a["id"] for a in d["allocations"]], default=0) + 1,
            "idempotency_key": body["idempotency_key"],
            "source_depot_id": body["source_depot_id"],
            "destination_station_id": body["destination_station_id"],
            "route_id": body["route_id"],
            "fuel_type": fuel,
            "quantity": qty,
            "created_tick": tick,
            "departure_tick": None,
            "expected_arrival_tick": tick + 1 + route["transit_ticks"],
            "actual_arrival_tick": None,
            "status": "PENDING",
            "failure_reason": None,
        }
        d["allocations"].append(alloc)
        return Allocation.model_validate(alloc)

    async def admin(self, action: str) -> dict:
        inst = self._d["instance"]
        if action == "step":
            return self._tick_once()
        if action == "run":
            inst["status"] = "RUNNING"
            if self._runner is None or self._runner.done():
                self._runner = asyncio.create_task(self._run(), name="demo-sim-runner")
        elif action == "pause":
            inst["status"] = "PAUSED"
            await self._stop_runner()
        else:
            raise SimulatorError("UNSUPPORTED", f"The demo simulator has no '{action}' control.", 501)
        self._sync_status()
        return {"status": inst["status"]}

    async def _stop_runner(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner
            self._runner = None

    async def close(self) -> None:
        await self._stop_runner()

"""The demo simulator behaves enough like a simulator to drive an end-to-end demo: idempotent POST, stepping, arrival."""

import pytest

from app.simulator.demo import DemoSimulatorClient
from app.simulator.errors import SimulatorError
from tests.conftest import FIXTURES


@pytest.fixture
def sim() -> DemoSimulatorClient:
    return DemoSimulatorClient(FIXTURES, "normal")


def body(key: str = "k1", qty: float = 3000.0) -> dict:
    return {
        "source_depot_id": "depot-gazipur",
        "destination_station_id": "station-tongi",
        "route_id": "route-gazipur-tongi",
        "fuel_type": "DIESEL",
        "quantity": qty,
        "idempotency_key": key,
    }


async def test_post_is_idempotent_and_draws_down_the_depot(sim) -> None:
    before = next(d for d in sim._d["depots"] if d["id"] == "depot-gazipur")["inventory"]["DIESEL"]
    first = await sim.create_allocation(body())
    again = await sim.create_allocation(body())
    assert first.id == again.id and len(sim._d["allocations"]) == 1
    after = next(d for d in sim._d["depots"] if d["id"] == "depot-gazipur")["inventory"]["DIESEL"]
    assert after == before - 3000.0


async def test_allocation_travels_and_delivers(sim) -> None:
    alloc = await sim.create_allocation(body())
    station = next(s for s in sim._d["stations"] if s["id"] == "station-tongi")
    inv = station["inventory"]["DIESEL"]
    ticks = 0
    while sim._d["allocations"][0]["status"] != "ARRIVED":
        await sim.admin("step")
        ticks += 1
        assert ticks < 20
    assert alloc.status == "PENDING" and sim._d["allocations"][0]["actual_arrival_tick"] is not None
    assert station["inventory"]["DIESEL"] > inv - 100000


async def test_disrupted_route_and_unknown_control_are_refused(sim) -> None:
    disrupted = next(r for r in sim._d["routes"])
    disrupted["status"] = "DISRUPTED"
    with pytest.raises(SimulatorError) as exc:
        await sim.create_allocation({**body(), "route_id": disrupted["id"], "source_depot_id": disrupted["source_depot_id"]})
    assert exc.value.code == "ROUTE_DISRUPTED"
    with pytest.raises(SimulatorError):
        await sim.admin("reset")

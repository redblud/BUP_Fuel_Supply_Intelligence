"""Fault scenarios through the whole API: what an operator sees while the simulator misbehaves.

The simulator is a ScriptedClient that injects the documented fault types (unavailable, error_rate, latency,
stale_data, torn snapshots, stream_disconnect). tests/scenarios/real_faults.py runs the same checks against the real
simulator's /admin/faults.
"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.simulator.errors import SimulatorError
from app.state.service import StateService
from tests.helpers import ScriptedClient

UNAVAILABLE = SimulatorError("FAULT_INJECTED", "Simulator API temporarily unavailable.", 503)


class FaultyClient(ScriptedClient):
    """ScriptedClient plus the fault types the real simulator can inject."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_next = 0  # error_rate: this many upcoming reads fail with a retryable 503
        self.delay = 0.0  # latency

    async def get(self, resource, **params):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_next:
            self.fail_next -= 1
            raise SimulatorError("FAULT_INJECTED", "Injected transient API error.", 503)
        return await super().get(resource, **params)


@pytest.fixture
def api(tmp_path):
    made: list[TestClient] = []

    def make() -> tuple[TestClient, FaultyClient]:
        settings = Settings(simulator_mode="fake", database_url=f"sqlite+aiosqlite:///{tmp_path / 'f.db'}", operator_token="t")
        c = TestClient(create_app(settings), headers={"X-Operator-Token": "t"})
        c.__enter__()
        made.append(c)
        ctx = c.app.state.ctx
        sim = FaultyClient()
        ctx.state = StateService(sim, tick_tolerance=1, fixture=False, db=ctx.db, history_ticks=settings.demand_history_ticks)
        return c, sim

    yield make
    for c in made:
        c.__exit__(None, None, None)


def dash(c: TestClient) -> dict:
    return c.get("/api/dashboard").json()


def codes(d: dict) -> list[str]:
    return [t["code"] for t in d["tripwire"]["trips"]]


def test_unavailable_with_no_history_shows_no_state_and_trips(api) -> None:
    c, sim = api()
    sim.error = UNAVAILABLE
    d = dash(c)
    assert d["state"] is None and d["plan"] is None
    assert d["tripwire"]["state"] == "TRIPPED" and "SIMULATOR_UNAVAILABLE" in codes(d)
    assert d["health"]["components"]["simulator"]["status"] == "DOWN"


def test_unavailable_after_a_good_read_labels_the_old_snapshot_and_trips(api) -> None:
    c, sim = api()
    assert dash(c)["state"]["meta"]["freshness"] == "FRESH"
    sim.error = UNAVAILABLE
    d = dash(c)
    assert d["state"] is None or d["state"]["meta"]["freshness"] == "UNAVAILABLE"  # never presented as live
    assert d["tripwire"]["state"] == "TRIPPED" and "SIMULATOR_UNAVAILABLE" in codes(d)


def test_recovery_after_an_outage_clears_the_trip(api) -> None:
    c, sim = api()
    dash(c)
    sim.error = UNAVAILABLE
    assert dash(c)["tripwire"]["state"] == "TRIPPED"
    sim.error = None
    asyncio.run(c.app.state.ctx.state.refresh())
    d = dash(c)
    assert d["tripwire"]["state"] == "CLEAR" and d["state"]["meta"]["freshness"] == "FRESH"


def test_error_rate_burst_is_reported_never_as_fresh_data(api) -> None:
    c, sim = api()
    sim.fail_next = 3
    d = dash(c)
    assert d["state"] is None or d["state"]["meta"]["freshness"] != "FRESH"
    sim.fail_next = 0
    asyncio.run(c.app.state.ctx.state.refresh())
    assert dash(c)["state"]["meta"]["freshness"] == "FRESH"


def test_latency_keeps_the_api_responsive(api) -> None:
    c, sim = api()
    sim.delay = 0.05
    dash(c)
    started = time.perf_counter()
    assert c.get("/api/health").status_code == 200
    assert time.perf_counter() - started < 2.0


def test_stale_data_is_labelled_stale_and_trips(api) -> None:
    c, sim = api()
    sim.stale = True
    d = dash(c)
    assert d["state"]["meta"]["freshness"] == "STALE"
    assert d["tripwire"]["state"] == "TRIPPED" and "SNAPSHOT_STALE" in codes(d)


def test_torn_snapshot_is_labelled_torn_and_trips(api) -> None:
    c, sim = api()
    sim.instance_ticks = [40, 45]
    d = dash(c)
    assert d["state"] is None or d["state"]["meta"]["freshness"] == "TORN"
    assert d["tripwire"]["state"] == "TRIPPED" and "SNAPSHOT_TORN" in codes(d)


def test_execution_is_refused_while_the_simulator_is_faulted(api) -> None:
    c, sim = api()
    rid = dash(c)["plan"]["recommendations"][0]["id"]
    sim.error = UNAVAILABLE
    r = c.post(f"/api/recommendations/{rid}/approve")
    assert r.status_code in (409, 503)

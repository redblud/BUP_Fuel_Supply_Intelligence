"""Supervised execution: guards, idempotency, unknown outcomes. Runs on the fake fixtures with a scripted simulator."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.models import Allocation
from app.main import create_app
from app.simulator.errors import SimulatorError
from app.simulator.fake import FakeSimulatorClient
from tests.helpers import fixture_data


class RecordingSim(FakeSimulatorClient):
    """Fixture reads plus a POST we control."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.posts: list[dict] = []
        self.fail: list[SimulatorError] = []

    async def create_allocation(self, body: dict) -> Allocation:
        self.posts.append(body)
        if self.fail:
            raise self.fail.pop(0)
        template = fixture_data()["allocations"][0]
        return Allocation.model_validate({**template, "id": 900 + len(self.posts), "idempotency_key": body["idempotency_key"], "status": "PENDING"})


@pytest.fixture
def api(tmp_path):
    def make(scenario: str = "route-disruption") -> tuple[TestClient, RecordingSim]:
        settings = Settings(simulator_mode="fake", fixture_scenario=scenario, database_url=f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")
        c = TestClient(create_app(settings))
        c.__enter__()
        ctx = c.app.state.ctx
        sim = RecordingSim(settings.fixture_dir, scenario)
        ctx.state.client = sim
        return c, sim

    return make


def first_rec_id(c: TestClient) -> str:
    return c.get("/api/dashboard").json()["plan"]["recommendations"][0]["id"]


def test_approve_creates_one_allocation_and_is_idempotent(api) -> None:
    c, sim = api()
    rid = first_rec_id(c)
    first = c.post(f"/api/recommendations/{rid}/approve")
    again = c.post(f"/api/recommendations/{rid}/approve")
    assert first.status_code == 200 and first.json()["status"] == "DONE"
    assert again.json() == first.json()
    assert len(sim.posts) == 1
    assert sim.posts[0]["idempotency_key"]


def test_kill_switch_blocks_execution(api) -> None:
    c, sim = api()
    rid = first_rec_id(c)
    c.put("/api/automation", json={"mode": "ADVISORY", "kill_switch": True})
    r = c.post(f"/api/recommendations/{rid}/approve")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "KILL_SWITCH"
    assert sim.posts == []


def test_tripwire_blocks_execution_on_stale_state(api) -> None:
    c, sim = api("stale")
    rid = c.get("/api/dashboard").json()["plan"]["recommendations"][0]["id"]
    r = c.post(f"/api/recommendations/{rid}/approve")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TRIPWIRE_TRIPPED"
    assert sim.posts == []


def test_unknown_recommendation_is_404(api) -> None:
    c, _ = api()
    assert c.post("/api/recommendations/nope/approve").status_code == 404


def test_reject_then_approve_refused(api) -> None:
    c, sim = api()
    rid = first_rec_id(c)
    assert c.post(f"/api/recommendations/{rid}/reject").json()["status"] == "REJECTED"
    r = c.post(f"/api/recommendations/{rid}/approve")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_EXECUTABLE"
    assert sim.posts == []


def test_simulator_validation_error_marks_failed(api) -> None:
    c, sim = api()
    rid = first_rec_id(c)
    sim.fail = [SimulatorError("INSUFFICIENT_DEPOT_INVENTORY", "no", 422)]
    r = c.post(f"/api/recommendations/{rid}/approve")
    assert r.status_code == 502
    states = {s["recommendation_id"]: s for s in c.get("/api/dashboard").json()["recommendation_states"]}
    assert states[rid]["status"] == "FAILED"
    assert len(sim.posts) == 1  # not retried


def test_unclear_outcome_trips_and_retries_with_same_key(api) -> None:
    c, sim = api()
    rid = first_rec_id(c)
    sim.fail = [SimulatorError("TIMEOUT", "slow"), SimulatorError("TIMEOUT", "slow")]
    r = c.post(f"/api/recommendations/{rid}/approve")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "EXECUTION_UNKNOWN"
    assert len(sim.posts) == 2 and sim.posts[0] == sim.posts[1]
    dash = c.get("/api/dashboard").json()
    assert dash["tripwire"]["state"] == "TRIPPED"
    assert "EXECUTION_UNKNOWN" in [t["code"] for t in dash["tripwire"]["trips"]]
    assert dash["health"]["components"]["execution"]["status"] == "DEGRADED"
    ok = c.post(f"/api/recommendations/{rid}/approve")  # operator retries: same key, now it works
    assert ok.json()["status"] == "DONE"
    assert sim.posts[-1]["idempotency_key"] == sim.posts[0]["idempotency_key"]


def test_guarded_auto_refused_while_tripped(api) -> None:
    c, _ = api("stale")
    r = c.put("/api/automation", json={"mode": "GUARDED_AUTO", "kill_switch": False})
    assert r.status_code == 409


def test_concurrent_same_depot_second_is_refused(api) -> None:
    c, sim = api()
    rid = first_rec_id(c)
    depot = c.get("/api/dashboard").json()["plan"]["recommendations"][0]["request"]["source_depot_id"]
    lock = asyncio.Lock()
    asyncio.run(lock.acquire())
    c.app.state.ctx.depot_locks[depot] = lock
    r = c.post(f"/api/recommendations/{rid}/approve")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "DEPOT_BUSY"
    assert sim.posts == []


def test_auto_step_executes_only_in_guarded_auto_and_when_clear(api) -> None:
    from app.decision.auto import auto_step

    c, sim = api()
    ctx = c.app.state.ctx
    assert asyncio.run(auto_step(ctx)) == []  # ADVISORY: nothing
    ctx.automation = ctx.automation.model_copy(update={"mode": "GUARDED_AUTO", "kill_switch": True})
    assert asyncio.run(auto_step(ctx)) == [] and sim.posts == []  # kill switch
    ctx.automation = ctx.automation.model_copy(update={"kill_switch": False})
    done = asyncio.run(auto_step(ctx))
    assert done and len(sim.posts) == len(done)
    assert asyncio.run(auto_step(ctx)) == []  # nothing new, no duplicate orders
    assert len(sim.posts) == len(done)


def test_auto_step_does_nothing_when_tripped(api) -> None:
    from app.decision.auto import auto_step

    c, sim = api("stale")
    ctx = c.app.state.ctx
    ctx.automation = ctx.automation.model_copy(update={"mode": "GUARDED_AUTO"})
    assert asyncio.run(auto_step(ctx)) == [] and sim.posts == []

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

TOKEN = "test-token"
AUTH = {"X-Operator-Token": TOKEN}


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
        settings = Settings(simulator_mode="fake", fixture_scenario=scenario, database_url=f"sqlite+aiosqlite:///{tmp_path / 'x.db'}",
        operator_token=TOKEN, demo_controls_enabled=True)
        c = TestClient(create_app(settings), headers=AUTH)
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


def test_database_failure_trips_persistence_and_freezes_execution(api, monkeypatch) -> None:
    from app.state import service

    c, sim = api()
    rid = first_rec_id(c)

    async def broken(*_a, **_k):
        raise OSError("disk I/O error")

    monkeypatch.setattr(service.repository, "record_allocation_transitions", broken)
    dash = c.get("/api/dashboard").json()
    assert dash["plan"]["recommendations"]  # advisory view still renders
    assert "PERSISTENCE_FAILURE" in [t["code"] for t in dash["tripwire"]["trips"]]
    r = c.post(f"/api/recommendations/{rid}/approve")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "TRIPWIRE_TRIPPED"
    assert sim.posts == []


def test_metrics_endpoint_exposes_counters(api) -> None:
    c, sim = api()
    rid = first_rec_id(c)
    c.post(f"/api/recommendations/{rid}/approve")
    text = c.get("/metrics").text
    assert "fuelops_allocation_attempts_total 1" in text
    assert 'fuelops_recommendations{status="DONE"} 1' in text
    assert "fuelops_planner_runtime_seconds" in text and "fuelops_snapshot_age_seconds" in text


def test_planner_crash_falls_back_and_dashboard_renders(api, monkeypatch) -> None:
    import app.intelligence as intelligence

    c, _ = api()
    monkeypatch.setattr(intelligence, "plan_replenishment", lambda *a, **k: 1 / 0)
    dash = c.get("/api/dashboard").json()
    assert dash["plan"]["planner_version"] == "fallback" and dash["plan"]["recommendations"] == []
    assert "PRIMARY_PLANNER_FAILED" in [t["code"] for t in dash["tripwire"]["trips"]]
    assert dash["health"]["components"]["planner"]["status"] == "DEGRADED"


def test_json_log_carries_context_fields() -> None:
    import json
    import logging

    from app.core.logging import JsonFormatter

    rec = logging.LogRecord("x", logging.INFO, "f", 1, "executed", None, None)
    rec.decision_id, rec.allocation_id, rec.component = "rec-1", 7, "decision"
    out = json.loads(JsonFormatter().format(rec))
    assert out["decision_id"] == "rec-1" and out["allocation_id"] == 7 and out["component"] == "decision" and "run_id" not in out


def restart(tmp_path, scenario: str = "route-disruption") -> tuple[TestClient, RecordingSim]:
    """A second API process on the same database file."""
    settings = Settings(simulator_mode="fake", fixture_scenario=scenario, database_url=f"sqlite+aiosqlite:///{tmp_path / 'x.db'}",
        operator_token=TOKEN, demo_controls_enabled=True)
    c = TestClient(create_app(settings), headers=AUTH)
    c.__enter__()
    sim = RecordingSim(settings.fixture_dir, scenario)
    c.app.state.ctx.state.client = sim
    return c, sim


def test_prepared_intent_is_stored_before_the_post(api, tmp_path) -> None:
    import json
    import sqlite3

    c, sim = api()
    rid = first_rec_id(c)
    seen: list[tuple] = []
    original = sim.create_allocation

    async def spy(body: dict) -> Allocation:
        con = sqlite3.connect(tmp_path / "x.db")
        seen.append(con.execute("SELECT status, message, idempotency_key, request_body FROM execution_intents").fetchone())
        con.close()
        return await original(body)

    sim.create_allocation = spy  # type: ignore[method-assign]
    assert c.post(f"/api/recommendations/{rid}/approve").status_code == 200
    status, message, key, raw = seen[0]
    assert (status, message) == ("EXECUTING", "PREPARED")
    assert json.loads(raw)["idempotency_key"] == key == sim.posts[0]["idempotency_key"]


def test_lifecycle_and_kill_switch_survive_a_restart(api, tmp_path) -> None:
    c, _ = api()
    rid = first_rec_id(c)
    assert c.post(f"/api/recommendations/{rid}/approve").json()["status"] == "DONE"
    c.put("/api/automation", json={"mode": "ADVISORY", "kill_switch": True})
    c.__exit__(None, None, None)
    c2, sim2 = restart(tmp_path)
    assert c2.get("/api/automation").json()["kill_switch"] is True
    states = {s["recommendation_id"]: s for s in c2.get("/api/dashboard").json()["recommendation_states"]}
    assert states[rid]["status"] == "DONE"
    assert sim2.posts == []


def test_crash_mid_execution_reloads_as_unknown_and_retries_with_same_key(api, tmp_path) -> None:
    c, sim = api()
    rid = first_rec_id(c)

    async def die(body: dict) -> Allocation:
        sim.posts.append(body)
        raise RuntimeError("process died")

    sim.create_allocation = die  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        c.post(f"/api/recommendations/{rid}/approve")
    key = sim.posts[0]["idempotency_key"]
    c.__exit__(None, None, None)
    c2, sim2 = restart(tmp_path)
    dash = c2.get("/api/dashboard").json()
    assert "EXECUTION_UNKNOWN" in [t["code"] for t in dash["tripwire"]["trips"]]
    ok = c2.post(f"/api/recommendations/{rid}/approve")
    assert ok.json()["status"] == "DONE"
    assert sim2.posts[0]["idempotency_key"] == key


def test_expired_recommendation_is_reported_once_as_a_warning(api) -> None:
    from app.decision import orchestrator

    c, _ = api()
    rid = first_rec_id(c)
    ctx = c.app.state.ctx
    tick = ctx.state.latest.run.tick
    expired = ctx.recommendation_states[rid].model_copy(update={"status": "EXPIRED", "updated_tick": tick})
    ctx.recommendation_states = {**ctx.recommendation_states, rid: expired}
    assert orchestrator.just_expired(ctx.recommendation_states, tick) == [rid]
    assert orchestrator.just_expired(ctx.recommendation_states, tick + 1) == []


# ---- close-out: review flags, plan approval, outcomes, degradation ------------------------------------------------------


def _rec(rec_id: str, qty: float = 1000.0, route: str = "r1", depot: str = "d1"):
    from app.domain.models import AllocationRequest, Recommendation

    base = Recommendation.model_construct(
        id=rec_id,
        station_id="s1",
        fuel_type="DIESEL",
        request=AllocationRequest.model_construct(source_depot_id=depot, route_id=route, quantity=qty),
    )
    return base


def _plan(*recs):
    from app.domain.models import Plan

    return Plan.model_construct(recommendations=list(recs))


def _proposed(rec_id: str):
    from app.domain.models import RecommendationState

    return {rec_id: RecommendationState(recommendation_id=rec_id, status="PROPOSED", updated_tick=1)}


def test_review_flags_mark_a_materially_changed_replacement() -> None:
    from app.decision.orchestrator import review_flags

    base, flagged = review_flags({}, {}, frozenset(), _plan(_rec("rec-1")))
    assert flagged == frozenset()
    _, flagged = review_flags(base, _proposed("rec-1"), frozenset(), _plan(_rec("rec-2", qty=1050.0)))
    assert flagged == frozenset()  # 5% is not material
    _, flagged = review_flags(base, _proposed("rec-1"), frozenset(), _plan(_rec("rec-2", qty=1500.0)))
    assert flagged == frozenset({"rec-2"})
    _, flagged = review_flags(base, _proposed("rec-1"), frozenset(), _plan(_rec("rec-2", route="r2")))
    assert flagged == frozenset({"rec-2"})


def test_review_flag_is_inherited_and_not_raised_for_an_executed_predecessor() -> None:
    from app.decision.orchestrator import review_flags
    from app.domain.models import RecommendationState

    base, _ = review_flags({}, {}, frozenset(), _plan(_rec("rec-2")))
    _, flagged = review_flags(base, _proposed("rec-2"), frozenset({"rec-2"}), _plan(_rec("rec-3")))
    assert flagged == frozenset({"rec-3"})
    done = {"rec-2": RecommendationState(recommendation_id="rec-2", status="DONE", updated_tick=1)}
    _, flagged = review_flags(base, done, frozenset(), _plan(_rec("rec-3", qty=5000.0)))
    assert flagged == frozenset()


def test_guarded_auto_skips_flagged_recommendations_and_audits_actions(api, tmp_path) -> None:
    import sqlite3

    from app.decision.auto import auto_step

    c, sim = api()
    rid = first_rec_id(c)
    ctx = c.app.state.ctx
    ctx.review_required = frozenset(r["id"] for r in c.get("/api/dashboard").json()["plan"]["recommendations"])
    ctx.automation = ctx.automation.model_copy(update={"mode": "GUARDED_AUTO"})
    assert asyncio.run(auto_step(ctx)) == [] and sim.posts == []
    ctx.review_required = frozenset()
    assert rid in asyncio.run(auto_step(ctx))
    con = sqlite3.connect(tmp_path / "x.db")
    actors = [r[0] for r in con.execute("SELECT detail FROM system_events WHERE kind = 'operator_action'")]
    con.close()
    assert any("guarded-auto" in a and "auto_approve" in a for a in actors)


def test_plan_approval_needs_manual_demo_and_revalidates_each_recommendation(api) -> None:
    c, sim = api()
    assert c.post("/api/plan/approve").json()["detail"]["code"] == "NOT_MANUAL_DEMO"
    c.put("/api/automation", json={"mode": "MANUAL_DEMO", "kill_switch": False})
    recs = c.get("/api/dashboard").json()["plan"]["recommendations"]
    r = c.post("/api/plan/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["stopped_early"] is False
    assert [i["outcome"] for i in body["items"]] == ["EXECUTED"] * len(recs) or all(i["outcome"] in ("EXECUTED", "REFUSED") for i in body["items"])
    assert len(sim.posts) == sum(1 for i in body["items"] if i["outcome"] == "EXECUTED")


def test_plan_approval_stops_on_kill_switch(api) -> None:
    c, sim = api()
    c.put("/api/automation", json={"mode": "MANUAL_DEMO", "kill_switch": True})
    body = c.post("/api/plan/approve").json()
    assert body["stopped_early"] is True and body["items"][0]["code"] == "KILL_SWITCH"
    assert sim.posts == []


def test_done_recommendation_reports_the_observed_allocation_lifecycle(api) -> None:
    from types import SimpleNamespace

    c, _ = api()
    rid = first_rec_id(c)
    done = c.post(f"/api/recommendations/{rid}/approve").json()
    assert done["allocation_status"] is None

    async def tracked(**_kw):
        return SimpleNamespace(allocation=SimpleNamespace(status="ARRIVED"))

    c.app.state.ctx.state.allocation_status = tracked
    states = {s["recommendation_id"]: s for s in c.get("/api/decisions").json()}
    assert states[rid]["status"] == "DONE" and states[rid]["allocation_status"] == "ARRIVED"


def test_planner_failure_degrades_to_the_fallback_and_the_dashboard_still_renders(api, monkeypatch) -> None:
    import app.intelligence as intelligence

    def boom(*_a, **_k):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(intelligence, "plan_replenishment", boom)
    c, _ = api()
    r = c.get("/api/dashboard")
    body = r.json()
    assert r.status_code == 200 and body["plan"]["planner_version"] == "fallback"
    assert "PRIMARY_PLANNER_FAILED" in [t["code"] for t in body["tripwire"]["trips"]]
    assert body["health"]["components"]["planner"]["status"] == "DEGRADED"
    assert body["state"] is not None  # the advisory view still has data

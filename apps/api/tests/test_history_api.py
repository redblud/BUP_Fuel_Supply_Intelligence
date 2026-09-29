"""GET /api/allocations, /events, /demand-history, /decisions."""

from app.domain.models import RecommendationState


def test_allocations_lists_tracked_lifecycle(client) -> None:
    with client() as c:
        c.get("/api/dashboard")  # one refresh records what the simulator reported
        body = c.get("/api/allocations").json()
        in_transit = c.get("/api/allocations", params={"status": "IN_TRANSIT"}).json()
        arrived = c.get("/api/allocations", params={"status": "ARRIVED"}).json()
    assert [a["allocation"]["id"] for a in body] == [1]
    assert body[0]["transitions"][0]["status"] == "IN_TRANSIT"
    assert len(in_transit) == 1 and arrived == []


def test_allocations_rejects_unknown_status(client) -> None:
    with client() as c:
        assert c.get("/api/allocations", params={"status": "NOPE"}).status_code == 422


def test_events_sorted_and_filterable(client) -> None:
    with client() as c:
        all_events = c.get("/api/events").json()
        active = c.get("/api/events", params={"status": "ACTIVE"}).json()
    assert [e["start_tick"] for e in all_events] == sorted((e["start_tick"] for e in all_events), reverse=True)
    assert active and all(e["status"] == "ACTIVE" for e in active)


def test_demand_history_filters_and_limits(client) -> None:
    with client() as c:
        c.get("/api/dashboard")
        newest = c.get("/api/demand-history", params={"limit": 12}).json()
        one = c.get("/api/demand-history", params={"station_id": "station-tongi", "fuel_type": "DIESEL"}).json()
        bad = c.get("/api/demand-history", params={"limit": 5000})
    assert len(newest) == 12 and {o["tick"] for o in newest} == {40}
    assert one and all(o["station_id"] == "station-tongi" and o["fuel_type"] == "DIESEL" for o in one)
    assert [o["tick"] for o in one] == sorted(o["tick"] for o in one)
    assert bad.status_code == 422


def test_endpoints_report_503_when_no_state_exists(client) -> None:
    with client("does-not-exist") as c:
        for path in ("/api/allocations", "/api/events", "/api/demand-history"):
            response = c.get(path)
            assert response.status_code == 503, path
            assert response.json()["detail"]["code"]


def test_decisions_lists_recommendation_states_newest_first(client) -> None:
    with client() as c:
        states = c.app.state.ctx.recommendation_states
        states["r1"] = RecommendationState(recommendation_id="r1", status="PROPOSED", updated_tick=38)
        states["r2"] = RecommendationState(recommendation_id="r2", status="DONE", updated_tick=40, allocation_id=1)
        body = c.get("/api/decisions").json()
        done = c.get("/api/decisions", params={"status": "DONE"}).json()
    assert [d["recommendation_id"] for d in body] == ["r2", "r1"]
    assert [d["recommendation_id"] for d in done] == ["r2"]

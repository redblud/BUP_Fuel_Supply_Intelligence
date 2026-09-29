def test_health(client) -> None:
    with client() as c:
        body = c.get("/api/health").json()
    assert body["components"]["api"]["status"] == "HEALTHY"
    assert body["components"]["database"]["status"] == "HEALTHY"
    assert body["components"]["simulator"]["status"] == "HEALTHY"


def test_state_uses_fixture(client) -> None:
    with client() as c:
        body = c.get("/api/state").json()
    assert body["run"]["tick"] == 40
    assert body["meta"]["freshness"] == "FIXTURE"


def test_dashboard_has_plan_and_clear_tripwire(client) -> None:
    with client() as c:
        body = c.get("/api/dashboard").json()
    assert body["plan"]["recommendations"]
    assert body["tripwire"]["state"] == "CLEAR"
    assert body["automation"]["mode"] == "ADVISORY"


def test_stale_fixture_trips(client) -> None:
    with client("stale") as c:
        body = c.get("/api/dashboard").json()
    assert body["tripwire"]["state"] == "TRIPPED"
    assert "SNAPSHOT_STALE" in [t["code"] for t in body["tripwire"]["trips"]]
    assert body["health"]["components"]["snapshot"] == {"status": "DEGRADED", "detail": "STALE"}


def test_missing_fixture_degrades_not_crashes(client) -> None:
    with client("does-not-exist") as c:
        body = c.get("/api/dashboard").json()
        assert c.get("/api/state").status_code == 503
    assert body["state"] is None
    assert body["tripwire"]["trips"][0]["code"] == "SIMULATOR_UNAVAILABLE"

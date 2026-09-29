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


def test_missing_fixture_degrades_not_crashes(client) -> None:
    with client("does-not-exist") as c:
        body = c.get("/api/dashboard").json()
        assert c.get("/api/state").status_code == 503
    assert body["state"] is None
    assert body["tripwire"]["trips"][0]["code"] == "SIMULATOR_UNAVAILABLE"


def test_dashboard_keeps_last_trusted_state_when_simulator_goes_away(client) -> None:
    with client() as c:
        first = c.get("/api/dashboard").json()
        assert first["state"]["meta"]["freshness"] == "FIXTURE"
        c.app.state.ctx.state.client.scenario = "does-not-exist"  # the simulator becomes unreadable
        body = c.get("/api/dashboard").json()
    assert body["state"]["meta"]["freshness"] == "UNAVAILABLE"  # old data, labelled as such, never live
    assert body["state"]["run"]["tick"] == first["state"]["run"]["tick"]
    assert body["last_trusted_age_seconds"] is not None
    assert body["tripwire"]["state"] == "TRIPPED"
    assert "SIMULATOR_UNAVAILABLE" in [t["code"] for t in body["tripwire"]["trips"]]


def test_sse_component_is_unknown_in_fixture_mode(client) -> None:
    with client() as c:
        body = c.get("/api/health").json()
    assert body["components"]["sse"]["status"] == "UNKNOWN"

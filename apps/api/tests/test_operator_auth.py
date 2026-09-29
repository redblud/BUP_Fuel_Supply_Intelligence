"""Consequential routes need the operator token; reads stay open; each action is audited with its actor."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.api.auth import check_token
from app.core.config import Settings
from app.main import create_app

TOKEN = "s3cret"
PROTECTED = [
    ("POST", "/api/recommendations/x/approve", None),
    ("POST", "/api/recommendations/x/reject", None),
    ("PUT", "/api/automation", {"mode": "ADVISORY", "kill_switch": False}),
    ("POST", "/api/sim/control", {"action": "pause"}),
]


@pytest.fixture
def make(tmp_path):
    def build(token: str = TOKEN, demo: bool = True) -> TestClient:
        settings = Settings(
            simulator_mode="fake",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'a.db'}",
            operator_token=token,
            demo_controls_enabled=demo,
        )
        c = TestClient(create_app(settings))
        c.__enter__()
        return c

    return build


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
def test_no_token_and_wrong_token_are_401_in_the_shared_error_shape(make, method, path, body) -> None:
    c = make()
    missing = c.request(method, path, json=body)
    wrong = c.request(method, path, json=body, headers={"X-Operator-Token": "nope"})
    assert missing.status_code == 401 and missing.json()["detail"]["code"] == "OPERATOR_TOKEN_REQUIRED"
    assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "OPERATOR_TOKEN_INVALID"


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
def test_routes_stay_closed_when_no_token_is_configured(make, method, path, body) -> None:
    r = make(token="").request(method, path, json=body, headers={"X-Operator-Token": ""})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "OPERATOR_AUTH_NOT_CONFIGURED"


@pytest.mark.parametrize(("method", "path", "body"), PROTECTED)
def test_right_token_passes_the_gate(make, method, path, body) -> None:
    r = make().request(method, path, json=body, headers={"X-Operator-Token": TOKEN})
    assert r.status_code not in (401, 403)


def test_read_endpoints_stay_open(make) -> None:
    c = make()
    assert c.get("/api/dashboard").status_code == 200
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/automation").status_code == 200


def test_demo_controls_are_403_unless_enabled_even_with_the_token(make) -> None:
    r = make(demo=False).post("/api/sim/control", json={"action": "pause"}, headers={"X-Operator-Token": TOKEN})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "DEMO_CONTROLS_DISABLED"


def test_actions_are_audited_with_the_actor(make, tmp_path) -> None:
    c = make()
    headers = {"X-Operator-Token": TOKEN, "X-Operator-Name": "rahim"}
    c.put("/api/automation", json={"mode": "ADVISORY", "kill_switch": True}, headers=headers)
    c.post("/api/recommendations/nope/reject", headers=headers)
    con = sqlite3.connect(tmp_path / "a.db")
    rows = [json.loads(d) for (d,) in con.execute("SELECT detail FROM system_events WHERE kind = 'operator_action' ORDER BY id")]
    con.close()
    assert [(r["actor"], r["action"]) for r in rows] == [("rahim", "set_automation"), ("rahim", "reject")]
    assert rows[1]["outcome"].startswith("refused:")


def test_check_token_is_pure() -> None:
    assert check_token("a", "a") is None
    assert check_token("a", "b") is not None and check_token("a", None) is not None and check_token("", "a") is not None

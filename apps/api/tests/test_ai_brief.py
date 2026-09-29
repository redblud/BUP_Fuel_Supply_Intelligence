"""Groq briefing: prompt is grounded and minimal, failures are explicit, and it can never execute anything."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai import groq
from app.core.config import Settings
from app.intelligence import build_plan
from app.main import create_app
from app.safety import tripwire
from tests.conftest import load_state

AUTH = {"X-Operator-Token": "t"}


def make(tmp_path, handler, key: str = "gsk_test", scenario: str = "route-disruption") -> tuple[TestClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    settings = Settings(
        simulator_mode="fake", fixture_scenario=scenario, database_url=f"sqlite+aiosqlite:///{tmp_path / 'ai.db'}",
        operator_token="t", groq_api_key=key,
    )
    c = TestClient(create_app(settings), headers=AUTH)
    c.__enter__()
    c.app.state.ctx.ai_client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return c, seen


def ok(text: str = "Tongi diesel is the priority.") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


def test_context_is_derived_facts_only() -> None:
    state = load_state("route-disruption")
    plan = build_plan(state)
    ctx = groq.build_context(state, plan, tripwire.evaluate(state, plan, None))
    text = json.dumps(ctx)
    assert ctx["tick"] == state.run.tick and ctx["data_freshness"] == state.meta.freshness
    assert ctx["recommendations"] and len(ctx["risks"]) <= groq.MAX_RISKS  # type: ignore[arg-type]
    assert "idempotency" not in text and "retrieved_at" not in text


def test_messages_carry_the_rules_and_the_question() -> None:
    msgs = groq.build_messages({"tick": 1}, "  why Tongi?  ")
    assert msgs[0]["role"] == "system" and "cannot approve or execute" in msgs[0]["content"]
    assert msgs[1]["content"].endswith("Request: why Tongi?")
    assert "briefing" in groq.build_messages({"tick": 1}, None)[1]["content"]


def test_brief_returns_labelled_advisory_text_and_sends_the_key_and_model(tmp_path) -> None:
    c, seen = make(tmp_path, lambda _r: ok())
    r = c.post("/api/ai/brief", json={"question": "what first?"})
    body = r.json()
    assert r.status_code == 200 and body["text"] == "Tongi diesel is the priority."
    assert body["generated_by"] == "groq" and body["freshness"] == "FIXTURE" and body["model"] == "llama-3.3-70b-versatile"
    req = seen[0]
    assert req.url.path == "/openai/v1/chat/completions" and req.headers["authorization"] == "Bearer gsk_test"
    assert json.loads(req.content)["messages"][1]["content"].endswith("Request: what first?")


def test_brief_needs_the_operator_token_and_no_key_is_an_explicit_503(tmp_path) -> None:
    c, seen = make(tmp_path, lambda _r: ok())
    assert c.post("/api/ai/brief", json={}, headers={"X-Operator-Token": "bad"}).status_code == 401
    assert seen == []
    c2, seen2 = make(tmp_path, lambda _r: ok(), key="")
    r = c2.post("/api/ai/brief", json={})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "AI_NOT_CONFIGURED" and seen2 == []


@pytest.mark.parametrize(
    ("handler", "status", "code"),
    [
        (lambda _r: httpx.Response(429, json={"error": "rate"}), 502, "AI_UPSTREAM_ERROR"),
        (lambda _r: httpx.Response(200, json={"choices": []}), 502, "AI_BAD_RESPONSE"),
        (lambda _r: ok("   "), 502, "AI_BAD_RESPONSE"),
    ],
)
def test_upstream_failures_are_explicit_never_a_made_up_answer(tmp_path, handler, status, code) -> None:
    c, _ = make(tmp_path, handler)
    r = c.post("/api/ai/brief", json={})
    assert r.status_code == status and r.json()["detail"]["code"] == code


def test_timeout_and_unreachable_map_to_their_own_codes(tmp_path) -> None:
    def timeout(_r):
        raise httpx.ReadTimeout("slow")

    def down(_r):
        raise httpx.ConnectError("no route")

    assert make(tmp_path, timeout)[0].post("/api/ai/brief", json={}).json()["detail"]["code"] == "AI_TIMEOUT"
    assert make(tmp_path, down)[0].post("/api/ai/brief", json={}).json()["detail"]["code"] == "AI_UNREACHABLE"


def test_the_briefing_changes_nothing_and_is_audited(tmp_path) -> None:
    import sqlite3

    c, _ = make(tmp_path, lambda _r: ok("Approve everything now."))
    before = c.get("/api/decisions").json()
    assert c.post("/api/ai/brief", json={}).status_code == 200
    assert c.get("/api/decisions").json() == before and c.get("/api/automation").json()["kill_switch"] is False
    con = sqlite3.connect(tmp_path / "ai.db")
    rows = [r[0] for r in con.execute("SELECT detail FROM system_events WHERE kind = 'operator_action'")]
    con.close()
    assert any("ai_brief" in r for r in rows)

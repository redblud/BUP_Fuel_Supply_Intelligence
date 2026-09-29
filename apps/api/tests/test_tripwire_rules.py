"""One test per Tripwire rule: what trips it, its severity, and the actions it requires."""

import pytest

from app.domain.models import NetworkState, Plan, RiskAssessment
from app.intelligence import build_plan
from app.safety import tripwire
from tests.conftest import load_state


def meta(state: NetworkState, **changes) -> NetworkState:
    return state.model_copy(update={"meta": state.meta.model_copy(update=changes)})


def trip(status, code: str):
    return next((t for t in status.trips if t.code == code), None)


@pytest.fixture
def state() -> NetworkState:
    return load_state("normal")


@pytest.fixture
def plan(state: NetworkState) -> Plan:
    return build_plan(state)


def test_clear_when_nothing_is_wrong(state, plan) -> None:
    status = tripwire.evaluate(state, plan, None)
    assert status.state == "CLEAR"
    assert [t for t in status.trips if t.severity == "CRITICAL"] == []


def test_snapshot_stale_is_critical(state) -> None:
    t = trip(tripwire.evaluate(meta(state, stale=True), None, None), "SNAPSHOT_STALE")
    assert t is not None and t.severity == "CRITICAL" and "FREEZE_AUTOMATION" in t.required_actions


def test_snapshot_torn_is_critical(state) -> None:
    status = tripwire.evaluate(meta(state, freshness="TORN"), None, None)
    t = trip(status, "SNAPSHOT_TORN")
    assert status.state == "TRIPPED" and t is not None and "FULL_RESYNC" in t.required_actions


def test_simulator_unavailable_without_a_state_carries_the_error() -> None:
    t = trip(tripwire.evaluate(None, None, "TIMEOUT: slow"), "SIMULATOR_UNAVAILABLE")
    assert t is not None and t.severity == "CRITICAL" and "TIMEOUT" in t.message


def test_run_id_unknown_is_critical(state) -> None:
    status = tripwire.evaluate(meta(state, run_id="  "), None, None)
    t = trip(status, "RUN_ID_UNKNOWN")
    assert status.state == "TRIPPED" and t is not None and "FREEZE_AUTOMATION" in t.required_actions


def test_persistence_failure_freezes_execution(state) -> None:
    status = tripwire.evaluate(state, None, None, persistence_error="OperationalError: disk full")
    t = trip(status, "PERSISTENCE_FAILURE")
    assert status.state == "TRIPPED" and t is not None and "disk full" in t.message


def test_execution_unknown_is_critical(state) -> None:
    status = tripwire.evaluate(state, None, None, unknown_executions=2)
    t = trip(status, "EXECUTION_UNKNOWN")
    assert status.state == "TRIPPED" and t is not None and "2 execution" in t.message


def test_recommendation_expired_is_a_warning_that_asks_for_a_replan(state) -> None:
    status = tripwire.evaluate(state, None, None, expired_recommendations=["rec-1"])
    t = trip(status, "RECOMMENDATION_EXPIRED")
    assert status.state == "CLEAR" and t is not None and t.scope == "rec-1" and t.required_actions == ["REPLAN"]


def test_primary_planner_failed_warns_and_requires_review(state, plan) -> None:
    fallback = plan.model_copy(update={"planner_version": "fallback", "warnings": ["boom"]})
    status = tripwire.evaluate(state, fallback, None)
    t = trip(status, "PRIMARY_PLANNER_FAILED")
    assert status.state == "CLEAR" and t is not None and "REQUIRE_MANUAL_REVIEW" in t.required_actions


def test_station_unreachable_warns_per_station(state, plan) -> None:
    risk = RiskAssessment.model_validate({**plan.risks[0].model_dump(), "reason_codes": ["CONNECTIVITY_RISK"]})
    status = tripwire.evaluate(state, plan.model_copy(update={"risks": [risk]}), None)
    t = trip(status, "STATION_UNREACHABLE")
    assert status.state == "CLEAR" and t is not None and t.scope == risk.station_id


def test_forecast_drift_warns_only_with_enough_scored_ticks(state, plan) -> None:
    drifting = plan.forecasts[0].model_copy(update={"error_mape": 0.9, "error_ticks": tripwire.DRIFT_MIN_TICKS})
    thin = plan.forecasts[0].model_copy(update={"error_mape": 0.9, "error_ticks": tripwire.DRIFT_MIN_TICKS - 1})
    good = plan.forecasts[0].model_copy(update={"error_mape": 0.1, "error_ticks": 8})
    status = tripwire.evaluate(state, plan.model_copy(update={"forecasts": [drifting]}), None)
    t = trip(status, "FORECAST_DRIFT")
    assert status.state == "CLEAR" and t is not None and t.severity == "WARNING"
    for forecasts in ([thin], [good]):
        assert trip(tripwire.evaluate(state, plan.model_copy(update={"forecasts": forecasts}), None), "FORECAST_DRIFT") is None

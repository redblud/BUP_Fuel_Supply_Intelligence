"""Tripwire rules fed by the state pipeline: unavailable, reset-uncertain, history gaps; plus pure state helpers."""

from datetime import UTC, datetime, timedelta

from app.domain.models import DemandObservation, HistoryGap
from app.safety import tripwire
from app.state.allocations import new_transitions, transition_tick
from app.state.history import find_history_gaps
from app.state.run_identity import apply_run_identity, detect_reset, run_id_with_sequence
from tests.conftest import load_state


def with_freshness(freshness: str):
    state = load_state("route-disruption")
    return state.model_copy(update={"meta": state.meta.model_copy(update={"freshness": freshness})})


def codes(status) -> list[str]:
    return [t.code for t in status.trips]


def test_unavailable_freshness_trips_even_with_a_state():
    status = tripwire.evaluate(with_freshness("UNAVAILABLE"), None, "TIMEOUT: slow")
    assert status.state == "TRIPPED" and "SIMULATOR_UNAVAILABLE" in codes(status)


def test_reset_uncertain_trips_and_freezes_automation():
    status = tripwire.evaluate(with_freshness("RESET_UNCERTAIN"), None, None)
    trip = next(t for t in status.trips if t.code == "RESET_UNCERTAIN")
    assert status.state == "TRIPPED" and "FREEZE_AUTOMATION" in trip.required_actions


def test_history_gap_warns_without_tripping():
    state = with_freshness("FIXTURE")
    state = state.model_copy(update={"history_gaps": [HistoryGap(station_id="station-tongi", fuel_type="DIESEL", from_tick=42, to_tick=59)]})
    status = tripwire.evaluate(state, None, None)
    assert status.state == "CLEAR"
    gap = next(t for t in status.trips if t.code == "HISTORY_GAP")
    assert gap.severity == "WARNING" and gap.scope == "station-tongi"


# ---- pure helpers ----------------------------------------------------------------------------------------------------


def obs(station: str, fuel: str, tick: int) -> DemandObservation:
    return DemandObservation(station_id=station, fuel_type=fuel, tick=tick, demand_liters=1, served_liters=1, unmet_liters=0)  # type: ignore[arg-type]


def test_gaps_are_per_series_and_only_between_observed_ticks():
    history = [obs("a", "DIESEL", t) for t in (10, 11, 15, 16)] + [obs("b", "PETROL", t) for t in (10, 11, 12)]
    gaps = find_history_gaps(history)
    assert [(g.station_id, g.from_tick, g.to_tick) for g in gaps] == [("a", 12, 14)]


def test_no_gap_for_contiguous_or_single_tick_series():
    assert find_history_gaps([obs("a", "DIESEL", 5)]) == []
    assert find_history_gaps([obs("a", "DIESEL", t) for t in range(20)]) == []
    assert find_history_gaps([]) == []


def test_detect_reset_signals():
    later = load_state("route-disruption")
    earlier = load_state("normal")
    assert detect_reset(None, later) is None
    assert detect_reset(earlier, later) is None  # forward progress
    assert "tick regressed" in detect_reset(later, earlier)
    other_seed = later.model_copy(update={"run": later.run.model_copy(update={"seed": 1})})
    assert detect_reset(later, other_seed) == "scenario or seed changed"
    earlier_time = later.model_copy(update={"run": later.run.model_copy(update={"sim_time": later.run.sim_time - timedelta(hours=1)})})
    assert "sim_time" in detect_reset(later, earlier_time)
    reused = later.model_copy(update={"allocations": [later.allocations[0].model_copy(update={"idempotency_key": "different"})]})
    assert "reused" in detect_reset(later, reused)


def test_run_id_sequence():
    assert run_id_with_sequence("baseline:1", 0) == "baseline:1"
    assert run_id_with_sequence("baseline:1", 2) == "baseline:1#2"
    state = load_state("route-disruption")
    assert apply_run_identity(apply_run_identity(state, 1), 2).meta.run_id == "baseline:12345#2"  # never stacks suffixes


def test_transition_ticks_prefer_the_simulator_timestamps():
    state = load_state("route-disruption")
    allocation = state.allocations[0]  # IN_TRANSIT, departed at 39
    assert transition_tick(allocation, observed_tick=40) == 39
    assert transition_tick(allocation.model_copy(update={"status": "CANCELLED"}), observed_tick=40) == 40
    assert transition_tick(allocation.model_copy(update={"status": "PENDING"}), observed_tick=40) == allocation.created_tick


def test_new_transitions_skips_known_statuses():
    state = load_state("route-disruption")
    now = datetime.now(UTC)
    assert len(new_transitions(state, set(), now)) == len(state.allocations)
    assert new_transitions(state, {(1, "IN_TRANSIT")}, now) == []

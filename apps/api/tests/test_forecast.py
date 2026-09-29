from collections.abc import Callable

import pytest

from app.domain.models import DemandObservation, NetworkState, Policy
from app.intelligence.forecast import forecast_demand, multiplier_at, structural_demand
from tests.conftest import load_state

POLICY = Policy()
DHAKA_STATION = "station-mirpur"


def _forecast(state: NetworkState, station_id: str = DHAKA_STATION, fuel: str = "DIESEL"):
    return next(f for f in forecast_demand(state, POLICY) if f.station_id == station_id and f.fuel_type == fuel)


def _map_demand(state: NetworkState, fn: Callable[[DemandObservation], float]) -> NetworkState:
    """Copy of `state` with each observation's demand_liters replaced by fn(obs); served_liters is left untouched."""
    history = [o.model_copy(update={"demand_liters": fn(o)}) for o in state.demand_history]
    return state.model_copy(update={"demand_history": history})


def test_forecast_is_deterministic() -> None:
    state = load_state("route-disruption")
    assert forecast_demand(state, POLICY) == forecast_demand(state, POLICY)


def test_no_history_falls_back_to_structural_with_no_error() -> None:
    fc = _forecast(load_state("normal"))
    assert fc.method == "structural"
    assert fc.calibration == 1.0
    assert fc.error_mape is None
    assert fc.error_ticks == 0


def test_error_is_measured_and_small_when_model_matches_history() -> None:
    fc = _forecast(load_state("route-disruption"))
    assert fc.method == "structural+calibrated"
    assert fc.error_ticks == POLICY.forecast_error_ticks
    assert fc.error_mape is not None and fc.error_mape < 0.02


def test_error_grows_when_demand_departs_from_the_model() -> None:
    state = load_state("route-disruption")
    recent = state.run.tick - 3
    noisy = _map_demand(state, lambda o: o.demand_liters * 1.6 if o.tick > recent else o.demand_liters)
    fc = _forecast(noisy)
    assert fc.error_mape is not None and fc.error_mape > 0.05


def test_calibrates_on_demand_not_served() -> None:
    state = load_state("route-disruption")
    starved = state.model_copy(
        update={"demand_history": [o.model_copy(update={"served_liters": 0.0, "unmet_liters": o.demand_liters}) for o in state.demand_history]}
    )
    assert _forecast(starved).calibration == _forecast(state).calibration


def test_alpha_is_clamped() -> None:
    huge = _map_demand(load_state("route-disruption"), lambda o: o.demand_liters * 10)
    assert _forecast(huge).calibration == 2.0


def test_recent_spike_does_not_skew_calibration() -> None:
    """Spike began at tick 38 (two ticks before now); earlier ticks ran at base demand, so alpha must stay ~1."""
    state = load_state("route-disruption")
    spike = state.events[1]
    assert spike.type == "demand_spike" and spike.parameters["multiplier"] == 1.8
    late = state.model_copy(update={"events": [state.events[0], spike.model_copy(update={"start_tick": 38})]})
    dhaka = {s.id for s in state.stations if s.region_id == "region-dhaka"}
    # The fixture ran at 1.8x from tick 24; undo that for ticks 24-37 so the history matches the later spike start.
    base = _map_demand(late, lambda o: o.demand_liters / 1.8 if o.station_id in dhaka and 24 <= o.tick < 38 else o.demand_liters)
    for station_id in dhaka:
        assert _forecast(base, station_id).calibration == pytest.approx(1.0, abs=0.02)


def test_multiplier_at_reconstructs_history_from_spike_events() -> None:
    state = load_state("route-disruption")
    mirpur = next(s for s in state.stations if s.id == DHAKA_STATION)
    assert mirpur.demand_multiplier == pytest.approx(1.8)
    assert multiplier_at(state, mirpur, 23) == pytest.approx(1.0)
    assert multiplier_at(state, mirpur, 30) == pytest.approx(1.8)
    other = next(s for s in state.stations if s.region_id != "region-dhaka")
    assert multiplier_at(state, other, 30) == other.demand_multiplier


def _scheduled_spike_state(start_tick: int) -> NetworkState:
    """route-disruption at tick 40 with the Dhaka spike rewound to be SCHEDULED: history and live multiplier are all baseline."""
    state = load_state("route-disruption")
    dhaka = {s.id for s in state.stations if s.region_id == "region-dhaka"}
    spike = state.events[1].model_copy(update={"start_tick": start_tick, "status": "SCHEDULED"})
    stations = [s.model_copy(update={"demand_multiplier": 1.0}) if s.id in dhaka else s for s in state.stations]
    rewound = state.model_copy(update={"events": [state.events[0], spike], "stations": stations})
    return _map_demand(rewound, lambda o: o.demand_liters / 1.8 if o.station_id in dhaka and o.tick >= 24 else o.demand_liters)


def _horizon_error(forecast_liters: list[float], truth: list[float]) -> float:
    return sum(abs(f - t) / t for f, t in zip(forecast_liters, truth, strict=True)) / len(truth)


def test_scheduled_spike_is_anticipated_and_naive_forecast_is_measurably_worse() -> None:
    state = _scheduled_spike_state(start_tick=44)
    spike = state.events[1]
    mirpur = next(s for s in state.stations if s.id == DHAKA_STATION)
    start = state.run.tick + 1
    truth = [
        structural_demand(state, mirpur, "DIESEL", t, 1.8 if spike.start_tick <= t < spike.end_tick else 1.0)
        for t in range(start, start + POLICY.horizon_ticks)
    ]
    naive_state = state.model_copy(update={"events": [state.events[0]]})  # what the forecast believed before it read events
    aware = _horizon_error(_forecast(state).liters_per_tick, truth)
    naive = _horizon_error(_forecast(naive_state).liters_per_tick, truth)
    assert aware < 0.02
    assert naive > 0.15 and naive > 10 * aware


def test_spike_ending_inside_the_horizon_is_not_forecast_to_last_forever() -> None:
    state = load_state("route-disruption")  # Dhaka spike x1.8 active until tick 56
    forecast = _forecast(state)
    mirpur = next(s for s in state.stations if s.id == DHAKA_STATION)
    for tick in (state.run.tick + 1, 60):
        i = tick - forecast.start_tick
        multiplier = 1.8 if tick < 56 else 1.0
        assert forecast.liters_per_tick[i] == pytest.approx(
            structural_demand(state, mirpur, "DIESEL", tick, multiplier) * forecast.calibration, rel=1e-3
        )


def _with_spike(state: NetworkState, parameters: dict) -> NetworkState:
    """route-disruption with its spike's parameters replaced; station multipliers reset so only the event explains them."""
    spike = state.events[1].model_copy(update={"parameters": parameters})
    return state.model_copy(update={"events": [state.events[0], spike]})


@pytest.mark.parametrize(
    ("parameters", "hit"),
    [
        ({"multiplier": 1.8}, {"station-mirpur", "station-tongi", "station-karnaphuli", "station-coxsbazar"}),  # no filters: every station
        ({"multiplier": 1.8, "region_ids": []}, {"station-mirpur", "station-tongi", "station-karnaphuli", "station-coxsbazar"}),
        ({"multiplier": 1.8, "station_ids": ["station-tongi"]}, {"station-tongi"}),
        ({"multiplier": 1.8, "region_ids": ["region-chattogram"]}, {"station-karnaphuli", "station-coxsbazar"}),
    ],
)
def test_spike_filters_follow_the_simulator_guide(parameters: dict, hit: set[str]) -> None:
    state = _with_spike(load_state("route-disruption"), parameters)
    for station in state.stations:
        expected = 1.8 if station.id in hit else 1.0
        assert multiplier_at(state, station, 30) / multiplier_at(state, station, 23) == pytest.approx(expected)

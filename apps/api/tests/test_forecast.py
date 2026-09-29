import pytest

from app.domain.models import FUEL_TYPES, DemandObservation, Policy, SimEvent
from app.intelligence.forecast import forecast_demand, project_depot_supply, structural_demand
from tests.conftest import load_state


def forecast_for(state, station_id: str, fuel_type: str):
    return next(forecast for forecast in forecast_demand(state, Policy()) if forecast.station_id == station_id and forecast.fuel_type == fuel_type)


def test_forecast_covers_every_station_fuel_for_the_configured_horizon() -> None:
    state = load_state("normal")
    policy = Policy(horizon_ticks=7)

    forecasts = forecast_demand(state, policy)

    assert len(forecasts) == len(state.stations) * len(FUEL_TYPES)
    assert {(forecast.station_id, forecast.fuel_type) for forecast in forecasts} == {
        (station.id, fuel) for station in state.stations for fuel in FUEL_TYPES
    }
    assert all(forecast.start_tick == state.run.tick + 1 for forecast in forecasts)
    assert all(len(forecast.liters_per_tick) == policy.horizon_ticks for forecast in forecasts)


def test_cold_start_uses_safe_structural_fallback() -> None:
    state = load_state("normal")

    forecast = forecast_for(state, "station-mirpur", "DIESEL")

    assert forecast.method == "structural-cold-start"
    assert forecast.calibration == 1.0
    assert forecast.error.sample_count == 0
    assert forecast.error.mae_liters is None
    assert all(value >= 0 for value in forecast.liters_per_tick)


def test_stable_demand_has_unit_calibration_and_zero_measured_error() -> None:
    state = load_state("route-disruption")

    forecast = forecast_for(state, "station-karnaphuli", "DIESEL")

    assert forecast.method == "structural+calibrated"
    assert forecast.calibration == pytest.approx(1.0, abs=1e-4)
    assert forecast.error.sample_count == 16
    assert forecast.error.mae_liters == pytest.approx(0.0, abs=0.001)
    assert forecast.error.rmse_liters == pytest.approx(0.0, abs=0.001)


def test_live_demand_spike_changes_forecast_without_distorting_calibration() -> None:
    spiked = load_state("route-disruption")
    station = next(station for station in spiked.stations if station.id == "station-mirpur")
    demand_event = next(event for event in spiked.events if event.type == "demand_spike")
    spiked = spiked.model_copy(
        update={
            "events": [
                event.model_copy(update={"start_tick": spiked.run.tick, "end_tick": spiked.run.tick + 16}) if event.id == demand_event.id else event
                for event in spiked.events
            ],
            "demand_history": [
                DemandObservation(
                    station_id=station.id,
                    fuel_type="DIESEL",
                    tick=tick,
                    demand_liters=structural_demand(spiked, station, "DIESEL", tick, demand_multiplier=1.0),
                    served_liters=0.0,
                    unmet_liters=0.0,
                )
                for tick in range(spiked.run.tick - 16, spiked.run.tick)
            ],
        }
    )

    forecast = forecast_for(spiked, station.id, "DIESEL")
    expected = structural_demand(spiked, station, "DIESEL", forecast.start_tick)

    assert station.demand_multiplier == 1.8
    assert forecast.calibration == pytest.approx(1.0, abs=1e-4)
    assert forecast.liters_per_tick[0] == pytest.approx(expected, abs=0.001)


def test_calibration_uses_demand_not_served_liters() -> None:
    state = load_state("route-disruption")
    baseline = forecast_for(state, "station-tongi", "DIESEL")
    changed_history = [
        observation.model_copy(update={"served_liters": 0.0, "unmet_liters": observation.demand_liters})
        if observation.station_id == "station-tongi" and observation.fuel_type == "DIESEL"
        else observation
        for observation in state.demand_history
    ]
    state = state.model_copy(update={"demand_history": changed_history})

    changed = forecast_for(state, "station-tongi", "DIESEL")

    assert changed == baseline


def test_forecast_is_deterministic() -> None:
    state = load_state("route-disruption")

    assert forecast_demand(state, Policy()) == forecast_demand(state, Policy())


def test_scheduled_demand_spike_is_applied_inside_horizon() -> None:
    state = load_state("normal")
    state = state.model_copy(
        update={
            "events": [
                SimEvent(
                    id=1,
                    type="demand_spike",
                    start_tick=2,
                    end_tick=4,
                    status="SCHEDULED",
                    parameters={"region_ids": ["region-dhaka"], "multiplier": 2.0},
                )
            ]
        }
    )

    forecast = forecast_for(state, "station-mirpur", "DIESEL")

    assert forecast.liters_per_tick[1] == pytest.approx(forecast.liters_per_tick[0] * 2, abs=0.001)
    assert forecast.liters_per_tick[3] == pytest.approx(forecast.liters_per_tick[0], abs=0.001)


def test_event_aware_forecast_beats_naive_forecast_when_spike_starts() -> None:
    state = load_state("demand-spike")
    station = next(station for station in state.stations if station.id == "station-mirpur")
    observations = [
        observation for observation in state.demand_history if observation.station_id == station.id and observation.fuel_type == "DIESEL"
    ][-16:]
    naive_expected = sum(structural_demand(state, station, "DIESEL", observation.tick) for observation in observations)
    naive_alpha = min(max(sum(observation.demand_liters for observation in observations) / naive_expected, 0.5), 2.0)
    next_tick = state.run.tick + 1
    actual = structural_demand(state, station, "DIESEL", next_tick)
    naive = actual * naive_alpha
    forecast = forecast_for(state, station.id, "DIESEL").liters_per_tick[0]

    assert abs(forecast - actual) < abs(naive - actual)
    assert forecast == pytest.approx(actual, abs=0.001)


def test_scheduled_delay_and_shortfall_change_depot_supply_expectation() -> None:
    state = load_state("normal")
    state = state.model_copy(
        update={
            "events": [
                SimEvent(
                    id=1,
                    type="shipment_delay",
                    start_tick=5,
                    end_tick=6,
                    status="SCHEDULED",
                    parameters={"delay_ticks": 10, "depot_ids": ["depot-gazipur"], "fuel_types": ["DIESEL"]},
                ),
                SimEvent(
                    id=2,
                    type="supply_shortfall",
                    start_tick=6,
                    end_tick=7,
                    status="SCHEDULED",
                    parameters={"factor": 0.5, "depot_ids": ["depot-gazipur"], "fuel_types": ["DIESEL"]},
                ),
            ]
        }
    )

    projection = next(
        projection
        for projection in project_depot_supply(state, Policy(horizon_ticks=32))
        if projection.depot_id == "depot-gazipur" and projection.fuel_type == "DIESEL"
    )
    points = {point.tick: point for point in projection.points}

    assert points[12].incoming == 0.0
    assert points[22].incoming == 9000.0


def test_materialized_one_shot_supply_event_is_not_applied_twice() -> None:
    state = load_state("normal")
    arrivals = [
        arrival.model_copy(update={"planned_tick": 22, "quantity": 9000.0, "status": "DELAYED"}) if arrival.id == "supply-001" else arrival
        for arrival in state.supply_arrivals
    ]
    state = state.model_copy(
        update={
            "supply_arrivals": arrivals,
            "events": [
                SimEvent(
                    id=1,
                    type="shipment_delay",
                    start_tick=5,
                    end_tick=6,
                    status="ACTIVE",
                    parameters={"delay_ticks": 10, "depot_ids": ["depot-gazipur"], "fuel_types": ["DIESEL"]},
                ),
                SimEvent(
                    id=2,
                    type="supply_shortfall",
                    start_tick=6,
                    end_tick=7,
                    status="RESOLVED",
                    parameters={"factor": 0.5, "depot_ids": ["depot-gazipur"], "fuel_types": ["DIESEL"]},
                ),
            ],
        }
    )

    projection = next(
        projection
        for projection in project_depot_supply(state, Policy(horizon_ticks=32))
        if projection.depot_id == "depot-gazipur" and projection.fuel_type == "DIESEL"
    )

    assert next(point for point in projection.points if point.tick == 22).incoming == 9000.0

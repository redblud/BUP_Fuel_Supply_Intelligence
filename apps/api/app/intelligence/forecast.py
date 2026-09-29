"""Forecast engine and inventory projection. Owner: Developer 2 (intelligence).

P0: forecast = alpha x structural demand, where structural demand is
profile x region factor x hour factor x live demand_multiplier (guide §8.5, §8.6)
and alpha is calibrated on recent observed demand_liters (not served_liters).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from math import sqrt

from app.domain.models import (
    FUEL_TYPES,
    Allocation,
    DemandObservation,
    DepotSupplyProjection,
    Forecast,
    ForecastError,
    FuelType,
    InventoryProjection,
    NetworkState,
    Policy,
    ProjectionPoint,
    SimEvent,
    Station,
    SupplyArrival,
)

# Liters per simulated day (guide §8.5).
DAILY_DEMAND: dict[str, dict[FuelType, float]] = {
    "urban_high": {"DIESEL": 8500, "PETROL": 10500, "OCTANE": 5600},
    "industrial": {"DIESEL": 14000, "PETROL": 4500, "OCTANE": 2200},
    "highway": {"DIESEL": 10500, "PETROL": 11000, "OCTANE": 6200},
    "regional": {"DIESEL": 7200, "PETROL": 7600, "OCTANE": 3600},
}


@dataclass(frozen=True)
class ExpectedSupplyArrival:
    depot_id: str
    fuel_type: FuelType
    tick: int
    quantity: float


def hour_factor(profile: str, hour: int) -> float:
    """Hour-of-day multiplier (guide §8.6)."""
    if profile == "industrial":
        return 1.55 if 6 <= hour <= 17 else 0.45
    if profile == "highway":
        return 1.35 if 6 <= hour <= 9 or 16 <= hour <= 20 else 0.75
    if profile == "urban_high":
        return 1.45 if 7 <= hour <= 9 or 16 <= hour <= 20 else 0.70
    if profile == "regional":
        return 1.25 if 7 <= hour <= 20 else 0.65
    return 1.0


def structural_demand(
    state: NetworkState,
    station: Station,
    fuel: FuelType,
    tick: int,
    *,
    demand_multiplier: float | None = None,
) -> float:
    """Expected liters in one tick before calibration."""
    run = state.run
    ticks_per_day = 24 * 60 / run.tick_minutes
    sim_time = run.sim_time + timedelta(minutes=(tick - run.tick) * run.tick_minutes)
    region_factor = next((r.demand_factor for r in state.regions if r.id == station.region_id), 1.0)
    daily = DAILY_DEMAND.get(station.demand_profile, {}).get(fuel, 0.0)
    multiplier = station.demand_multiplier if demand_multiplier is None else demand_multiplier
    return daily / ticks_per_day * hour_factor(station.demand_profile, sim_time.hour) * region_factor * multiplier


def _event_affects_station(event: SimEvent, station: Station) -> bool:
    station_ids = event.parameters.get("station_ids", [])
    region_ids = event.parameters.get("region_ids", [])
    return (not station_ids or station.id in station_ids) and (not region_ids or station.region_id in region_ids)


def _spike_factor(event: SimEvent) -> float:
    try:
        return max(float(event.parameters.get("multiplier", 1.5)), 0.01)
    except (TypeError, ValueError):
        return 1.5


def _active_spike_factor(state: NetworkState, station: Station, tick: int) -> float:
    factor = 1.0
    for event in state.events:
        if event.type == "demand_spike" and event.start_tick <= tick < event.end_tick and _event_affects_station(event, station):
            factor *= _spike_factor(event)
    return factor


def demand_multiplier_at_tick(state: NetworkState, station: Station, tick: int) -> float:
    """Reconstruct the multiplier at a tick from the live value and event timeline.

    The simulator exposes the station's current multiplier plus persisted events, not a
    multiplier on each demand observation. Removing active effects at the current tick
    recovers the station's baseline; applying events at the requested tick prevents a
    newly-started spike from being absorbed into calibration alpha.
    """
    current_event_factor = _active_spike_factor(state, station, state.run.tick)
    baseline = station.demand_multiplier / current_event_factor if current_event_factor > 0 else station.demand_multiplier
    return baseline * _active_spike_factor(state, station, tick)


def _calibration_alpha(
    state: NetworkState,
    station: Station,
    fuel: FuelType,
    observations: list[DemandObservation],
) -> float:
    observed = sum(obs.demand_liters for obs in observations)
    expected = sum(
        structural_demand(
            state,
            station,
            fuel,
            obs.tick,
            demand_multiplier=demand_multiplier_at_tick(state, station, obs.tick),
        )
        for obs in observations
    )
    return min(max(observed / expected, 0.5), 2.0) if observations and expected > 0 else 1.0


def _forecast_error(actual: list[float], predicted: list[float]) -> ForecastError:
    if not actual:
        return ForecastError(sample_count=0)
    errors = [a - p for a, p in zip(actual, predicted, strict=True)]
    absolute = [abs(error) for error in errors]
    total_actual = sum(abs(value) for value in actual)
    symmetric_denominator = sum(abs(a) + abs(p) for a, p in zip(actual, predicted, strict=True))
    return ForecastError(
        sample_count=len(actual),
        mae_liters=round(sum(absolute) / len(absolute), 3),
        rmse_liters=round(sqrt(sum(error * error for error in errors) / len(errors)), 3),
        wape_percent=round(100 * sum(absolute) / total_actual, 3) if total_actual > 0 else None,
        smape_percent=round(200 * sum(absolute) / symmetric_denominator, 3) if symmetric_denominator > 0 else None,
    )


def _rolling_error(
    state: NetworkState,
    station: Station,
    fuel: FuelType,
    observations: list[DemandObservation],
    window: int,
) -> ForecastError:
    """Backtest each recent point using only observations available before it."""
    actual: list[float] = []
    predicted: list[float] = []
    ordered = sorted(observations, key=lambda obs: obs.tick)
    start = max(len(ordered) - window, 0)
    for index in range(start, len(ordered)):
        observation = ordered[index]
        prior = ordered[max(0, index - window) : index]
        alpha = _calibration_alpha(state, station, fuel, prior)
        estimate = structural_demand(
            state,
            station,
            fuel,
            observation.tick,
            demand_multiplier=demand_multiplier_at_tick(state, station, observation.tick),
        )
        actual.append(observation.demand_liters)
        predicted.append(estimate * alpha)
    return _forecast_error(actual, predicted)


def forecast_demand(state: NetworkState, policy: Policy) -> list[Forecast]:
    """Forecast liters per tick for every (station, fuel) over the policy horizon."""
    history: dict[tuple[str, FuelType], list[DemandObservation]] = defaultdict(list)
    for obs in state.demand_history:
        history[(obs.station_id, obs.fuel_type)].append(obs)

    forecasts: list[Forecast] = []
    start = state.run.tick + 1
    calibration_window = max(policy.history_calibration_ticks, 1)
    for station in state.stations:
        for fuel in FUEL_TYPES:
            observations = sorted(history[(station.id, fuel)], key=lambda o: o.tick)
            recent = observations[-calibration_window:]
            # Clamp so one noisy window cannot swing the plan wildly.
            alpha = _calibration_alpha(state, station, fuel, recent)
            error = _rolling_error(state, station, fuel, observations, calibration_window)
            forecasts.append(
                Forecast(
                    station_id=station.id,
                    fuel_type=fuel,
                    start_tick=start,
                    liters_per_tick=[
                        round(
                            structural_demand(
                                state,
                                station,
                                fuel,
                                t,
                                demand_multiplier=demand_multiplier_at_tick(state, station, t),
                            )
                            * alpha,
                            3,
                        )
                        for t in range(start, start + policy.horizon_ticks)
                    ],
                    calibration=round(alpha, 4),
                    error=error,
                    method="structural+calibrated" if recent else "structural-cold-start",
                )
            )
    return forecasts


def _event_affects_supply(event: SimEvent, arrival: SupplyArrival) -> bool:
    depot_ids = event.parameters.get("depot_ids", [])
    fuel_types = event.parameters.get("fuel_types", [])
    return (not depot_ids or arrival.depot_id in depot_ids) and (not fuel_types or arrival.fuel_type in fuel_types)


def _parameter_float(event: SimEvent, name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(float(event.parameters.get(name, default)), minimum)
    except (TypeError, ValueError):
        return default


def expected_supply_arrivals(state: NetworkState, horizon_ticks: int) -> list[ExpectedSupplyArrival]:
    """Apply future one-shot supply events without mutating the canonical state.

    ACTIVE and RESOLVED one-shot events are already reflected by `/v1/supply-arrivals`.
    Only SCHEDULED events inside the requested horizon need to be anticipated here.
    """
    horizon_end = state.run.tick + max(horizon_ticks, 0)
    expected = {
        arrival.id: ExpectedSupplyArrival(
            depot_id=arrival.depot_id,
            fuel_type=arrival.fuel_type,
            tick=arrival.planned_tick,
            quantity=arrival.quantity,
        )
        for arrival in state.supply_arrivals
        if arrival.status != "ARRIVED" and arrival.planned_tick > state.run.tick
    }
    scheduled_events = sorted(
        (
            event
            for event in state.events
            if event.status == "SCHEDULED"
            and state.run.tick < event.start_tick <= horizon_end
            and event.type in ("shipment_delay", "supply_shortfall")
        ),
        key=lambda event: (event.start_tick, event.id),
    )
    arrivals_by_id = {arrival.id: arrival for arrival in state.supply_arrivals}
    for event in scheduled_events:
        for arrival_id, projection in tuple(expected.items()):
            arrival = arrivals_by_id[arrival_id]
            if projection.tick < event.start_tick or not _event_affects_supply(event, arrival):
                continue
            if event.type == "shipment_delay":
                delay = int(_parameter_float(event, "delay_ticks", 2.0))
                expected[arrival_id] = ExpectedSupplyArrival(
                    depot_id=projection.depot_id,
                    fuel_type=projection.fuel_type,
                    tick=projection.tick + delay,
                    quantity=projection.quantity,
                )
            else:
                factor = _parameter_float(event, "factor", 0.5)
                expected[arrival_id] = ExpectedSupplyArrival(
                    depot_id=projection.depot_id,
                    fuel_type=projection.fuel_type,
                    tick=projection.tick,
                    quantity=projection.quantity * factor,
                )
    return sorted(expected.values(), key=lambda arrival: (arrival.tick, arrival.depot_id, arrival.fuel_type))


def project_depot_supply(state: NetworkState, policy: Policy) -> list[DepotSupplyProjection]:
    """Project depot stock from current inventory and expected future supply."""
    incoming: dict[tuple[str, FuelType, int], float] = defaultdict(float)
    for arrival in expected_supply_arrivals(state, policy.horizon_ticks):
        if arrival.tick <= state.run.tick + policy.horizon_ticks:
            incoming[(arrival.depot_id, arrival.fuel_type, arrival.tick)] += arrival.quantity

    projections: list[DepotSupplyProjection] = []
    for depot in state.depots:
        for fuel in FUEL_TYPES:
            level = depot.inventory.get(fuel, 0.0)
            capacity = depot.capacity.get(fuel, float("inf"))
            points = [ProjectionPoint(tick=state.run.tick, inventory=level)]
            for tick in range(state.run.tick + 1, state.run.tick + policy.horizon_ticks + 1):
                arriving = incoming[(depot.id, fuel, tick)]
                level = min(level + arriving, capacity)
                points.append(ProjectionPoint(tick=tick, inventory=round(level, 3), incoming=round(arriving, 3)))
            projections.append(DepotSupplyProjection(depot_id=depot.id, fuel_type=fuel, points=points))
    return projections


def expected_arrival(allocation: Allocation, state: NetworkState) -> int | None:
    """Tick an open allocation is expected to land; PENDING departs next tick."""
    if allocation.status == "IN_TRANSIT":
        return allocation.expected_arrival_tick
    if allocation.status == "PENDING":
        route = next((r for r in state.routes if r.id == allocation.route_id), None)
        return state.run.tick + 1 + route.transit_ticks if route else None
    return None


def safety_stock(forecast: Forecast, tick_minutes: int, hours: float) -> float:
    avg = sum(forecast.liters_per_tick) / max(len(forecast.liters_per_tick), 1)
    return round(avg * (60 / tick_minutes) * hours, 3)


def project_inventory(state: NetworkState, forecasts: list[Forecast], policy: Policy) -> list[InventoryProjection]:
    """Roll inventory forward: inventory - forecast demand + open allocations arriving."""
    incoming: dict[tuple[str, str, int], float] = defaultdict(float)
    for alloc in state.allocations:
        tick = expected_arrival(alloc, state)
        if tick is not None:
            incoming[(alloc.destination_station_id, alloc.fuel_type, tick)] += alloc.quantity

    stations = {s.id: s for s in state.stations}
    projections: list[InventoryProjection] = []
    for fc in forecasts:
        station = stations[fc.station_id]
        cap = station.capacity.get(fc.fuel_type, float("inf"))
        level = station.inventory.get(fc.fuel_type, 0.0)
        points = [ProjectionPoint(tick=state.run.tick, inventory=level)]
        projected_shortage = 0.0
        for i, demand in enumerate(fc.liters_per_tick):
            tick = fc.start_tick + i
            arriving = incoming[(fc.station_id, fc.fuel_type, tick)]
            available = min(level + arriving, cap)
            served = 0.0 if station.status == "OUTAGE" else min(demand, available)
            unmet = max(demand - served, 0.0)
            projected_shortage += unmet
            level = available - served
            points.append(
                ProjectionPoint(
                    tick=tick,
                    inventory=round(level, 3),
                    incoming=round(arriving, 3),
                    demand=round(demand, 3),
                    unmet_demand=round(unmet, 3),
                )
            )
        projections.append(
            InventoryProjection(
                station_id=fc.station_id,
                fuel_type=fc.fuel_type,
                safety_stock=safety_stock(fc, state.run.tick_minutes, policy.safety_stock_hours),
                minimum_projected_inventory=round(min(point.inventory for point in points), 3),
                projected_shortage_liters=round(projected_shortage, 3),
                points=points,
            )
        )
    return projections

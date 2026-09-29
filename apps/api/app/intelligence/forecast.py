"""Forecast engine and inventory projection. Owner: Developer 2 (intelligence).

P0: forecast = alpha x structural demand, where structural demand is
profile x region factor x hour factor x live demand_multiplier (guide §8.5, §8.6)
and alpha is calibrated on recent observed demand_liters (not served_liters).
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta

from app.domain.models import (
    FUEL_TYPES,
    Allocation,
    DemandObservation,
    Forecast,
    FuelType,
    InventoryProjection,
    NetworkState,
    Policy,
    ProjectionPoint,
    SimEvent,
    Station,
)

# Alpha is clamped so one noisy window cannot swing the plan wildly.
ALPHA_MIN, ALPHA_MAX = 0.5, 2.0

# Liters per simulated day (guide §8.5).
DAILY_DEMAND: dict[str, dict[FuelType, float]] = {
    "urban_high": {"DIESEL": 8500, "PETROL": 10500, "OCTANE": 5600},
    "industrial": {"DIESEL": 14000, "PETROL": 4500, "OCTANE": 2200},
    "highway": {"DIESEL": 10500, "PETROL": 11000, "OCTANE": 6200},
    "regional": {"DIESEL": 7200, "PETROL": 7600, "OCTANE": 3600},
}


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


def structural_demand(state: NetworkState, station: Station, fuel: FuelType, tick: int, multiplier: float | None = None) -> float:
    """Expected liters in one tick before calibration. `multiplier` overrides the station's live demand_multiplier."""
    run = state.run
    ticks_per_day = 24 * 60 / run.tick_minutes
    sim_time = run.sim_time + timedelta(minutes=(tick - run.tick) * run.tick_minutes)
    region_factor = next((r.demand_factor for r in state.regions if r.id == station.region_id), 1.0)
    daily = DAILY_DEMAND.get(station.demand_profile, {}).get(fuel, 0.0)
    live = station.demand_multiplier if multiplier is None else multiplier
    return daily / ticks_per_day * hour_factor(station.demand_profile, sim_time.hour) * region_factor * live


SPIKE_DEFAULT_MULTIPLIER = 1.5  # simulator default when a demand_spike gives no multiplier (guide §7.8)


def _spike_applies(event: SimEvent, station: Station) -> bool:
    """station_ids and region_ids are filters; an empty or missing list means every station (guide §7.8)."""
    station_ids = event.parameters.get("station_ids") or []
    region_ids = event.parameters.get("region_ids") or []
    return (not station_ids or station.id in station_ids) and (not region_ids or station.region_id in region_ids)


def _spike_factor(state: NetworkState, station: Station, tick: int) -> float:
    """Product of the demand_spike multipliers that apply to this station at `tick`."""
    factor = 1.0
    for event in state.events:
        if event.type == "demand_spike" and event.start_tick <= tick < event.end_tick and _spike_applies(event, station):
            factor *= float(event.parameters.get("multiplier", SPIKE_DEFAULT_MULTIPLIER))
    return factor


def multiplier_at(state: NetworkState, station: Station, tick: int) -> float:
    """Demand multiplier the station had at a past `tick`.

    The live `demand_multiplier` already includes any spike active now. Divide that out and apply the spikes that
    were active at `tick`, so calibration is not skewed by a spike that started (or ended) inside the window.
    With no matching spike events this is just the live multiplier.
    """
    now = _spike_factor(state, station, state.run.tick)
    base = station.demand_multiplier / now if now > 0 else station.demand_multiplier
    return base * _spike_factor(state, station, tick)


def calibrate_alpha(state: NetworkState, station: Station, fuel: FuelType, observed: Sequence[DemandObservation]) -> float:
    """Ratio of observed demand_liters to structural demand over `observed`, clamped; 1.0 with no usable data."""
    expected = sum(structural_demand(state, station, fuel, o.tick, multiplier_at(state, station, o.tick)) for o in observed)
    if not observed or expected <= 0:
        return 1.0
    return min(max(sum(o.demand_liters for o in observed) / expected, ALPHA_MIN), ALPHA_MAX)


def forecast_error_mape(
    state: NetworkState, station: Station, fuel: FuelType, observed: Sequence[DemandObservation], policy: Policy
) -> tuple[float | None, int]:
    """One-step-ahead backtest over the last `forecast_error_ticks` observations: (MAPE, ticks scored).

    Each tick is predicted with an alpha calibrated only on the observations before it, so the error is out of sample.
    Ticks with zero observed demand are skipped (percentage error is undefined). Returns (None, 0) when nothing can be scored.
    """
    errors: list[float] = []
    first = max(len(observed) - policy.forecast_error_ticks, 1)
    for i in range(first, len(observed)):
        actual = observed[i].demand_liters
        if actual <= 0:
            continue
        alpha = calibrate_alpha(state, station, fuel, observed[max(i - policy.history_calibration_ticks, 0) : i])
        tick = observed[i].tick
        predicted = structural_demand(state, station, fuel, tick, multiplier_at(state, station, tick)) * alpha
        errors.append(abs(predicted - actual) / actual)
    if not errors:
        return None, 0
    return round(sum(errors) / len(errors), 4), len(errors)


def forecast_demand(state: NetworkState, policy: Policy) -> list[Forecast]:
    """Forecast liters per tick for every (station, fuel) over the policy horizon."""
    history: dict[tuple[str, str], list] = defaultdict(list)
    for obs in state.demand_history:
        history[(obs.station_id, obs.fuel_type)].append(obs)

    forecasts: list[Forecast] = []
    start = state.run.tick + 1
    for station in state.stations:
        for fuel in FUEL_TYPES:
            observed = sorted(history[(station.id, fuel)], key=lambda o: o.tick)
            recent = observed[-policy.history_calibration_ticks :]
            alpha = calibrate_alpha(state, station, fuel, recent)
            mape, scored = forecast_error_mape(state, station, fuel, observed, policy)
            forecasts.append(
                Forecast(
                    station_id=station.id,
                    fuel_type=fuel,
                    start_tick=start,
                    liters_per_tick=[
                        round(structural_demand(state, station, fuel, t) * alpha, 3) for t in range(start, start + policy.horizon_ticks)
                    ],
                    calibration=round(alpha, 4),
                    method="structural+calibrated" if recent else "structural",
                    error_mape=mape,
                    error_ticks=scored,
                )
            )
    return forecasts


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
        for i, demand in enumerate(fc.liters_per_tick):
            tick = fc.start_tick + i
            arriving = incoming[(fc.station_id, fc.fuel_type, tick)]
            served = 0.0 if station.status == "OUTAGE" else demand
            level = min(max(level - served, 0.0) + arriving, cap)
            points.append(ProjectionPoint(tick=tick, inventory=round(level, 3), incoming=arriving))
        projections.append(
            InventoryProjection(
                station_id=fc.station_id,
                fuel_type=fc.fuel_type,
                safety_stock=safety_stock(fc, state.run.tick_minutes, policy.safety_stock_hours),
                points=points,
            )
        )
    return projections

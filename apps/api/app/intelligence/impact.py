"""Recommendation impact and measured-error confidence.

All functions are deterministic and operate only on the supplied NetworkState,
forecast, policy, and candidate shipment.  Confidence is deliberately not a
probability: it is a qualitative robustness label derived from measured recent
absolute forecast error.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean

from app.domain.models import Forecast, FuelType, NetworkState, Policy, Station
from app.intelligence.forecast import structural_demand


@dataclass(frozen=True)
class ImpactResult:
    projected_stockout_ticks_avoided: int
    projected_unmet_demand_liters_avoided: float
    inventory_at_arrival_without_liters: float
    inventory_at_arrival_with_liters: float


@dataclass(frozen=True)
class ForecastErrorResult:
    mae_liters_per_tick: float | None
    band_liters_per_tick: float | None
    confidence: str
    basis: str


def _incoming_by_tick(state: NetworkState, station_id: str, fuel: FuelType) -> dict[int, float]:
    incoming: dict[int, float] = defaultdict(float)
    for allocation in state.allocations:
        if allocation.destination_station_id != station_id or allocation.fuel_type != fuel:
            continue
        if allocation.status in {"FAILED", "CANCELLED", "ARRIVED"}:
            # ARRIVED is already represented by current station inventory, so do not add it again.
            continue
        if allocation.status == "IN_TRANSIT" and allocation.expected_arrival_tick is not None:
            incoming[allocation.expected_arrival_tick] += allocation.quantity
        elif allocation.status == "PENDING":
            route = next((r for r in state.routes if r.id == allocation.route_id), None)
            if route is not None:
                arrival = state.run.tick + 1 + route.transit_ticks
                incoming[arrival] += allocation.quantity
    return incoming


def _simulate(
    state: NetworkState,
    station: Station,
    forecast: Forecast,
    arrival_tick: int,
    shipment_quantity: float,
) -> tuple[dict[int, float], float]:
    cap = station.capacity.get(forecast.fuel_type, 0.0)
    level = station.inventory.get(forecast.fuel_type, 0.0)
    incoming = _incoming_by_tick(state, station.id, forecast.fuel_type)
    inventories: dict[int, float] = {}
    unmet_total = 0.0

    # Match the existing projection convention: demand is consumed for the tick
    # before incoming fuel is applied at the end of that tick.
    for tick, demand in zip(
        range(forecast.start_tick, forecast.start_tick + len(forecast.liters_per_tick)),
        forecast.liters_per_tick,
        strict=True,    ):
        demand = max(float(demand), 0.0)
        if station.status == "OPEN":
            unmet_total += max(demand - level, 0.0)
            level = max(level - demand, 0.0)
        else:
            # Outage suppresses served demand in the existing projection engine.
            level = max(level, 0.0)

        arriving = incoming.get(tick, 0.0)
        if tick == arrival_tick:
            arriving += shipment_quantity
        level = min(level + arriving, cap)
        inventories[tick] = round(level, 3)

    return inventories, round(unmet_total, 3)


def recommendation_impact(
    state: NetworkState,
    station: Station,
    forecast: Forecast,
    arrival_tick: int,
    quantity: float,
) -> ImpactResult:
    """Compare the forecast horizon with and without the recommended shipment."""
    without, unmet_without = _simulate(state, station, forecast, arrival_tick, 0.0)
    with_shipment, unmet_with = _simulate(state, station, forecast, arrival_tick, max(quantity, 0.0))

    arrival_without = without.get(arrival_tick)
    if arrival_without is None:
        # A recommendation may land just outside the forecast horizon.
        arrival_without = next(reversed(without.values()), station.inventory.get(forecast.fuel_type, 0.0))
    arrival_with = min(
        station.capacity.get(forecast.fuel_type, float("inf")),
        arrival_without + max(quantity, 0.0),
    )

    stockout_ticks_avoided = sum(1 for tick, base_level in without.items() if base_level <= 0.0 and with_shipment.get(tick, base_level) > 0.0)

    return ImpactResult(
        projected_stockout_ticks_avoided=stockout_ticks_avoided,
        projected_unmet_demand_liters_avoided=round(max(unmet_without - unmet_with, 0.0), 3),
        inventory_at_arrival_without_liters=round(max(arrival_without, 0.0), 3),
        inventory_at_arrival_with_liters=round(max(arrival_with, 0.0), 3),
    )


def _rolling_calibration(
    state: NetworkState,
    station: Station,
    fuel: FuelType,
    observations,
) -> float:
    if not observations:
        return 1.0
    observed = sum(max(o.demand_liters, 0.0) for o in observations)
    expected = sum(structural_demand(state, station, fuel, o.tick) for o in observations)
    if expected <= 0:
        return 1.0
    return min(max(observed / expected, 0.5), 2.0)


def measured_forecast_error(
    state: NetworkState,
    station: Station,
    fuel: FuelType,
    policy: Policy,
    stress_margin_liters: float,
) -> ForecastErrorResult:
    """Measure recent rolling one-step errors and classify robustness.

    Each historical observation is scored against a forecast calibrated only on
    observations strictly before it. This avoids using the observation being
    scored to calibrate its own error estimate.
    """
    observations = sorted(
        [o for o in state.demand_history if o.station_id == station.id and o.fuel_type == fuel],
        key=lambda o: o.tick,
    )
    recent = observations[-policy.history_calibration_ticks :]
    if len(recent) < 4:
        return ForecastErrorResult(
            mae_liters_per_tick=None,
            band_liters_per_tick=None,
            confidence="INSUFFICIENT_DATA",
            basis=(
                f"Only {len(recent)} recent observation(s) are available; at least 4 are required "
                "before measured forecast error can support a confidence label."
            ),
        )

    errors: list[float] = []
    for obs in recent:
        prior = [x for x in observations if x.tick < obs.tick][-policy.history_calibration_ticks :]
        alpha = _rolling_calibration(state, station, fuel, prior)
        predicted = structural_demand(state, station, fuel, obs.tick) * alpha
        errors.append(abs(float(obs.demand_liters) - predicted))

    mae = mean(errors)
    band = max(errors)
    magnitude = abs(float(stress_margin_liters))
    if band == 0:
        confidence = "HIGH" if magnitude > 0 else "MEDIUM"
        basis = "Recent measured absolute forecast error is 0 L/tick; the stress margin is non-zero."
    else:
        ratio = magnitude / band
        if ratio >= 2.0:
            confidence = "HIGH"
            basis = f"Stress-margin magnitude {magnitude:,.0f} L is at least 2× the recent max absolute forecast error band {band:,.0f} L/tick."
        elif ratio >= 1.0:
            confidence = "MEDIUM"
            basis = f"Stress-margin magnitude {magnitude:,.0f} L is within 1–2× the recent max absolute forecast error band {band:,.0f} L/tick."
        else:
            confidence = "LOW"
            basis = f"Stress-margin magnitude {magnitude:,.0f} L is inside the recent max absolute forecast error band {band:,.0f} L/tick."

    return ForecastErrorResult(
        mae_liters_per_tick=round(mae, 3),
        band_liters_per_tick=round(band, 3),
        confidence=confidence,
        basis=basis,
    )

"""What a shipment buys, and how far to trust the forecast behind it. Pure: no HTTP, database, clock, or FastAPI.

Impact rolls the station's inventory forward twice, without and with the shipment, using the same recurrence as
`project_inventory`. Confidence uses only the forecast error that was actually measured (`Forecast.error_mape`):
it compares the shortfall the shipment covers with the demand error we would expect over the lead time. It never
turns that into a probability, because nothing here is calibrated to produce one.
"""

from collections.abc import Sequence
from typing import NamedTuple

from app.domain.models import Forecast, ForecastConfidence, InventoryProjection, RecommendationImpact


class Rollout(NamedTuple):
    levels: list[float]
    stockout_ticks: int
    unmet_liters: float


def roll_inventory(level: float, capacity: float, demand: Sequence[float], incoming: Sequence[float], outage: bool) -> Rollout:
    """Inventory after each horizon tick: demand is served from the previous level, then arrivals land."""
    levels: list[float] = []
    unmet = 0.0
    for want, arriving in zip(demand, incoming, strict=True):
        if not outage:
            unmet += max(want - level, 0.0)
        served = 0.0 if outage else want
        level = min(max(level - served, 0.0) + arriving, capacity)
        levels.append(level)
    return Rollout(levels, sum(1 for v in levels if v <= 0), unmet)


def _incoming(proj: InventoryProjection, fc: Forecast, extra: Sequence[tuple[int, float]]) -> list[float]:
    """Arrivals per horizon tick: what is already open (from the projection) plus `extra` (tick, liters) shipments."""
    incoming = [p.incoming for p in proj.points[1:]]
    for tick, liters in extra:
        if 0 <= tick - fc.start_tick < len(incoming):
            incoming[tick - fc.start_tick] += liters
    return incoming


def assess_impact(
    proj: InventoryProjection,
    fc: Forecast,
    start_level: float,
    capacity: float,
    outage: bool,
    prior: Sequence[tuple[int, float]],
    arrival: int,
    quantity: float,
) -> RecommendationImpact:
    """Compare the horizon with and without this shipment, on top of `prior` shipments already planned for the station."""
    without = roll_inventory(start_level, capacity, fc.liters_per_tick, _incoming(proj, fc, prior), outage)
    with_ = roll_inventory(start_level, capacity, fc.liters_per_tick, _incoming(proj, fc, [*prior, (arrival, quantity)]), outage)
    at = min(max(arrival - fc.start_tick, 0), len(fc.liters_per_tick) - 1)
    return RecommendationImpact(
        horizon_ticks=len(fc.liters_per_tick),
        stockout_ticks_avoided=without.stockout_ticks - with_.stockout_ticks,
        unmet_liters_avoided=round(without.unmet_liters - with_.unmet_liters, 1),
        inventory_at_arrival_without=round(without.levels[at], 1),
        inventory_at_arrival_with=round(with_.levels[at], 1),
    )


def assess_confidence(fc: Forecast, arrival: int, shortfall_liters: float) -> ForecastConfidence:
    """Confidence from measured forecast error only.

    The band is the measured error rate applied to the demand forecast between now and the arrival. HIGH when the
    shortfall the shipment covers is more than twice that band, MEDIUM when it exceeds the band, otherwise LOW.
    No measured error means UNMEASURED, not a guess.
    """
    if fc.error_mape is None:
        return ForecastConfidence(
            level="UNMEASURED",
            error_ticks=fc.error_ticks,
            shortfall_liters=round(shortfall_liters, 1),
            message="Forecast error has not been measured yet (not enough demand history), so no confidence is claimed.",
        )
    band = fc.error_mape * sum(fc.liters_per_tick[: max(arrival - fc.start_tick + 1, 0)])
    if shortfall_liters <= 0:
        level, relation = (
            "LOW",
            "The station is projected at or above safety stock at arrival, so this shipment tops it up toward target rather than fixing a shortfall.",
        )
    elif shortfall_liters > 2 * band:
        level, relation = (
            "HIGH",
            f"The {shortfall_liters:,.0f} L shortfall is more than twice that band, so the need holds even if demand is misjudged.",
        )
    elif shortfall_liters > band:
        level, relation = "MEDIUM", f"The {shortfall_liters:,.0f} L shortfall exceeds that band, but not by a wide margin."
    else:
        level, relation = "LOW", f"The {shortfall_liters:,.0f} L shortfall is within that band, so forecast error alone could erase it."
    return ForecastConfidence(
        level=level,
        error_mape=fc.error_mape,
        error_ticks=fc.error_ticks,
        error_band_liters=round(band, 1),
        shortfall_liters=round(max(shortfall_liters, 0.0), 1),
        message=(
            f"Measured forecast error is {fc.error_mape:.1%} over the last {fc.error_ticks} ticks, "
            f"about {band:,.0f} L of demand by arrival. {relation}"
        ),
    )

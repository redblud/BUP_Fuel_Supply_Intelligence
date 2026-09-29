"""Pure, deterministic, scarcity-aware replenishment planner.

The planner recommends simulator-valid allocation requests; it never submits them.
Shared depot stock and dispatch budgets are consumed while planning so stations that
compete for scarce fuel cannot each be promised the same liters.
"""

from collections import defaultdict
from dataclasses import dataclass
from math import ceil, inf

from app.domain.models import (
    AllocationRequest,
    AlternativeAction,
    Forecast,
    InventoryProjection,
    NetworkState,
    Policy,
    ReasonCode,
    Recommendation,
    RiskAssessment,
    Route,
)
from app.intelligence.forecast import expected_arrival
from app.intelligence.topology import arrival_tick, feasible_routes

PLANNER_VERSION = "scarcity-aware-v1"


@dataclass(frozen=True)
class _Target:
    risk: RiskAssessment
    forecast: Forecast
    projection: InventoryProjection
    target_inventory: float
    average_demand: float


@dataclass(frozen=True)
class _Option:
    target: _Target
    route: Route
    arrival: int
    inventory_at_arrival: float
    quantity: float
    binding_limit: str
    resulting_cover_hours: float
    scarcity_limited: bool


def _projection_inventory(projection: InventoryProjection, tick: int) -> float:
    """Projected station inventory at the first modeled point at or after ``tick``."""
    return next((point.inventory for point in projection.points if point.tick >= tick), projection.points[-1].inventory)


def _next_split_quantity(needed: float, available: float, max_shipment: float, minimum: float) -> float:
    """Return one shipment, preserving a valid final split when possible."""
    total = max(min(needed, available), 0.0)
    if max_shipment <= 0 or total <= 0 or total < minimum:
        return 0.0
    shipment_count = max(ceil(total / max_shipment), 1)
    if minimum > 0 and total < shipment_count * minimum:
        shipment_count = max(int(total // minimum), 1)
    quantity = min(max_shipment, total - minimum * (shipment_count - 1))
    return round(quantity, 3)


def _reason_codes(risk: RiskAssessment, scarcity_limited: bool) -> list[ReasonCode]:
    codes = list(risk.reason_codes)
    if scarcity_limited and "SCARCITY_LIMITED" not in codes:
        codes.append("SCARCITY_LIMITED")
    return codes


def plan_replenishment(
    state: NetworkState,
    forecasts: list[Forecast],
    risks: list[RiskAssessment],
    projections: list[InventoryProjection],
    policy: Policy,
) -> list[Recommendation]:
    """Recommend feasible shipments using shared stock, dispatch, and tank budgets."""
    now = state.run.tick
    ticks_per_hour = 60 / state.run.tick_minutes
    depots = {depot.id: depot for depot in state.depots}
    stations = {station.id: station for station in state.stations}
    projections_by_key = {(projection.station_id, projection.fuel_type): projection for projection in projections}
    forecasts_by_key = {(forecast.station_id, forecast.fuel_type): forecast for forecast in forecasts}

    # Simulator inventory is already reserved when an allocation is created. Dispatch,
    # however, includes every pending and in-transit allocation from the depot.
    depot_stock = {(depot.id, fuel): quantity for depot in state.depots for fuel, quantity in depot.inventory.items()}
    dispatch_left = {depot.id: depot.dispatch_capacity_per_tick for depot in state.depots}
    incoming_by_station: dict[tuple[str, str], float] = defaultdict(float)
    for allocation in state.allocations:
        if allocation.status in ("PENDING", "IN_TRANSIT"):
            dispatch_left[allocation.source_depot_id] = max(
                dispatch_left.get(allocation.source_depot_id, 0.0) - allocation.quantity,
                0.0,
            )
        if expected_arrival(allocation, state) is not None:
            incoming_by_station[(allocation.destination_station_id, allocation.fuel_type)] += allocation.quantity

    planned_by_station: dict[tuple[str, str], float] = defaultdict(float)
    planned_arrivals: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)
    targets: list[_Target] = []
    for risk in risks:
        if risk.level not in ("CRITICAL", "HIGH") or risk.redundancy == 0:
            continue
        station = stations[risk.station_id]
        if station.status != "OPEN":
            continue
        key = (station.id, risk.fuel_type)
        forecast = forecasts_by_key[key]
        projection = projections_by_key[key]
        capacity = station.capacity.get(risk.fuel_type, 0.0)
        cover_ticks = int(policy.target_cover_hours * ticks_per_hour)
        cover = sum(forecast.liters_per_tick[:cover_ticks])
        target_inventory = min(max(cover, capacity * policy.target_fill_fraction), capacity)
        average_demand = sum(forecast.liters_per_tick) / max(len(forecast.liters_per_tick), 1)
        targets.append(_Target(risk, forecast, projection, target_inventory, average_demand))

    recommendations: list[Recommendation] = []
    sequence = 0
    while True:
        candidates: list[_Option] = []
        for target in targets:
            risk = target.risk
            station = stations[risk.station_id]
            fuel = risk.fuel_type
            key = (station.id, fuel)
            capacity = station.capacity.get(fuel, 0.0)
            destination_left = capacity - station.inventory.get(fuel, 0.0) - incoming_by_station[key] - planned_by_station[key]
            if destination_left < policy.min_shipment_liters:
                continue

            routes = feasible_routes(state, station.id, fuel)
            unique_depots = {route.source_depot_id for route in routes}
            total_deliverable = sum(
                min(
                    max(
                        depot_stock[(depot_id, fuel)]
                        - depots[depot_id].capacity.get(fuel, 0.0) * policy.depot_reserve_fraction,
                        0.0,
                    ),
                    dispatch_left[depot_id],
                )
                for depot_id in unique_depots
            )
            for route in routes:
                depot = depots[route.source_depot_id]
                arrival = arrival_tick(state, route)
                prior_at_arrival = sum(quantity for tick, quantity in planned_arrivals[key] if tick <= arrival)
                inventory_at_arrival = _projection_inventory(target.projection, arrival) + prior_at_arrival
                needed = max(target.target_inventory - inventory_at_arrival, 0.0)
                reserve = depot.capacity.get(fuel, 0.0) * policy.depot_reserve_fraction
                limits = {
                    "order-up-to target": needed,
                    "route max_shipment": route.max_shipment,
                    "depot stock above reserve": max(depot_stock[(depot.id, fuel)] - reserve, 0.0),
                    "depot dispatch capacity this tick": dispatch_left[depot.id],
                    "station destination capacity": max(destination_left, 0.0),
                }
                splittable_available = min(value for name, value in limits.items() if name != "route max_shipment")
                quantity = _next_split_quantity(needed, splittable_available, route.max_shipment, policy.min_shipment_liters)
                if quantity < policy.min_shipment_liters:
                    continue
                binding = min(limits, key=lambda name: (limits[name], name))
                resulting_inventory = inventory_at_arrival + quantity
                resulting_cover = resulting_inventory / target.average_demand / ticks_per_hour if target.average_demand > 0 else inf
                scarcity_limited = total_deliverable + 1e-9 < needed
                candidates.append(
                    _Option(target, route, arrival, inventory_at_arrival, quantity, binding, resulting_cover, scarcity_limited)
                )

        if not candidates:
            break

        # The issue's priority rule is lexicographic: breach urgency, route
        # redundancy, then the lowest cover resulting from the next shipment.
        option = min(
            candidates,
            key=lambda item: (
                item.target.risk.projected_safety_breach_tick
                if item.target.risk.projected_safety_breach_tick is not None
                else 10**9,
                item.target.risk.redundancy,
                item.resulting_cover_hours,
                item.arrival,
                item.target.risk.station_id,
                item.target.risk.fuel_type,
                item.route.id,
            ),
        )
        risk = option.target.risk
        station = stations[risk.station_id]
        fuel = risk.fuel_type
        key = (station.id, fuel)
        route = option.route
        quantity = option.quantity

        depot_stock[(route.source_depot_id, fuel)] -= quantity
        dispatch_left[route.source_depot_id] -= quantity
        planned_by_station[key] += quantity
        planned_arrivals[key].append((option.arrival, quantity))
        sequence += 1

        alternatives: list[AlternativeAction] = []
        for alternative in sorted(
            (candidate for candidate in candidates if candidate.target == option.target and candidate.route.id != route.id),
            key=lambda candidate: (candidate.arrival, candidate.route.id),
        ):
            alternatives.append(
                AlternativeAction(
                    route_id=alternative.route.id,
                    source_depot_id=alternative.route.source_depot_id,
                    expected_arrival_tick=alternative.arrival,
                    max_quantity=alternative.quantity,
                    rejected_because="slower arrival" if alternative.arrival > option.arrival else "lower-priority deterministic tie-break",
                )
            )

        recommendations.append(
            Recommendation(
                id=f"rec-{now}-{station.id}-{fuel}-{sequence:02d}",
                station_id=station.id,
                fuel_type=fuel,
                priority=risk.level,
                reason_codes=_reason_codes(risk, option.scarcity_limited),
                request=AllocationRequest(
                    source_depot_id=route.source_depot_id,
                    destination_station_id=station.id,
                    route_id=route.id,
                    fuel_type=fuel,
                    quantity=quantity,
                ),
                generated_tick=now,
                expiry_tick=now + policy.recommendation_ttl_ticks,
                dispatch_tick=now + 1,
                expected_arrival_tick=option.arrival,
                current_inventory=station.inventory.get(fuel, 0.0),
                projected_inventory_at_arrival=round(option.inventory_at_arrival, 3),
                projected_safety_breach_tick=risk.projected_safety_breach_tick,
                projected_stockout_tick=risk.projected_stockout_tick,
                stress_margin_liters=round(option.inventory_at_arrival - risk.safety_stock, 1),
                summary=f"Send {quantity:,.0f} L {fuel} from {route.source_depot_id} via {route.id}, landing at tick {option.arrival}.",
                factors=[
                    risk.reason,
                    f"Priority: breach tick {risk.projected_safety_breach_tick}, then {risk.redundancy} feasible route(s).",
                    f"Resulting cover is approximately {option.resulting_cover_hours:.2f} hours; quantity limited by {option.binding_limit}.",
                ],
                alternatives=alternatives,
                planner_version=PLANNER_VERSION,
            )
        )

    return recommendations

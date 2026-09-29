"""Pure planner: arrival-aware order-up-to heuristic. Owner: Developer 2 (intelligence).

Pure: no HTTP, no database, no simulator POST, no wall clock, no global state.
Same NetworkState + Policy -> same recommendations.

Quantities respect every simulator validation rule checkable locally (guide §5.2):
max_shipment, depot inventory, dispatch capacity per tick, destination capacity.
Scarcity: risks arrive sorted earliest-breach-first, lower-redundancy-second, and
shared depot budgets are consumed in that order.
"""

from collections import defaultdict

from app.domain.models import (
    AllocationRequest,
    AlternativeAction,
    Forecast,
    InventoryProjection,
    NetworkState,
    Policy,
    Recommendation,
    RiskAssessment,
)
from app.intelligence.forecast import expected_arrival
from app.intelligence.topology import arrival_tick, feasible_routes

PLANNER_VERSION = "order-up-to-v0"


def plan_replenishment(
    state: NetworkState,
    forecasts: list[Forecast],
    risks: list[RiskAssessment],
    projections: list[InventoryProjection],
    policy: Policy,
) -> list[Recommendation]:
    now = state.run.tick
    ticks_per_hour = 60 / state.run.tick_minutes
    depots = {d.id: d for d in state.depots}
    stations = {s.id: s for s in state.stations}
    proj_by_key = {(p.station_id, p.fuel_type): p for p in projections}
    fc_by_key = {(f.station_id, f.fuel_type): f for f in forecasts}

    # Shared budgets, consumed as we plan so later recommendations see earlier ones.
    depot_stock = {(d.id, f): v for d in state.depots for f, v in d.inventory.items()}
    dispatch_left = {d.id: d.dispatch_capacity_per_tick for d in state.depots}
    open_incoming: dict[tuple[str, str], float] = defaultdict(float)
    for alloc in state.allocations:
        if alloc.status == "PENDING":
            dispatch_left[alloc.source_depot_id] = dispatch_left.get(alloc.source_depot_id, 0.0) - alloc.quantity
        if expected_arrival(alloc, state) is not None:
            open_incoming[(alloc.destination_station_id, alloc.fuel_type)] += alloc.quantity

    recs: list[Recommendation] = []
    for risk in risks:
        if risk.level not in ("CRITICAL", "HIGH") or risk.redundancy == 0:
            continue
        station = stations[risk.station_id]
        if station.status != "OPEN":
            continue
        fuel = risk.fuel_type
        key = (station.id, fuel)
        cap = station.capacity.get(fuel, 0.0)
        inventory = station.inventory.get(fuel, 0.0)
        proj = proj_by_key[key]

        # Order-up-to target: max(demand cover, tank fill), never above capacity.
        fc = fc_by_key[key]
        cover_ticks = int(policy.target_cover_hours * ticks_per_hour)
        cover = sum(fc.liters_per_tick[:cover_ticks])
        target = min(max(cover, cap * policy.target_fill_fraction), cap)

        options = []
        for route in feasible_routes(state, station.id, fuel):
            depot = depots[route.source_depot_id]
            arrival = arrival_tick(state, route)
            at_arrival = next((p.inventory for p in proj.points if p.tick >= arrival), proj.points[-1].inventory)
            reserve = depot.capacity.get(fuel, 0.0) * policy.depot_reserve_fraction
            limits = {
                "order-up-to target": target - at_arrival,
                "route max_shipment": route.max_shipment,
                "depot stock above reserve": depot_stock[(depot.id, fuel)] - reserve,
                "depot dispatch capacity this tick": dispatch_left[depot.id],
                # Simulator validates current inventory + quantity <= capacity at submit time.
                "station capacity now": cap - inventory - open_incoming[key],
            }
            binding = min(limits, key=lambda k: limits[k])
            options.append((route, arrival, at_arrival, max(float(int(limits[binding])), 0.0), binding))

        viable = [o for o in options if o[3] >= policy.min_shipment_liters]
        if not viable:
            continue
        route, arrival, at_arrival, qty, binding = viable[0]

        depot_stock[(route.source_depot_id, fuel)] -= qty
        dispatch_left[route.source_depot_id] -= qty
        open_incoming[key] += qty

        recs.append(
            Recommendation(
                id=f"rec-{now}-{station.id}-{fuel}",
                station_id=station.id,
                fuel_type=fuel,
                priority=risk.level,
                reason_codes=risk.reason_codes + (["SCARCITY_LIMITED"] if binding.startswith("depot") else []),
                request=AllocationRequest(
                    source_depot_id=route.source_depot_id,
                    destination_station_id=station.id,
                    route_id=route.id,
                    fuel_type=fuel,
                    quantity=qty,
                ),
                generated_tick=now,
                expiry_tick=now + policy.recommendation_ttl_ticks,
                dispatch_tick=now + 1,
                expected_arrival_tick=arrival,
                current_inventory=inventory,
                projected_inventory_at_arrival=at_arrival,
                projected_safety_breach_tick=risk.projected_safety_breach_tick,
                projected_stockout_tick=risk.projected_stockout_tick,
                stress_margin_liters=round(at_arrival - risk.safety_stock, 1),
                summary=f"Send {qty:,.0f} L {fuel} from {route.source_depot_id} via {route.id}, landing at tick {arrival}.",
                factors=[
                    risk.reason,
                    f"Fastest feasible route ({route.transit_ticks} ticks transit, {risk.redundancy} feasible route(s)).",
                    f"Quantity limited by {binding}.",
                ],
                alternatives=[
                    AlternativeAction(
                        route_id=r.id,
                        source_depot_id=r.source_depot_id,
                        expected_arrival_tick=a,
                        max_quantity=q,
                        rejected_because="slower arrival" if q >= policy.min_shipment_liters else f"insufficient ({b})",
                    )
                    for r, a, _, q, b in options
                    if r.id != route.id
                ],
                planner_version=PLANNER_VERSION,
            )
        )
    return recs

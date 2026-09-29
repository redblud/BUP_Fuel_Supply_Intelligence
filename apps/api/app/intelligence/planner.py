"""Pure planner: arrival-aware order-up-to heuristic with scarcity-aware allocation. Owner: Developer 2 (intelligence).

Pure: no HTTP, no database, no simulator POST, no wall clock, no global state.
Same NetworkState + Policy -> same recommendations.

Quantities respect every simulator validation rule checkable locally (guide §5.2):
max_shipment, depot inventory, dispatch capacity per tick, destination capacity.

Scarcity: risks arrive sorted by level, earliest breach, then lowest redundancy. Stations that would draw on the same
depot and fuel share what the depot can give (stock above reserve, dispatch capacity) by water-filling: the budget
is poured in until resulting cover hours are as equal as the limits allow, and a station whose share falls below the
minimum shipment is dropped so the rest can be served. A need above one route's max_shipment is split into several
shipments while dispatch capacity allows.
"""

from collections import defaultdict
from collections.abc import Sequence
from typing import NamedTuple

from app.domain.models import (
    AllocationRequest,
    AlternativeAction,
    Depot,
    Forecast,
    FuelType,
    InventoryProjection,
    NetworkState,
    Policy,
    Recommendation,
    RiskAssessment,
    Route,
    Station,
)
from app.intelligence.forecast import expected_arrival
from app.intelligence.topology import arrival_tick, feasible_routes

PLANNER_VERSION = "order-up-to-v1"
MAX_SHIPMENTS_PER_STATION = 4
WATER_FILL_ITERATIONS = 60
FAIR_SHARE = "fair share of depot stock, balancing cover hours across stations"


class Want(NamedTuple):
    """One station's claim on a shared depot budget."""

    at_arrival: float
    rate_per_hour: float
    max_qty: float


class Option(NamedTuple):
    """One feasible route for a candidate: when it lands, what it could carry now, and what limits that."""

    route: Route
    arrival: int
    at_arrival: float
    qty: float
    binding: str
    need: float


class Candidate(NamedTuple):
    risk: RiskAssessment
    station: Station
    fuel: FuelType
    proj: InventoryProjection
    target: float
    rate_per_hour: float


def fair_shares(budget: float, wants: Sequence[Want]) -> list[float]:
    """Split `budget` liters so resulting cover hours, (at_arrival + share) / rate, are as equal as the caps allow."""
    if sum(w.max_qty for w in wants) <= budget:
        return [w.max_qty for w in wants]

    def shares_at(level: float) -> list[float]:
        return [min(max(level * w.rate_per_hour - w.at_arrival, 0.0), w.max_qty) for w in wants]

    low, high = 0.0, max((w.at_arrival + w.max_qty) / w.rate_per_hour for w in wants)
    for _ in range(WATER_FILL_ITERATIONS):
        mid = (low + high) / 2
        low, high = (mid, high) if sum(shares_at(mid)) <= budget else (low, mid)
    return shares_at(low)


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
    planned: dict[tuple[str, str], float] = defaultdict(float)

    candidates: list[Candidate] = []
    for risk in risks:
        station = stations[risk.station_id]
        if risk.level not in ("CRITICAL", "HIGH") or risk.redundancy == 0 or station.status != "OPEN":
            continue
        fc = fc_by_key[(station.id, risk.fuel_type)]
        cap = station.capacity.get(risk.fuel_type, 0.0)
        cover = sum(fc.liters_per_tick[: int(policy.target_cover_hours * ticks_per_hour)])
        # Order-up-to target: max(demand cover, tank fill), never above capacity.
        target = min(max(cover, cap * policy.target_fill_fraction), cap)
        rate = max(sum(fc.liters_per_tick) / max(len(fc.liters_per_tick), 1) * ticks_per_hour, 1.0)
        candidates.append(Candidate(risk, station, risk.fuel_type, proj_by_key[(station.id, risk.fuel_type)], target, rate))

    def reserve_of(depot: Depot, fuel: FuelType) -> float:
        return depot.capacity.get(fuel, 0.0) * policy.depot_reserve_fraction

    def budget_left(depot: Depot, fuel: FuelType) -> float:
        return max(min(depot_stock[(depot.id, fuel)] - reserve_of(depot, fuel), dispatch_left[depot.id]), 0.0)

    def options(c: Candidate) -> list[Option]:
        """Feasible routes, fastest first, with the quantity each could carry right now and what limits it."""
        key = (c.station.id, c.fuel)
        cap = c.station.capacity.get(c.fuel, 0.0)
        found = []
        for route in feasible_routes(state, c.station.id, c.fuel):
            depot = depots[route.source_depot_id]
            arrival = arrival_tick(state, route)
            at_arrival = next((p.inventory for p in c.proj.points if p.tick >= arrival), c.proj.points[-1].inventory) + planned[key]
            need = c.target - at_arrival
            limits = {
                "order-up-to target": need,
                "route max_shipment": route.max_shipment,
                "depot stock above reserve": depot_stock[(depot.id, c.fuel)] - reserve_of(depot, c.fuel),
                "depot dispatch capacity this tick": dispatch_left[depot.id],
                # Simulator validates current inventory + quantity <= capacity at submit time.
                "station capacity now": cap - c.station.inventory.get(c.fuel, 0.0) - open_incoming[key],
            }
            binding = min(limits, key=lambda k: limits[k])
            found.append(Option(route, arrival, at_arrival, max(float(int(limits[binding])), 0.0), binding, need))
        return found

    def first_viable(opts: Sequence[Option]) -> Option | None:
        return next((o for o in opts if o.qty >= policy.min_shipment_liters), None)

    def shipment(c: Candidate, pending: Sequence[Candidate]) -> tuple[Option, float, str, list[Option]] | None:
        """One shipment for `c`: its first viable route, cut to its fair share of the depot it draws on."""
        opts = options(c)
        chosen = first_viable(opts)
        if chosen is None:
            return None
        depot_id = chosen.route.source_depot_id
        group = [(c, chosen)]
        for other in pending:
            other_option = first_viable(options(other)) if other is not c and other.fuel == c.fuel else None
            if other_option is not None and other_option.route.source_depot_id == depot_id:
                group.append((other, other_option))
        while True:
            wants = [Want(o.at_arrival, p.rate_per_hour, min(o.route.max_shipment, max(o.need, 0.0))) for p, o in group]
            shares = fair_shares(budget_left(depots[depot_id], c.fuel), wants)
            starved = {i for i, share in enumerate(shares) if share < policy.min_shipment_liters}
            if not starved:
                break
            group = [g for i, g in enumerate(group) if i not in starved]
            if not any(p is c for p, _ in group):
                return None
        share = shares[next(i for i, (p, _) in enumerate(group) if p is c)]
        qty = float(int(min(share, chosen.qty)))
        binding = FAIR_SHARE if len(group) > 1 and share < chosen.qty else chosen.binding
        return chosen, qty, binding, opts

    recs: list[Recommendation] = []
    shipments: dict[tuple[str, str], int] = defaultdict(int)
    pending = list(candidates)
    for _ in range(MAX_SHIPMENTS_PER_STATION):
        progressed = False
        for c in list(pending):
            key = (c.station.id, c.fuel)
            result = shipment(c, pending)
            if result is None:
                pending.remove(c)
                continue
            chosen, qty, binding, opts = result
            route = chosen.route
            depot_stock[(route.source_depot_id, c.fuel)] -= qty
            dispatch_left[route.source_depot_id] -= qty
            open_incoming[key] += qty
            planned[key] += qty
            shipments[key] += 1
            progressed = True
            scarce = binding.startswith(("depot", "fair share"))
            reason_codes = c.risk.reason_codes + (["SCARCITY_LIMITED"] if scarce and "SCARCITY_LIMITED" not in c.risk.reason_codes else [])
            recs.append(
                Recommendation(
                    id=f"rec-{now}-{c.station.id}-{c.fuel}" + (f"-{shipments[key]}" if shipments[key] > 1 else ""),
                    station_id=c.station.id,
                    fuel_type=c.fuel,
                    priority=c.risk.level,
                    reason_codes=reason_codes,
                    request=AllocationRequest(
                        source_depot_id=route.source_depot_id,
                        destination_station_id=c.station.id,
                        route_id=route.id,
                        fuel_type=c.fuel,
                        quantity=qty,
                    ),
                    generated_tick=now,
                    expiry_tick=now + policy.recommendation_ttl_ticks,
                    dispatch_tick=now + 1,
                    expected_arrival_tick=chosen.arrival,
                    current_inventory=c.station.inventory.get(c.fuel, 0.0),
                    projected_inventory_at_arrival=chosen.at_arrival,
                    projected_safety_breach_tick=c.risk.projected_safety_breach_tick,
                    projected_stockout_tick=c.risk.projected_stockout_tick,
                    stress_margin_liters=round(chosen.at_arrival - c.risk.safety_stock, 1),
                    summary=f"Send {qty:,.0f} L {c.fuel} from {route.source_depot_id} via {route.id}, landing at tick {chosen.arrival}.",
                    factors=[
                        c.risk.reason,
                        f"Fastest feasible route ({route.transit_ticks} ticks transit, {c.risk.redundancy} feasible route(s)).",
                        f"Quantity limited by {binding}.",
                    ],
                    alternatives=[
                        AlternativeAction(
                            route_id=o.route.id,
                            source_depot_id=o.route.source_depot_id,
                            expected_arrival_tick=o.arrival,
                            max_quantity=o.qty,
                            rejected_because="slower arrival" if o.qty >= policy.min_shipment_liters else f"insufficient ({o.binding})",
                        )
                        for o in opts
                        if o.route.id != route.id
                    ],
                    planner_version=PLANNER_VERSION,
                )
            )
            # Keep going only when this route's max_shipment cut the shipment and a worthwhile need remains.
            if not (binding == "route max_shipment" and chosen.need - qty >= policy.min_shipment_liters):
                pending.remove(c)
        if not progressed or not pending:
            break
    order = {(c.station.id, c.fuel): i for i, c in enumerate(candidates)}
    return sorted(recs, key=lambda r: order[(r.station_id, r.fuel_type)])

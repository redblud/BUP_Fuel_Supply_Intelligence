from collections import defaultdict

import pytest

from app.domain.models import Policy, Recommendation
from app.intelligence import build_plan
from app.intelligence.planner import Want, fair_shares
from tests.conftest import load_state

SCENARIOS = ["normal", "route-disruption", "scarcity"]
POLICY = Policy()


def _diesel(scenario: str) -> list[Recommendation]:
    return [r for r in build_plan(load_state(scenario)).recommendations if r.fuel_type == "DIESEL"]


def test_fair_shares_gives_everyone_their_cap_when_the_budget_allows() -> None:
    assert fair_shares(10_000, [Want(0, 100, 3000), Want(0, 100, 2000)]) == [3000, 2000]


def test_fair_shares_equalises_cover_hours_and_spends_the_budget() -> None:
    wants = [Want(1000, 1000, 9000), Want(500, 500, 9000)]
    shares = fair_shares(4000, wants)
    covers = [(w.at_arrival + s) / w.rate_per_hour for w, s in zip(wants, shares, strict=True)]
    assert covers[0] == pytest.approx(covers[1], rel=1e-6)
    assert sum(shares) == pytest.approx(4000, abs=1e-3)


def test_fair_shares_never_pours_into_a_station_that_already_has_more_cover() -> None:
    shares = fair_shares(1000, [Want(0, 1000, 5000), Want(50_000, 1000, 5000)])
    assert shares[1] == 0 and shares[0] == pytest.approx(1000, abs=1e-3)


def test_fair_shares_respects_each_cap() -> None:
    shares = fair_shares(9000, [Want(0, 1000, 1000), Want(0, 1000, 20_000)])
    assert shares[0] == pytest.approx(1000) and shares[1] == pytest.approx(8000, abs=1e-3)


def test_scarce_depot_is_shared_so_cover_balances() -> None:
    state = load_state("scarcity")
    recs = _diesel("scarcity")
    assert {r.station_id for r in recs} == {"station-mirpur", "station-tongi"}
    assert all(r.request.source_depot_id == "depot-gazipur" for r in recs)
    assert all("SCARCITY_LIMITED" in r.reason_codes for r in recs)
    gazipur = next(d for d in state.depots if d.id == "depot-gazipur")
    assert sum(r.request.quantity for r in recs) <= gazipur.inventory["DIESEL"] - gazipur.capacity["DIESEL"] * POLICY.depot_reserve_fraction

    plan = build_plan(state)
    rate = {
        f.station_id: sum(f.liters_per_tick) / len(f.liters_per_tick) * (60 / state.run.tick_minutes)
        for f in plan.forecasts
        if f.fuel_type == "DIESEL"
    }
    covers = [(r.projected_inventory_at_arrival + r.request.quantity) / rate[r.station_id] for r in recs]
    assert covers[0] == pytest.approx(covers[1], rel=0.02)


def test_recommendations_follow_earliest_breach_first() -> None:
    plan = build_plan(load_state("scarcity"))
    breach = {r.station_id: r.projected_safety_breach_tick for r in plan.risks if r.fuel_type == "DIESEL"}
    order = [r.station_id for r in plan.recommendations if r.fuel_type == "DIESEL"]
    assert order == sorted(order, key=lambda station_id: breach[station_id])


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_plan_is_deterministic(scenario: str) -> None:
    state = load_state(scenario)
    assert build_plan(state).model_dump() == build_plan(state).model_dump()


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_simulator_and_policy_limit_holds(scenario: str) -> None:
    state = load_state(scenario)
    recs = build_plan(state).recommendations
    routes = {r.id: r for r in state.routes}
    depots = {d.id: d for d in state.depots}
    stations = {s.id: s for s in state.stations}

    drawn: dict[tuple[str, str], float] = defaultdict(float)
    dispatched: dict[str, float] = defaultdict(float)
    delivered: dict[tuple[str, str], float] = defaultdict(float)
    for rec in recs:
        req = rec.request
        assert routes[req.route_id].status == "AVAILABLE"
        assert POLICY.min_shipment_liters <= req.quantity <= routes[req.route_id].max_shipment
        drawn[(req.source_depot_id, req.fuel_type)] += req.quantity
        dispatched[req.source_depot_id] += req.quantity
        delivered[(req.destination_station_id, req.fuel_type)] += req.quantity

    for (depot_id, fuel), qty in drawn.items():
        depot = depots[depot_id]
        assert depot.inventory[fuel] - qty >= depot.capacity[fuel] * POLICY.depot_reserve_fraction - 1e-6
    pending = defaultdict(float)
    for alloc in state.allocations:
        if alloc.status == "PENDING":
            pending[alloc.source_depot_id] += alloc.quantity
    for depot_id, qty in dispatched.items():
        assert qty <= depots[depot_id].dispatch_capacity_per_tick - pending[depot_id] + 1e-6
    incoming = defaultdict(float)
    for alloc in state.allocations:
        if alloc.status in ("PENDING", "IN_TRANSIT"):
            incoming[(alloc.destination_station_id, alloc.fuel_type)] += alloc.quantity
    for (station_id, fuel), qty in delivered.items():
        station = stations[station_id]
        assert station.inventory[fuel] + incoming[(station_id, fuel)] + qty <= station.capacity[fuel] + 1e-6


def test_need_above_max_shipment_is_split_while_dispatch_allows() -> None:
    recs = [r for r in _diesel("route-disruption") if r.station_id == "station-tongi"]
    assert len(recs) == 2
    assert len({r.id for r in recs}) == 2
    assert recs[0].request.quantity == 6500  # route-gazipur-tongi max_shipment
    assert {r.request.route_id for r in recs} == {"route-gazipur-tongi"}

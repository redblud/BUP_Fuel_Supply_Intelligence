import pytest

from app.domain.models import BlockedCase, FuelType, NetworkState, Plan
from app.intelligence import build_plan
from tests.conftest import load_state

MIRPUR, TONGI = "station-mirpur", "station-tongi"


def _patch(state: NetworkState, *, depots: dict | None = None, stations: dict | None = None, routes: dict | None = None) -> NetworkState:
    """Copy of `state` with per-id field updates: {id: {field: value}}."""

    def apply(items, updates):
        return [i.model_copy(update=updates[i.id]) if updates and i.id in updates else i for i in items]

    return state.model_copy(
        update={"depots": apply(state.depots, depots), "stations": apply(state.stations, stations), "routes": apply(state.routes, routes)}
    )


def _stock(state: NetworkState, depot_id: str, fuel: FuelType, liters: float) -> dict:
    depot = next(d for d in state.depots if d.id == depot_id)
    return {depot_id: {"inventory": {**depot.inventory, fuel: liters}}}


def _blocked(plan: Plan, station_id: str, fuel: FuelType = "DIESEL") -> BlockedCase:
    return next(b for b in plan.blocked if b.station_id == station_id and b.fuel_type == fuel)


def test_unreachable_station_is_explained_as_connectivity() -> None:
    state = load_state("scarcity")
    routes = {r.id: {"status": "DISRUPTED"} for r in state.routes if r.destination_station_id == MIRPUR}
    plan = build_plan(_patch(state, routes=routes))
    case = _blocked(plan, MIRPUR)
    assert case.code == "UNREACHABLE" and "connectivity" in case.message
    assert not [r for r in plan.recommendations if r.station_id == MIRPUR and r.fuel_type == "DIESEL"]


def test_dry_depots_are_explained_as_scarcity() -> None:
    state = load_state("scarcity")
    depots = {d.id: {"inventory": {**d.inventory, "DIESEL": 0.0}} for d in state.depots}
    assert _blocked(build_plan(_patch(state, depots=depots)), TONGI).code == "DEPOT_EMPTY"


def test_depot_below_reserve_is_explained() -> None:
    state = load_state("scarcity")
    reserve = next(d for d in state.depots if d.id == "depot-gazipur").capacity["DIESEL"] * 0.1
    case = _blocked(build_plan(_patch(state, depots=_stock(state, "depot-gazipur", "DIESEL", reserve + 100))), TONGI)
    assert case.code == "DEPOT_BELOW_RESERVE"
    assert "100 L can be released" in case.message


def test_full_dispatch_is_explained() -> None:
    state = load_state("scarcity")
    case = _blocked(build_plan(_patch(state, depots={"depot-gazipur": {"dispatch_capacity_per_tick": 200.0}})), TONGI)
    assert case.code == "DISPATCH_FULL" and "200 L" in case.message


def test_full_tank_is_explained() -> None:
    state = load_state("scarcity")
    mirpur = next(s for s in state.stations if s.id == MIRPUR)
    tank = {MIRPUR: {"capacity": {**mirpur.capacity, "DIESEL": mirpur.inventory["DIESEL"] + 100}}}
    assert _blocked(build_plan(_patch(state, stations=tank)), MIRPUR).code == "STATION_TANK_FULL"


def test_station_squeezed_out_by_a_shared_depot_is_explained() -> None:
    state = load_state("scarcity")
    tongi = next(s for s in state.stations if s.id == TONGI)
    mirpur = next(s for s in state.stations if s.id == MIRPUR)
    # Tongi burns fuel ~3x faster than Mirpur, so its fair share of the 4,500 L leaves Mirpur under the minimum shipment.
    # 1,800 L keeps Mirpur urgent (HIGH) under the crisis-aware forecast; the squeeze holds from about 1,650 to 1,950 L.
    stations = {TONGI: {"inventory": {**tongi.inventory, "DIESEL": 2500.0}}, MIRPUR: {"inventory": {**mirpur.inventory, "DIESEL": 1800.0}}}
    plan = build_plan(_patch(state, stations=stations))
    case = _blocked(plan, MIRPUR)
    assert case.code == "BELOW_MIN_SHIPMENT" and "Higher-priority stations already take 4,500 L" in case.message
    assert any(r.station_id == TONGI and r.fuel_type == "DIESEL" for r in plan.recommendations)


@pytest.mark.parametrize("scenario", ["normal", "route-disruption", "scarcity"])
def test_every_urgent_station_is_either_recommended_or_explained(scenario: str) -> None:
    plan = build_plan(load_state(scenario))
    recommended = {(r.station_id, r.fuel_type) for r in plan.recommendations}
    blocked = {(b.station_id, b.fuel_type) for b in plan.blocked}
    assert not recommended & blocked
    urgent = {(r.station_id, r.fuel_type) for r in plan.risks if r.level in ("CRITICAL", "HIGH")}
    assert urgent <= recommended | blocked


def test_recommendation_explains_stress_margin_and_cover() -> None:
    rec = next(r for r in build_plan(load_state("route-disruption")).recommendations if r.station_id == TONGI)
    line = rec.factors[-1]
    assert "safety stock" in line and "h of cover" in line
    assert rec.alternatives is not None and rec.summary and rec.reason_codes

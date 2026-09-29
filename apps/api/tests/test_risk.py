import pytest

from app.domain.models import FuelType, NetworkState, RiskAssessment
from app.intelligence import build_plan
from tests.conftest import load_state

MIRPUR = "station-mirpur"
LEVELS = ["CRITICAL", "HIGH", "WATCH", "OK"]


def _risk(state: NetworkState, station_id: str = MIRPUR, fuel: FuelType = "PETROL") -> RiskAssessment:
    return next(r for r in build_plan(state).risks if r.station_id == station_id and r.fuel_type == fuel)


def _set_inventory(state: NetworkState, station_id: str, fuel: FuelType, liters: float) -> NetworkState:
    stations = [s.model_copy(update={"inventory": {**s.inventory, fuel: liters}}) if s.id == station_id else s for s in state.stations]
    return state.model_copy(update={"stations": stations})


def _set_routes(state: NetworkState, station_id: str, status: str) -> NetworkState:
    routes = [r.model_copy(update={"status": status}) if r.destination_station_id == station_id else r for r in state.routes]
    return state.model_copy(update={"routes": routes})


def _empty_depots(state: NetworkState, fuel: FuelType) -> NetworkState:
    depots = [d.model_copy(update={"inventory": {**d.inventory, fuel: 0.0}}) for d in state.depots]
    return state.model_copy(update={"depots": depots})


def test_levels_worsen_as_inventory_falls_and_all_four_occur() -> None:
    state = load_state("route-disruption")
    cap = next(s for s in state.stations if s.id == MIRPUR).capacity["PETROL"]
    seen = [_risk(_set_inventory(state, MIRPUR, "PETROL", level)).level for level in range(int(cap), -1, -250)]
    ranks = [LEVELS.index(level) for level in seen]
    assert ranks == sorted(ranks, reverse=True), "risk must never improve as inventory drops"
    assert set(seen) == set(LEVELS)


def test_critical_means_stockout_before_any_delivery_can_land() -> None:
    risk = _risk(_set_inventory(load_state("route-disruption"), MIRPUR, "PETROL", 0.0))
    assert risk.level == "CRITICAL"
    assert "STOCKOUT_BEFORE_ARRIVAL" in risk.reason_codes
    assert risk.earliest_arrival_tick is not None and risk.projected_stockout_tick <= risk.earliest_arrival_tick


def test_unreachable_station_is_connectivity_not_scarcity() -> None:
    state = _set_inventory(_set_routes(load_state("route-disruption"), MIRPUR, "DISRUPTED"), MIRPUR, "PETROL", 600.0)
    risk = _risk(state)
    assert "CONNECTIVITY_RISK" in risk.reason_codes and "SCARCITY_LIMITED" not in risk.reason_codes
    assert risk.earliest_arrival_tick is None and risk.redundancy == 0


def test_dry_depots_are_scarcity_not_connectivity() -> None:
    state = _empty_depots(_set_inventory(load_state("route-disruption"), MIRPUR, "PETROL", 600.0), "PETROL")
    risk = _risk(state)
    assert "SCARCITY_LIMITED" in risk.reason_codes and "CONNECTIVITY_RISK" not in risk.reason_codes
    assert risk.earliest_arrival_tick is None and risk.redundancy == 0


def test_route_disruption_changes_the_risk() -> None:
    disrupted = load_state("route-disruption")
    restored = disrupted.model_copy(update={"routes": [r.model_copy(update={"status": "AVAILABLE"}) for r in disrupted.routes]})
    before, after = _risk(restored), _risk(disrupted)
    assert (before.redundancy, after.redundancy) == (2, 1)
    assert after.earliest_arrival_tick > before.earliest_arrival_tick
    assert "ROUTE_DISRUPTED" in after.reason_codes and "ROUTE_DISRUPTED" not in before.reason_codes


def test_hours_to_events_follow_the_projected_ticks() -> None:
    state = load_state("route-disruption")
    risk = _risk(state)
    hours_per_tick = state.run.tick_minutes / 60
    assert risk.projected_safety_breach_tick is not None and risk.projected_stockout_tick is not None
    assert risk.hours_to_safety_breach == pytest.approx((risk.projected_safety_breach_tick - state.run.tick) * hours_per_tick)
    assert risk.hours_to_stockout == pytest.approx((risk.projected_stockout_tick - state.run.tick) * hours_per_tick)
    calm = _risk(load_state("normal"))
    assert calm.hours_to_safety_breach is None and calm.hours_to_stockout is None

from app.domain.models import Allocation, Policy
from app.intelligence import build_plan
from tests.conftest import load_state


def risk_for(state, station_id: str, fuel_type: str, policy: Policy | None = None):
    return next(risk for risk in build_plan(state, policy).risks if risk.station_id == station_id and risk.fuel_type == fuel_type)


def with_inventory(state, station_id: str, fuel_type: str, quantity: float):
    stations = [
        station.model_copy(update={"inventory": {**station.inventory, fuel_type: quantity}}) if station.id == station_id else station
        for station in state.stations
    ]
    return state.model_copy(update={"stations": stations})


def test_imminent_stockout_reports_timing_shortage_coverage_and_drivers() -> None:
    state = with_inventory(load_state("normal"), "station-mirpur", "DIESEL", 100.0)

    risk = risk_for(state, "station-mirpur", "DIESEL", Policy(horizon_ticks=8))

    assert risk.level == "CRITICAL"
    assert risk.projected_stockout_tick is not None
    assert risk.time_to_stockout_ticks == risk.projected_stockout_tick - state.run.tick
    assert risk.time_to_safety_breach_ticks == 0
    assert risk.projected_shortage_liters > 0
    assert risk.minimum_projected_inventory == 0
    assert risk.coverage_ticks is not None and risk.coverage_ticks > 0
    assert {driver.code for driver in risk.risk_drivers} >= {
        "DEMAND_PRESSURE",
        "PROJECTED_SHORTAGE",
        "STOCKOUT_BEFORE_ARRIVAL",
    }


def test_incoming_delivery_reduces_shortage_and_delays_stockout() -> None:
    state = with_inventory(load_state("normal"), "station-mirpur", "DIESEL", 100.0)
    without_incoming = risk_for(state, "station-mirpur", "DIESEL", Policy(horizon_ticks=12))
    allocation = Allocation(
        id=1,
        idempotency_key="incoming-risk-test",
        source_depot_id="depot-gazipur",
        destination_station_id="station-mirpur",
        route_id="route-gazipur-mirpur",
        fuel_type="DIESEL",
        quantity=1000.0,
        created_tick=state.run.tick - 1,
        departure_tick=state.run.tick,
        expected_arrival_tick=state.run.tick + 1,
        actual_arrival_tick=None,
        status="IN_TRANSIT",
        failure_reason=None,
    )
    state = state.model_copy(update={"allocations": [allocation]})

    with_incoming = risk_for(state, "station-mirpur", "DIESEL", Policy(horizon_ticks=12))

    assert with_incoming.projected_shortage_liters < without_incoming.projected_shortage_liters
    assert with_incoming.projected_stockout_tick is None or with_incoming.projected_stockout_tick > without_incoming.projected_stockout_tick
    assert "INCOMING_SUPPLY" in {driver.code for driver in with_incoming.risk_drivers}


def test_station_outage_blocks_delivery_and_records_unmet_demand() -> None:
    state = load_state("normal")
    stations = [station.model_copy(update={"status": "OUTAGE"}) if station.id == "station-mirpur" else station for station in state.stations]
    state = state.model_copy(update={"stations": stations})

    plan = build_plan(state, Policy(horizon_ticks=4))
    risk = next(risk for risk in plan.risks if risk.station_id == "station-mirpur" and risk.fuel_type == "DIESEL")
    projection = next(projection for projection in plan.projections if projection.station_id == "station-mirpur" and projection.fuel_type == "DIESEL")

    assert risk.level == "WATCH"
    assert risk.reason_codes == ["PROJECTED_SHORTAGE", "STATION_OUTAGE"]
    assert risk.earliest_arrival_tick is None
    assert risk.projected_shortage_liters > 0
    assert all(point.inventory == projection.points[0].inventory for point in projection.points)


def test_constrained_depot_remains_reachable() -> None:
    state = load_state("normal")
    depots = [depot.model_copy(update={"status": "CONSTRAINED"}) if depot.id == "depot-gazipur" else depot for depot in state.depots]
    state = state.model_copy(update={"depots": depots})

    risk = risk_for(state, "station-tongi", "DIESEL")

    assert risk.earliest_arrival_tick is not None
    assert "CONNECTIVITY_RISK" not in risk.reason_codes


def test_fuel_scarcity_is_distinct_from_connectivity_risk() -> None:
    state = load_state("normal")
    depots = [
        depot.model_copy(update={"inventory": {**depot.inventory, "DIESEL": 0.0}}) if depot.id == "depot-gazipur" else depot for depot in state.depots
    ]
    state = state.model_copy(update={"depots": depots})

    risk = risk_for(state, "station-tongi", "DIESEL")

    assert "FUEL_SCARCITY" in risk.reason_codes
    assert "CONNECTIVITY_RISK" not in risk.reason_codes


def test_route_disruption_changes_earliest_arrival_and_redundancy() -> None:
    disrupted = load_state("route-disruption")
    disrupted_risk = risk_for(disrupted, "station-mirpur", "DIESEL")
    routes = [route.model_copy(update={"status": "AVAILABLE"}) if route.id == "route-gazipur-mirpur" else route for route in disrupted.routes]
    restored = disrupted.model_copy(update={"routes": routes})

    restored_risk = risk_for(restored, "station-mirpur", "DIESEL")

    assert disrupted_risk.earliest_arrival_tick > restored_risk.earliest_arrival_tick
    assert disrupted_risk.redundancy == restored_risk.redundancy - 1
    assert "ROUTE_DISRUPTED" in disrupted_risk.reason_codes


def test_unreachable_station_has_connectivity_not_scarcity_risk() -> None:
    state = with_inventory(load_state("normal"), "station-tongi", "DIESEL", 100.0)
    routes = [
        route.model_copy(update={"status": "DISRUPTED"}) if route.destination_station_id == "station-tongi" else route for route in state.routes
    ]
    state = state.model_copy(update={"routes": routes})

    risk = risk_for(state, "station-tongi", "DIESEL", Policy(horizon_ticks=8))

    assert "CONNECTIVITY_RISK" in risk.reason_codes
    assert "FUEL_SCARCITY" not in risk.reason_codes
    assert risk.earliest_arrival_tick is None

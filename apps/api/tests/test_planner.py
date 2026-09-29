from collections import defaultdict

from app.domain.models import Allocation, Policy
from app.intelligence import build_plan
from app.intelligence.forecast import expected_arrival
from tests.conftest import load_state


def _replace_diesel_inventory(state, quantities: dict[str, float]):
    stations = [
        station.model_copy(update={"inventory": {**station.inventory, "DIESEL": quantities[station.id]}})
        if station.id in quantities
        else station
        for station in state.stations
    ]
    return state.model_copy(update={"stations": stations})


def _assert_simulator_valid(state, policy: Policy) -> None:
    plan = build_plan(state, policy)
    depots = {depot.id: depot for depot in state.depots}
    stations = {station.id: station for station in state.stations}
    routes = {route.id: route for route in state.routes}
    dispatch = defaultdict(float)
    depot_fuel = defaultdict(float)
    destination_fuel = defaultdict(float)

    for allocation in state.allocations:
        if allocation.status in ("PENDING", "IN_TRANSIT"):
            dispatch[allocation.source_depot_id] += allocation.quantity
        if expected_arrival(allocation, state) is not None:
            destination_fuel[(allocation.destination_station_id, allocation.fuel_type)] += allocation.quantity

    assert len({recommendation.id for recommendation in plan.recommendations}) == len(plan.recommendations)
    for recommendation in plan.recommendations:
        request = recommendation.request
        depot = depots[request.source_depot_id]
        station = stations[request.destination_station_id]
        route = routes[request.route_id]
        assert (route.source_depot_id, route.destination_station_id) == (
            request.source_depot_id,
            request.destination_station_id,
        )
        assert route.status == "AVAILABLE"
        assert station.status == "OPEN"
        assert depot.status in ("OPEN", "CONSTRAINED")
        assert policy.min_shipment_liters <= request.quantity <= route.max_shipment
        dispatch[depot.id] += request.quantity
        depot_fuel[(depot.id, request.fuel_type)] += request.quantity
        destination_fuel[(station.id, request.fuel_type)] += request.quantity

    for depot_id, quantity in dispatch.items():
        assert quantity <= depots[depot_id].dispatch_capacity_per_tick
    for (depot_id, fuel), quantity in depot_fuel.items():
        depot = depots[depot_id]
        reserve = depot.capacity[fuel] * policy.depot_reserve_fraction
        assert quantity <= depot.inventory[fuel] - reserve
    for (station_id, fuel), quantity in destination_fuel.items():
        station = stations[station_id]
        assert quantity <= station.capacity[fuel] - station.inventory[fuel]


def test_scarcity_fixture_is_deterministic_and_has_zero_constraint_violations() -> None:
    state = load_state("scarcity")
    policy = Policy()

    first = build_plan(state, policy)
    second = build_plan(state, policy)

    assert first.model_dump() == second.model_dump()
    assert {recommendation.station_id for recommendation in first.recommendations if recommendation.fuel_type == "DIESEL"} == {
        "station-mirpur",
        "station-tongi",
    }
    assert all("SCARCITY_LIMITED" in recommendation.reason_codes for recommendation in first.recommendations)
    _assert_simulator_valid(state, policy)


def test_earliest_safety_breach_is_allocated_first() -> None:
    state = _replace_diesel_inventory(load_state("scarcity"), {"station-mirpur": 1300.0, "station-tongi": 1800.0})
    plan = build_plan(state)
    risks = {
        risk.station_id: risk
        for risk in plan.risks
        if risk.fuel_type == "DIESEL" and risk.station_id in ("station-mirpur", "station-tongi")
    }

    assert risks["station-tongi"].projected_safety_breach_tick < risks["station-mirpur"].projected_safety_breach_tick
    assert plan.recommendations[0].station_id == "station-tongi"


def test_lower_route_redundancy_breaks_equal_breach_tie() -> None:
    state = load_state("scarcity")
    routes = [
        route.model_copy(update={"destination_station_id": "station-tongi", "status": "AVAILABLE"})
        if route.id == "route-patiya-mirpur"
        else route
        for route in state.routes
    ]
    plan = build_plan(state.model_copy(update={"routes": routes}))
    risks = {
        risk.station_id: risk
        for risk in plan.risks
        if risk.fuel_type == "DIESEL" and risk.station_id in ("station-mirpur", "station-tongi")
    }

    assert risks["station-mirpur"].projected_safety_breach_tick == risks["station-tongi"].projected_safety_breach_tick
    assert risks["station-mirpur"].redundancy < risks["station-tongi"].redundancy
    assert plan.recommendations[0].station_id == "station-mirpur"


def test_equal_priority_stations_are_balanced_by_resulting_cover() -> None:
    state = load_state("scarcity")
    depots = [
        depot.model_copy(update={"inventory": {**depot.inventory, "DIESEL": 14000.0}, "dispatch_capacity_per_tick": 12000.0})
        if depot.id == "depot-gazipur"
        else depot
        for depot in state.depots
    ]
    plan = build_plan(state.model_copy(update={"depots": depots}))
    diesel_order = [recommendation.station_id for recommendation in plan.recommendations if recommendation.fuel_type == "DIESEL"]

    assert diesel_order[:4] == ["station-tongi", "station-mirpur", "station-tongi", "station-mirpur"]


def test_order_above_route_max_is_split_into_valid_shipments() -> None:
    state = load_state("scarcity")
    stations = [station.model_copy(update={"status": "OUTAGE"}) if station.id == "station-tongi" else station for station in state.stations]
    routes = [route.model_copy(update={"max_shipment": 2000.0}) if route.id == "route-gazipur-mirpur" else route for route in state.routes]
    depots = [
        depot.model_copy(update={"inventory": {**depot.inventory, "DIESEL": 14000.0}, "dispatch_capacity_per_tick": 12000.0})
        if depot.id == "depot-gazipur"
        else depot
        for depot in state.depots
    ]
    state = state.model_copy(update={"stations": stations, "routes": routes, "depots": depots})

    shipments = [recommendation.request.quantity for recommendation in build_plan(state).recommendations if recommendation.fuel_type == "DIESEL"]

    assert len(shipments) > 1
    assert sum(shipments) > 2000
    assert all(500 <= quantity <= 2000 for quantity in shipments)
    _assert_simulator_valid(state, Policy())


def test_destination_capacity_limits_cumulative_shipments() -> None:
    state = load_state("scarcity")
    stations = [
        station.model_copy(update={"status": "OUTAGE"})
        if station.id == "station-tongi"
        else station.model_copy(update={"capacity": {**station.capacity, "DIESEL": station.inventory["DIESEL"] + 1200.0}})
        if station.id == "station-mirpur"
        else station
        for station in state.stations
    ]
    state = state.model_copy(update={"stations": stations})

    shipments = [
        recommendation.request.quantity
        for recommendation in build_plan(state).recommendations
        if recommendation.station_id == "station-mirpur" and recommendation.fuel_type == "DIESEL"
    ]

    assert sum(shipments) == 1200.0
    _assert_simulator_valid(state, Policy())


def test_quantity_below_minimum_shipment_is_not_recommended() -> None:
    state = load_state("scarcity")
    stations = [
        station.model_copy(update={"status": "OUTAGE"})
        if station.id == "station-tongi"
        else station.model_copy(update={"capacity": {**station.capacity, "DIESEL": station.inventory["DIESEL"] + 499.0}})
        if station.id == "station-mirpur"
        else station
        for station in state.stations
    ]

    recommendations = build_plan(state.model_copy(update={"stations": stations})).recommendations

    assert not [
        recommendation
        for recommendation in recommendations
        if recommendation.station_id == "station-mirpur" and recommendation.fuel_type == "DIESEL"
    ]


def test_pending_and_in_transit_quantities_consume_dispatch_capacity() -> None:
    state = load_state("scarcity")
    allocations = [
        Allocation(
            id=1,
            idempotency_key="dispatch-existing-1",
            source_depot_id="depot-gazipur",
            destination_station_id="station-karnaphuli",
            route_id="route-gazipur-karnaphuli",
            fuel_type="DIESEL",
            quantity=5000.0,
            created_tick=state.run.tick,
            departure_tick=state.run.tick,
            expected_arrival_tick=state.run.tick + 4,
            actual_arrival_tick=None,
            status="IN_TRANSIT",
            failure_reason=None,
        ),
        Allocation(
            id=2,
            idempotency_key="dispatch-existing-2",
            source_depot_id="depot-gazipur",
            destination_station_id="station-mirpur",
            route_id="route-gazipur-mirpur",
            fuel_type="DIESEL",
            quantity=1000.0,
            created_tick=state.run.tick,
            departure_tick=None,
            expected_arrival_tick=None,
            actual_arrival_tick=None,
            status="PENDING",
            failure_reason=None,
        ),
    ]
    state = state.model_copy(update={"allocations": allocations})

    recommendations = [
        recommendation
        for recommendation in build_plan(state).recommendations
        if recommendation.request.source_depot_id == "depot-gazipur"
    ]

    assert sum(recommendation.request.quantity for recommendation in recommendations) == 500.0
    _assert_simulator_valid(state, Policy())

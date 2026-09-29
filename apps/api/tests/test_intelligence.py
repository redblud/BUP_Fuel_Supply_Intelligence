from app.intelligence import build_plan
from tests.conftest import load_state


def test_plan_is_deterministic() -> None:
    state = load_state("route-disruption")
    assert build_plan(state).model_dump() == build_plan(state).model_dump()


def test_recommendations_respect_simulator_limits() -> None:
    state = load_state("route-disruption")
    routes = {r.id: r for r in state.routes}
    stations = {s.id: s for s in state.stations}
    plan = build_plan(state)
    assert plan.recommendations, "crisis fixture should produce at least one recommendation"
    for rec in plan.recommendations:
        req = rec.request
        route = routes[req.route_id]
        assert route.status == "AVAILABLE"
        assert (route.source_depot_id, route.destination_station_id) == (req.source_depot_id, req.destination_station_id)
        assert 0 < req.quantity <= route.max_shipment
        st = stations[req.destination_station_id]
        assert st.inventory[req.fuel_type] + req.quantity <= st.capacity[req.fuel_type]


def test_disrupted_route_is_avoided() -> None:
    plan = build_plan(load_state("route-disruption"))
    assert all(r.request.route_id != "route-gazipur-mirpur" for r in plan.recommendations)


def test_normal_world_is_calm() -> None:
    plan = build_plan(load_state("normal"))
    assert not [r for r in plan.risks if r.level == "CRITICAL"]

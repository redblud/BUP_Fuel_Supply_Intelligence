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


def test_recommendation_impact_and_confidence_are_deterministic() -> None:
    state = load_state("route-disruption")
    first = build_plan(state)
    second = build_plan(state)
    first_data = [r.model_dump() for r in first.recommendations]
    second_data = [r.model_dump() for r in second.recommendations]
    assert first_data == second_data
    assert first.recommendations
    for rec in first.recommendations:
        assert rec.projected_stockout_ticks_avoided >= 0
        assert rec.projected_unmet_demand_liters_avoided >= 0
        assert rec.inventory_at_arrival_with_liters >= rec.inventory_at_arrival_without_liters
        assert rec.confidence in {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT_DATA"}
        if rec.confidence == "INSUFFICIENT_DATA":
            assert rec.forecast_error_band_liters_per_tick is None
        else:
            assert rec.forecast_error_band_liters_per_tick is not None
            assert rec.forecast_error_mae_liters_per_tick is not None
        assert "chance" not in rec.confidence_basis.lower()
        assert "probability" not in rec.confidence_basis.lower()

def test_impact_arrival_values_match_recommendation_projection() -> None:
    state = load_state("route-disruption")
    plan = build_plan(state)
    for rec in plan.recommendations:
        assert rec.inventory_at_arrival_without_liters == round(rec.projected_inventory_at_arrival, 3)
        assert rec.inventory_at_arrival_with_liters >= rec.inventory_at_arrival_without_liters

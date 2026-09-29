import pytest

from app.domain.models import Forecast, Recommendation
from app.intelligence import build_plan
from app.intelligence.impact import assess_confidence, roll_inventory
from tests.conftest import load_state

SCENARIOS = ["route-disruption", "scarcity"]


def _forecast(error_mape: float | None, per_tick: float = 100.0) -> Forecast:
    return Forecast(
        station_id="s",
        fuel_type="DIESEL",
        start_tick=1,
        liters_per_tick=[per_tick] * 10,
        calibration=1.0,
        method="test",
        error_mape=error_mape,
        error_ticks=8 if error_mape is not None else 0,
    )


def _recs(scenario: str) -> list[Recommendation]:
    return build_plan(load_state(scenario)).recommendations


def test_roll_inventory_serves_demand_then_lands_arrivals() -> None:
    roll = roll_inventory(250, 1000, [100, 100, 100, 100], [0, 0, 500, 0], outage=False)
    assert roll.levels == [150, 50, 500, 400]  # tick 3 has only 50 L left to serve 100 L, so 50 L goes unmet before the delivery lands
    assert roll.stockout_ticks == 0 and roll.unmet_liters == 50


def test_roll_inventory_counts_stockout_and_unmet_demand() -> None:
    roll = roll_inventory(150, 1000, [100, 100, 100], [0, 0, 0], outage=False)
    assert roll.levels == [50, 0, 0]
    assert roll.stockout_ticks == 2 and roll.unmet_liters == 150


def test_roll_inventory_caps_at_tank_capacity_and_ignores_demand_in_an_outage() -> None:
    assert roll_inventory(900, 1000, [100], [500], outage=False).levels == [1000]
    outage = roll_inventory(300, 1000, [100, 100], [0, 0], outage=True)
    assert outage.levels == [300, 300] and outage.unmet_liters == 0


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_a_shipment_never_makes_things_worse(scenario: str) -> None:
    for rec in _recs(scenario):
        assert rec.impact.stockout_ticks_avoided >= 0
        assert rec.impact.unmet_liters_avoided >= 0
        assert rec.impact.inventory_at_arrival_with >= rec.impact.inventory_at_arrival_without


def test_first_shipment_to_a_failing_station_avoids_stockout() -> None:
    tongi = next(r for r in _recs("route-disruption") if r.station_id == "station-tongi" and r.fuel_type == "DIESEL")
    assert tongi.impact.stockout_ticks_avoided > 0 and tongi.impact.unmet_liters_avoided > 0
    assert tongi.impact.inventory_at_arrival_without == pytest.approx(tongi.projected_inventory_at_arrival, abs=0.2)
    assert tongi.impact.inventory_at_arrival_with - tongi.impact.inventory_at_arrival_without == pytest.approx(tongi.request.quantity, abs=0.2)


def test_follow_up_shipment_is_measured_on_top_of_the_first() -> None:
    first, second = (r for r in _recs("route-disruption") if r.station_id == "station-tongi" and r.fuel_type == "DIESEL")
    assert second.impact.inventory_at_arrival_without == pytest.approx(first.impact.inventory_at_arrival_with, abs=0.2)


def test_confidence_bands_follow_the_measured_error() -> None:
    fc = _forecast(0.1)  # arrival 4: 4 ticks x 100 L x 10% = 40 L band
    assert assess_confidence(fc, 4, 200).level == "HIGH"
    assert assess_confidence(fc, 4, 60).level == "MEDIUM"
    assert assess_confidence(fc, 4, 30).level == "LOW"
    assert assess_confidence(fc, 4, 0).level == "LOW"
    high = assess_confidence(fc, 4, 200)
    assert high.error_band_liters == pytest.approx(40) and high.error_mape == 0.1 and high.error_ticks == 8


def test_confidence_is_unmeasured_without_measured_error_and_never_a_probability() -> None:
    unmeasured = assess_confidence(_forecast(None), 4, 500)
    assert unmeasured.level == "UNMEASURED" and unmeasured.error_mape is None and unmeasured.error_band_liters is None
    for text in (unmeasured.message, assess_confidence(_forecast(0.1), 4, 200).message):
        assert "probab" not in text.lower() and "chance" not in text.lower()


def test_recommendations_without_demand_history_report_unmeasured() -> None:
    state = load_state("route-disruption")
    plan = build_plan(state.model_copy(update={"demand_history": []}))
    assert plan.recommendations
    assert all(r.confidence.level == "UNMEASURED" for r in plan.recommendations)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_confidence_carries_the_forecasts_own_measured_error(scenario: str) -> None:
    plan = build_plan(load_state(scenario))
    measured = {(f.station_id, f.fuel_type): f.error_mape for f in plan.forecasts}
    for rec in plan.recommendations:
        assert rec.confidence.error_mape == measured[(rec.station_id, rec.fuel_type)]

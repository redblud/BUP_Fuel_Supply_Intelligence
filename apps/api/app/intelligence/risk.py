"""Risk engine: will this station become unsafe before replenishment can arrive?
Owner: Developer 2 (intelligence).
"""

from app.domain.models import InventoryProjection, NetworkState, ReasonCode, RiskAssessment
from app.intelligence.topology import arrival_tick, disrupted_routes, feasible_routes

LEVEL_ORDER = {"CRITICAL": 0, "HIGH": 1, "WATCH": 2, "OK": 3}


def assess_risk(state: NetworkState, projections: list[InventoryProjection]) -> list[RiskAssessment]:
    """Most urgent first."""
    stations = {s.id: s for s in state.stations}
    risks: list[RiskAssessment] = []

    for proj in projections:
        station = stations[proj.station_id]
        safety = proj.safety_stock
        breach = next((p.tick for p in proj.points if p.inventory < safety), None)
        stockout = next((p.tick for p in proj.points if p.inventory <= 0), None)
        routes = feasible_routes(state, proj.station_id, proj.fuel_type)
        arrival = arrival_tick(state, routes[0]) if routes else None
        at_arrival = (
            next((p.inventory for p in proj.points if p.tick >= arrival), proj.points[-1].inventory)
            if arrival is not None
            else None
        )

        codes: list[ReasonCode] = []
        if station.status == "OUTAGE":
            codes.append("STATION_OUTAGE")
            level, reason = "WATCH", "Station outage: no demand served and deliveries are blocked."
        elif stockout is not None and (arrival is None or stockout <= arrival):
            codes += ["STOCKOUT_BEFORE_ARRIVAL", "SAFETY_STOCK_BREACH"]
            level, reason = "CRITICAL", f"Stockout at tick {stockout}, before the earliest delivery can land."
        elif breach is not None and (arrival is None or breach <= arrival):
            codes.append("SAFETY_STOCK_BREACH")
            level, reason = "HIGH", f"Below safety stock at tick {breach}, at or before the earliest delivery."
        elif breach is not None:
            level, reason = "WATCH", f"Below safety stock at tick {breach}; a delivery can still land first."
        else:
            level, reason = "OK", "Projected inventory stays above safety stock over the horizon."

        if level != "OK":
            if not routes:
                codes.append("CONNECTIVITY_RISK")
                reason += " No feasible route: unreachable, not just scarce."
            elif len(routes) == 1:
                codes.append("SINGLE_SOURCE")
            if disrupted_routes(state, proj.station_id):
                codes.append("ROUTE_DISRUPTED")

        risks.append(
            RiskAssessment(
                station_id=proj.station_id,
                fuel_type=proj.fuel_type,
                level=level,
                reason_codes=codes,
                current_inventory=station.inventory.get(proj.fuel_type, 0.0),
                safety_stock=safety,
                projected_inventory_at_arrival=at_arrival,
                projected_safety_breach_tick=breach,
                projected_stockout_tick=stockout,
                earliest_arrival_tick=arrival,
                redundancy=len(routes),
                reason=reason,
            )
        )

    return sorted(
        risks,
        key=lambda r: (LEVEL_ORDER[r.level], r.projected_safety_breach_tick or 10**9, r.redundancy, r.station_id, r.fuel_type),
    )

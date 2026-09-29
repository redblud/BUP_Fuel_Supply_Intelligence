"""Risk engine: will this station become unsafe before replenishment can arrive?"""

from app.domain.models import InventoryProjection, NetworkState, ReasonCode, RiskAssessment, RiskDriver
from app.intelligence.topology import arrival_tick, disrupted_routes, feasible_routes, reachable_routes

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
        reachable = reachable_routes(state, proj.station_id)
        routes = feasible_routes(state, proj.station_id, proj.fuel_type)
        arrival = arrival_tick(state, routes[0]) if routes else None
        at_arrival = next((p.inventory for p in proj.points if p.tick == arrival), None) if arrival is not None else None
        forecast_points = proj.points[1:]
        total_demand = sum(point.demand for point in forecast_points)
        average_demand = total_demand / len(forecast_points) if forecast_points else 0.0
        coverage_ticks = station.inventory.get(proj.fuel_type, 0.0) / average_demand if average_demand > 0 else None
        coverage_hours = coverage_ticks * state.run.tick_minutes / 60 if coverage_ticks is not None else None
        incoming = sum(point.incoming for point in forecast_points)

        codes: list[ReasonCode] = []
        drivers: list[RiskDriver] = [
            RiskDriver(
                code="DEMAND_PRESSURE",
                value=round(average_demand, 3),
                unit="liters/tick",
                detail="Average forecast demand used to calculate current inventory coverage.",
            )
        ]
        if incoming > 0:
            drivers.append(
                RiskDriver(
                    code="INCOMING_SUPPLY",
                    value=round(incoming, 3),
                    unit="liters",
                    detail="Pending or in-transit supply arriving inside the projection horizon.",
                )
            )
        if proj.projected_shortage_liters > 0:
            codes.append("PROJECTED_SHORTAGE")
            drivers.append(
                RiskDriver(
                    code="PROJECTED_SHORTAGE",
                    value=proj.projected_shortage_liters,
                    unit="liters",
                    threshold=0.0,
                    detail="Demand that cannot be served within the projection horizon.",
                )
            )
        if station.status == "OUTAGE":
            codes.append("STATION_OUTAGE")
            drivers.append(RiskDriver(code="STATION_OUTAGE", detail="Station is closed to deliveries and serves no demand."))
            level, reason = "WATCH", "Station outage: no demand served and deliveries are blocked."
        elif stockout is not None and (arrival is None or stockout <= arrival):
            codes += ["STOCKOUT_BEFORE_ARRIVAL", "SAFETY_STOCK_BREACH"]
            drivers.append(
                RiskDriver(
                    code="STOCKOUT_BEFORE_ARRIVAL",
                    value=float(stockout),
                    unit="tick",
                    threshold=float(arrival) if arrival is not None else None,
                    detail="Projected stockout is at or before the earliest feasible replenishment.",
                )
            )
            level, reason = "CRITICAL", f"Stockout at tick {stockout}, before the earliest delivery can land."
        elif breach is not None and (arrival is None or breach <= arrival):
            codes.append("SAFETY_STOCK_BREACH")
            drivers.append(
                RiskDriver(
                    code="SAFETY_STOCK_BREACH",
                    value=float(breach),
                    unit="tick",
                    threshold=float(arrival) if arrival is not None else None,
                    detail="Safety stock is breached at or before the earliest feasible replenishment.",
                )
            )
            level, reason = "HIGH", f"Below safety stock at tick {breach}, at or before the earliest delivery."
        elif breach is not None:
            level, reason = "WATCH", f"Below safety stock at tick {breach}; a delivery can still land first."
        elif not reachable:
            level, reason = "WATCH", "Inventory is adequate over the horizon, but no route is currently reachable."
        elif not routes:
            level, reason = "WATCH", "Inventory is adequate over the horizon, but reachable depots have no fuel available."
        else:
            level, reason = "OK", "Projected inventory stays above safety stock over the horizon."

        if level != "OK":
            if station.status != "OUTAGE" and not reachable:
                codes.append("CONNECTIVITY_RISK")
                drivers.append(RiskDriver(code="CONNECTIVITY_RISK", detail="No available route connects a usable depot to the station."))
                reason += " No feasible route: unreachable, not just scarce."
            elif station.status != "OUTAGE" and not routes:
                codes.append("FUEL_SCARCITY")
                drivers.append(RiskDriver(code="FUEL_SCARCITY", detail="Routes exist, but no reachable depot currently holds this fuel."))
                reason += " Routes exist, but source fuel is unavailable."
            elif len(routes) == 1:
                codes.append("SINGLE_SOURCE")
                drivers.append(RiskDriver(code="SINGLE_SOURCE", value=1.0, unit="route", detail="Only one currently feasible source route remains."))
            if disrupted_routes(state, proj.station_id):
                codes.append("ROUTE_DISRUPTED")
                drivers.append(RiskDriver(code="ROUTE_DISRUPTED", detail="At least one route to the station is disrupted."))

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
                time_to_safety_breach_ticks=breach - state.run.tick if breach is not None else None,
                time_to_stockout_ticks=stockout - state.run.tick if stockout is not None else None,
                projected_shortage_liters=proj.projected_shortage_liters,
                minimum_projected_inventory=proj.minimum_projected_inventory,
                coverage_ticks=round(coverage_ticks, 3) if coverage_ticks is not None else None,
                coverage_hours=round(coverage_hours, 3) if coverage_hours is not None else None,
                earliest_arrival_tick=arrival,
                redundancy=len(routes),
                risk_drivers=drivers,
                reason=reason,
            )
        )

    return sorted(
        risks,
        key=lambda r: (LEVEL_ORDER[r.level], r.projected_safety_breach_tick or 10**9, r.redundancy, r.station_id, r.fuel_type),
    )

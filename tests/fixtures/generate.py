"""Generate fake-simulator scenarios from the published world (integration guide §8).

Each tests/fixtures/<scenario>.json maps simulator resource names to raw /v1/* bodies,
served by FakeSimulatorClient. Scenarios:

  normal            tick 0, initial world, nothing happening
  demand-spike      tick 24, Dhaka demand spike starts after baseline history
  scarcity          tick 0, Mirpur and Tongi compete for insufficient Gazipur diesel
  route-disruption  tick 40: Dhaka demand spike x1.8, route-gazipur-mirpur DISRUPTED,
                    one diesel shipment in transit to Tongi, Tongi diesel at risk
  stale             route-disruption with X-Simulator-Stale on every read

Representative, not recorded. Replace with real recordings once the simulator runs
(docs/simulator-semantics.md). Run from apps/api:

    uv run python ../../tests/fixtures/generate.py
"""

import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.domain.models import FUEL_TYPES, NetworkState
from app.intelligence.forecast import structural_demand

OUT = ROOT / "tests" / "fixtures"
TICK_MINUTES = 15
START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def sim_time(tick: int) -> str:
    return (START + timedelta(minutes=tick * TICK_MINUTES)).isoformat()


def fuels(d: float, p: float, o: float) -> dict[str, float]:
    return {"DIESEL": d, "PETROL": p, "OCTANE": o}


regions = [
    {"id": "region-dhaka", "name": "Dhaka Division", "demand_factor": 1.00},
    {"id": "region-chattogram", "name": "Chattogram Division", "demand_factor": 1.08},
]
DEPOTS = [
    {"id": "depot-gazipur", "name": "Gazipur Depot", "region_id": "region-dhaka", "status": "OPEN",
     "dispatch_capacity_per_tick": 12000, "capacity": fuels(90000, 70000, 45000), "inventory": fuels(60000, 45000, 26000)},
    {"id": "depot-patiya", "name": "Patiya Depot", "region_id": "region-chattogram", "status": "OPEN",
     "dispatch_capacity_per_tick": 11000, "capacity": fuels(85000, 65000, 40000), "inventory": fuels(55000, 42000, 24000)},
]
STATIONS = [
    {"id": "station-mirpur", "name": "Mirpur Fuel Station", "region_id": "region-dhaka", "status": "OPEN",
     "demand_profile": "urban_high", "demand_multiplier": 1.0, "capacity": fuels(15000, 14000, 9000), "inventory": fuels(9000, 9000, 5000)},
    {"id": "station-tongi", "name": "Tongi Fuel Station", "region_id": "region-dhaka", "status": "OPEN",
     "demand_profile": "industrial", "demand_multiplier": 1.0, "capacity": fuels(18000, 9000, 6000), "inventory": fuels(11000, 6000, 3500)},
    {"id": "station-karnaphuli", "name": "Karnaphuli Fuel Station", "region_id": "region-chattogram", "status": "OPEN",
     "demand_profile": "highway", "demand_multiplier": 1.0, "capacity": fuels(14000, 15000, 9000), "inventory": fuels(8500, 9500, 5200)},
    {"id": "station-coxsbazar", "name": "Cox's Bazar Fuel Station", "region_id": "region-chattogram", "status": "OPEN",
     "demand_profile": "regional", "demand_multiplier": 1.0, "capacity": fuels(12000, 12000, 7000), "inventory": fuels(7500, 7500, 4200)},
]
ROUTES = [
    {"id": "route-gazipur-mirpur", "source_depot_id": "depot-gazipur", "destination_station_id": "station-mirpur", "transit_ticks": 2, "max_shipment": 7000, "status": "AVAILABLE"},
    {"id": "route-gazipur-tongi", "source_depot_id": "depot-gazipur", "destination_station_id": "station-tongi", "transit_ticks": 2, "max_shipment": 6500, "status": "AVAILABLE"},
    {"id": "route-patiya-karnaphuli", "source_depot_id": "depot-patiya", "destination_station_id": "station-karnaphuli", "transit_ticks": 2, "max_shipment": 7000, "status": "AVAILABLE"},
    {"id": "route-patiya-coxsbazar", "source_depot_id": "depot-patiya", "destination_station_id": "station-coxsbazar", "transit_ticks": 3, "max_shipment": 6000, "status": "AVAILABLE"},
    {"id": "route-gazipur-karnaphuli", "source_depot_id": "depot-gazipur", "destination_station_id": "station-karnaphuli", "transit_ticks": 4, "max_shipment": 5000, "status": "AVAILABLE"},
    {"id": "route-patiya-mirpur", "source_depot_id": "depot-patiya", "destination_station_id": "station-mirpur", "transit_ticks": 4, "max_shipment": 5000, "status": "AVAILABLE"},
]
supply = [
    {"id": "supply-001", "depot_id": "depot-gazipur", "fuel_type": "DIESEL", "quantity": 18000, "planned_tick": 12},
    {"id": "supply-002", "depot_id": "depot-patiya", "fuel_type": "DIESEL", "quantity": 16000, "planned_tick": 14},
    {"id": "supply-003", "depot_id": "depot-gazipur", "fuel_type": "PETROL", "quantity": 15000, "planned_tick": 16},
    {"id": "supply-004", "depot_id": "depot-patiya", "fuel_type": "PETROL", "quantity": 14000, "planned_tick": 20},
    {"id": "supply-005", "depot_id": "depot-gazipur", "fuel_type": "DIESEL", "quantity": 22000, "planned_tick": 76},
    {"id": "supply-006", "depot_id": "depot-patiya", "fuel_type": "PETROL", "quantity": 20000, "planned_tick": 84},
]
EVENTS = [
    {"id": 2, "type": "route_disruption", "start_tick": 36, "end_tick": 48,
     "parameters": {"route_ids": ["route-gazipur-mirpur"]}},
    {"id": 1, "type": "demand_spike", "start_tick": 24, "end_tick": 56,
     "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8}},
]
in_transit = {
    "id": 1, "idempotency_key": "fixture-001", "source_depot_id": "depot-gazipur",
    "destination_station_id": "station-tongi", "route_id": "route-gazipur-tongi", "fuel_type": "DIESEL",
    "quantity": 5000, "created_tick": 38, "departure_tick": 39, "expected_arrival_tick": 41,
    "actual_arrival_tick": None, "status": "IN_TRANSIT", "failure_reason": None,
}


def instance(tick: int) -> dict:
    return {"id": 1, "scenario_id": "baseline", "scenario_version": "1.0", "seed": 12345,
            "sim_time": sim_time(tick), "tick": tick, "tick_minutes": TICK_MINUTES, "status": "PAUSED"}


META = {"run_id": "gen", "tick_start": 0, "tick_end": 0, "retrieved_at": START.isoformat(),
        "stale": False, "consistent": True, "freshness": "FIXTURE"}


def build(TICK: int, crisis: bool) -> dict:
    global depots, stations, routes
    depots, stations, routes = copy.deepcopy(DEPOTS), copy.deepcopy(STATIONS), copy.deepcopy(ROUTES)
    events = EVENTS if crisis else []
    history: list[dict] = []
    served_total = unmet_total = 0.0
    for tick in range(1, TICK + 1):
        for ev in events:
            if ev["type"] == "demand_spike" and tick == ev["start_tick"]:
                for s in stations:
                    if s["region_id"] in ev["parameters"]["region_ids"]:
                        s["demand_multiplier"] *= ev["parameters"]["multiplier"]
        for sup in supply:
            if sup["planned_tick"] == tick:
                depots_by_id = {d["id"]: d for d in depots}
                depots_by_id[sup["depot_id"]]["inventory"][sup["fuel_type"]] += sup["quantity"]
        if crisis and tick == in_transit["created_tick"]:
            depots[0]["inventory"]["DIESEL"] -= in_transit["quantity"]
        snap = NetworkState.model_validate({
            "meta": META, "run": instance(tick), "regions": regions, "depots": depots, "stations": stations, "routes": routes,
            "supply_arrivals": [], "events": [], "allocations": [], "demand_history": [],
        })
        for st_model, st in zip(snap.stations, stations):
            for fuel in FUEL_TYPES:
                demand = round(structural_demand(snap, st_model, fuel, tick), 3)
                served = round(min(demand, st["inventory"][fuel]), 3)
                st["inventory"][fuel] = round(st["inventory"][fuel] - served, 3)
                served_total += served
                unmet_total += demand - served
                history.append({"id": len(history) + 1, "station_id": st["id"], "fuel_type": fuel, "tick": tick,
                                "sim_time": sim_time(tick), "demand_liters": demand, "served_liters": served,
                                "unmet_liters": round(demand - served, 3)})

    if crisis:
        routes[0]["status"] = "DISRUPTED"
    raw = {
        "health": {"status": "ok", "database": "ok", "simulation": {"status": "PAUSED", "tick": TICK}},
        "instance": instance(TICK),
        "regions": regions,
        "depots": depots,
        "stations": stations,
        "routes": routes,
        "supply-arrivals": [
            {**s, "actual_tick": s["planned_tick"] if s["planned_tick"] <= TICK else None,
             "status": "ARRIVED" if s["planned_tick"] <= TICK else "SCHEDULED"}
            for s in supply
        ],
        "events": [{**e, "status": "ACTIVE" if e["start_tick"] <= TICK < e["end_tick"] else "SCHEDULED"} for e in events],
        "allocations": [in_transit] if crisis else [],
        # Simulator returns newest first; the adapter reverses to oldest first.
        "demand-history": list(reversed(history[-200:])),
        "metrics": {"served_demand_liters": round(served_total, 3), "unmet_demand_liters": round(unmet_total, 3),
                    "service_level": round(served_total / (served_total + unmet_total), 6) if served_total else 1.0,
                    "allocation_liters": in_transit["quantity"] if crisis else 0, "allocation_failures": 0},
    }
    return raw


def build_demand_spike() -> dict:
    """Spike starts on the current tick after a full baseline calibration window."""
    raw = build(24, crisis=False)
    event = {**copy.deepcopy(EVENTS[1]), "status": "ACTIVE"}
    raw["events"] = [event]
    dhaka_station_ids = {
        station["id"] for station in raw["stations"]
        if station["region_id"] in event["parameters"]["region_ids"]
    }
    stations_by_id = {station["id"]: station for station in raw["stations"]}
    served_delta = 0.0
    for station_id in dhaka_station_ids:
        stations_by_id[station_id]["demand_multiplier"] = event["parameters"]["multiplier"]
    for observation in raw["demand-history"]:
        if observation["tick"] != event["start_tick"] or observation["station_id"] not in dhaka_station_ids:
            continue
        previous = observation["demand_liters"]
        demand = round(previous * event["parameters"]["multiplier"], 3)
        extra = demand - previous
        station = stations_by_id[observation["station_id"]]
        fuel = observation["fuel_type"]
        station["inventory"][fuel] = round(station["inventory"][fuel] - extra, 3)
        observation["demand_liters"] = demand
        observation["served_liters"] = demand
        observation["unmet_liters"] = 0.0
        served_delta += extra
    raw["metrics"]["served_demand_liters"] = round(raw["metrics"]["served_demand_liters"] + served_delta, 3)
    raw["metrics"]["service_level"] = 1.0
    return raw


def build_scarcity() -> dict:
    """Two urgent stations can obtain diesel only from one constrained shared budget."""
    raw = build(0, crisis=False)
    raw["instance"]["scenario_id"] = "scarcity"
    gazipur = next(depot for depot in raw["depots"] if depot["id"] == "depot-gazipur")
    gazipur["capacity"]["DIESEL"] = 20000
    gazipur["inventory"]["DIESEL"] = 8500
    gazipur["dispatch_capacity_per_tick"] = 6500

    for station in raw["stations"]:
        if station["id"] == "station-mirpur":
            station["inventory"]["DIESEL"] = 250
        elif station["id"] == "station-tongi":
            station["inventory"]["DIESEL"] = 900
    for route in raw["routes"]:
        if route["id"] in ("route-gazipur-mirpur", "route-gazipur-tongi"):
            route["max_shipment"] = 3500
        elif route["destination_station_id"] in ("station-mirpur", "station-tongi"):
            route["status"] = "DISRUPTED"
    return raw


def main() -> None:
    scenarios = {"normal": build(0, crisis=False), "demand-spike": build_demand_spike(), "scarcity": build_scarcity(),
                 "route-disruption": build(40, crisis=True)}
    scenarios["stale"] = {**scenarios["route-disruption"], "stale": True}
    for name, raw in scenarios.items():
        (OUT / f"{name}.json").write_text(json.dumps(raw, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {OUT / name}.json")


if __name__ == "__main__":
    main()

"""Topology engine: how can fuel reach a station? Owner: Developer 2 (intelligence).

The network is single-hop (depot -> station), so filtering and sorting is enough.
This answers reachability only; it never decides quantities.
"""

from app.domain.models import FuelType, NetworkState, Route


def open_routes(state: NetworkState, station_id: str) -> list[Route]:
    """Routes into the station that are physically usable (status AVAILABLE), whatever the depots hold.

    Empty here means the station is unreachable, which is a different problem from the depots running dry.
    """
    return [r for r in state.routes if r.destination_station_id == station_id and r.status == "AVAILABLE"]


def feasible_routes(state: NetworkState, station_id: str, fuel: FuelType) -> list[Route]:
    """Open routes that can ship this fuel now, fastest first: the source depot exists and has stock."""
    depots = {d.id: d for d in state.depots}
    return sorted(
        (r for r in open_routes(state, station_id) if r.source_depot_id in depots and depots[r.source_depot_id].inventory.get(fuel, 0.0) > 0),
        key=lambda r: (r.transit_ticks, r.id),
    )


def disrupted_routes(state: NetworkState, station_id: str) -> list[Route]:
    return [r for r in state.routes if r.destination_station_id == station_id and r.status == "DISRUPTED"]


def arrival_tick(state: NetworkState, route: Route) -> int:
    """An allocation created at tick T departs at T+1 and lands transit_ticks later."""
    return state.run.tick + 1 + route.transit_ticks

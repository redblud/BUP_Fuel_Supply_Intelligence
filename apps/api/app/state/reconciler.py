"""State Reconciler: one coherent NetworkState from several REST reads. Owner: Developer 1.

instance (tick_start) -> parallel entity reads -> instance (tick_end); a gap wider than
the tolerance marks the snapshot TORN. Any X-Simulator-Stale header marks it STALE.
"""

import asyncio
from datetime import UTC, datetime

from app.domain.models import NetworkState, SnapshotMeta
from app.simulator.client import SimulatorClient
from app.simulator.mapper import run_id_for, to_network_state

ENTITY_RESOURCES = ["regions", "depots", "stations", "routes", "supply-arrivals", "events", "allocations", "metrics"]
DEMAND_HISTORY_LIMIT = 12 * 16  # 12 (station, fuel) rows per tick x 16 ticks


async def read_network_state(client: SimulatorClient, tick_tolerance: int, fixture: bool = False) -> NetworkState:
    first = await client.get("instance")
    results = await asyncio.gather(
        *(client.get(r) for r in ENTITY_RESOURCES),
        client.get("demand-history", limit=DEMAND_HISTORY_LIMIT),
    )
    last = await client.get("instance")

    raw = dict(zip(ENTITY_RESOURCES + ["demand-history"], (r.body for r in results), strict=True))
    raw["instance"] = last.body
    tick_start, tick_end = first.body["tick"], last.body["tick"]
    stale = first.stale or last.stale or any(r.stale for r in results)
    consistent = tick_end - tick_start <= tick_tolerance and run_id_for(first.body) == run_id_for(last.body)

    if fixture:
        freshness = "FIXTURE"
    elif not consistent:
        freshness = "TORN"
    elif stale:
        freshness = "STALE"
    else:
        freshness = "FRESH"

    meta = SnapshotMeta(
        run_id=run_id_for(last.body),
        tick_start=tick_start,
        tick_end=tick_end,
        retrieved_at=datetime.now(UTC),
        stale=stale,
        consistent=consistent,
        freshness=freshness,
    )
    return to_network_state(raw, meta)

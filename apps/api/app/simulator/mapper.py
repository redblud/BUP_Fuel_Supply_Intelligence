"""Raw simulator JSON -> NetworkState. Pydantic validates each entity (the DTO step)."""

from typing import Any

from app.domain.models import NetworkState, SnapshotMeta


def run_id_for(instance: dict[str, Any]) -> str:
    # TODO(dev1): a reset keeps scenario and seed, so also bump this on tick/sim_time/id regression.
    return f"{instance['scenario_id']}:{instance['seed']}"


def to_network_state(raw: dict[str, Any], meta: SnapshotMeta) -> NetworkState:
    return NetworkState(
        meta=meta,
        run=raw["instance"],
        regions=raw["regions"],
        depots=raw["depots"],
        stations=raw["stations"],
        routes=raw["routes"],
        supply_arrivals=raw["supply-arrivals"],
        events=raw["events"],
        allocations=raw["allocations"],
        # Simulator returns newest first; the contract is oldest first.
        demand_history=list(reversed(raw["demand-history"])),
        metrics=raw.get("metrics"),
    )

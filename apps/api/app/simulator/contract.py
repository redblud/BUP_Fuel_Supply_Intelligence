"""Contract check: does a recorded simulator payload still map onto NetworkState? Pure, no I/O."""

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.domain.models import NetworkState, SnapshotMeta
from app.simulator.mapper import run_id_for, to_network_state

REQUIRED_RESOURCES = ("instance", "regions", "depots", "stations", "routes", "supply-arrivals", "events", "allocations", "demand-history")


def find_drift(raw: dict[str, Any]) -> list[str]:
    """Return one line per field that no longer fits `app/domain/models.py`; empty means the payload maps cleanly.

    Each line reads `<resource>[index].<field>: <problem> (got <value>)`, so a failure names the exact field that drifted.
    """
    missing = [f"{name}: resource missing from recording" for name in REQUIRED_RESOURCES if name not in raw]
    if missing:
        return missing
    try:
        to_network_state(raw, _placeholder_meta(raw))
    except ValidationError as exc:
        return [_describe(err) for err in exc.errors()]
    return []


def map_recording(raw: dict[str, Any]) -> NetworkState:
    """NetworkState from a recording; raises AssertionError listing every drifted field."""
    drift = find_drift(raw)
    if drift:
        raise AssertionError("Simulator contract drift:\n  " + "\n  ".join(drift))
    return to_network_state(raw, _placeholder_meta(raw))


def _placeholder_meta(raw: dict[str, Any]) -> SnapshotMeta:
    tick = raw["instance"].get("tick", 0)
    return SnapshotMeta(
        run_id=run_id_for(raw["instance"]),
        tick_start=tick,
        tick_end=tick,
        retrieved_at=datetime.now(UTC),
        stale=False,
        consistent=True,
        freshness="FIXTURE",
    )


def _describe(err: Any) -> str:
    loc = list(err["loc"])
    path = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in loc).lstrip(".")
    return f"{path}: {err['msg']} (got {err.get('input')!r})"

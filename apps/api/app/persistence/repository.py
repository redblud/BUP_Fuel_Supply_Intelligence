"""Queries for simulator observations. Every function takes the Database explicitly; writes stay inside the API."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert

from app.domain.models import (
    Allocation,
    AllocationTransition,
    AutomationState,
    DemandObservation,
    NetworkState,
    RecommendationState,
    TrackedAllocation,
)
from app.persistence.database import Database
from app.persistence.models import (
    AllocationTransitionRow,
    AutomationSettingRow,
    DemandObservationRow,
    ExecutionIntentRow,
    SimulationRun,
    SnapshotRow,
    StationStateObservation,
)
from app.state.allocations import new_transitions


async def save_observations(db: Database, state: NetworkState) -> None:
    """Persist what this snapshot observed: the run, demand rows (idempotent per tick), and station inventory at the current tick."""
    run_id, tick, now = state.meta.run_id, state.run.tick, datetime.now(UTC)
    demand = [{"run_id": run_id, **o.model_dump()} for o in state.demand_history]
    stations = [
        {"run_id": run_id, "tick": tick, "station_id": s.id, "fuel_type": fuel, "inventory": liters, "station_status": s.status}
        for s in state.stations
        for fuel, liters in s.inventory.items()
    ]
    async with db.sessions() as s, s.begin():
        await s.execute(
            insert(SimulationRun)
            .values(
                run_id=run_id,
                scenario_id=state.run.scenario_id,
                seed=state.run.seed,
                first_tick=tick,
                last_tick=tick,
                first_seen_at=now,
                last_seen_at=now,
            )
            .on_conflict_do_update(
                index_elements=["run_id"],
                set_={"last_tick": tick, "last_seen_at": now, "first_tick": func.min(SimulationRun.first_tick, tick)},
            )
        )
        if demand:
            await s.execute(insert(DemandObservationRow).on_conflict_do_nothing(), demand)
        if stations:
            await s.execute(insert(StationStateObservation).on_conflict_do_nothing(), stations)


async def save_snapshot(db: Database, state: NetworkState) -> None:
    """Replace the run's last trusted snapshot. Call only for trusted (FRESH/FIXTURE) states."""
    row = {
        "run_id": state.meta.run_id,
        "tick": state.run.tick,
        "retrieved_at": state.meta.retrieved_at,
        "freshness": state.meta.freshness,
        "payload": state.model_dump_json(),
    }
    async with db.sessions() as s, s.begin():
        await s.execute(
            insert(SnapshotRow).values(**row).on_conflict_do_update(index_elements=["run_id"], set_={k: v for k, v in row.items() if k != "run_id"})
        )


async def record_allocation_transitions(db: Database, state: NetworkState) -> list[AllocationTransition]:
    """Persist every allocation status in `state` not seen before for this run; returns what was newly recorded.

    Idempotent: replaying the same snapshot records nothing. Also reconciles anything an SSE hint missed,
    because it compares the full REST allocation list against what is stored.
    """
    run_id = state.meta.run_id
    async with db.sessions() as s, s.begin():
        known = {
            (allocation_id, status)
            for allocation_id, status in (
                await s.execute(
                    select(AllocationTransitionRow.allocation_id, AllocationTransitionRow.status).where(AllocationTransitionRow.run_id == run_id)
                )
            ).all()
        }
        transitions = new_transitions(state, known, datetime.now(UTC))
        by_id = {a.id: a for a in state.allocations}
        rows = [
            {
                "run_id": run_id,
                "allocation_id": t.allocation_id,
                "idempotency_key": by_id[t.allocation_id].idempotency_key,
                "status": t.status,
                "tick": t.tick,
                "observed_at": t.observed_at,
                "payload": by_id[t.allocation_id].model_dump_json(),
            }
            for t in transitions
        ]
        if rows:
            await s.execute(insert(AllocationTransitionRow).on_conflict_do_nothing(), rows)
    return transitions


def _tracked(run_id: str, rows: list[AllocationTransitionRow]) -> TrackedAllocation:
    rows = sorted(rows, key=lambda r: r.id)
    return TrackedAllocation(
        run_id=run_id,
        allocation=Allocation.model_validate_json(rows[-1].payload),
        transitions=[
            AllocationTransition(run_id=r.run_id, allocation_id=r.allocation_id, status=r.status, tick=r.tick, observed_at=r.observed_at)  # type: ignore[arg-type]
            for r in rows
        ],
    )


async def list_tracked_allocations(db: Database, run_id: str, *, status: str | None = None, limit: int = 200) -> list[TrackedAllocation]:
    """Tracked allocations for a run, newest allocation id first. `status` filters on the current (latest observed) status."""
    async with db.sessions() as s:
        rows = (await s.scalars(select(AllocationTransitionRow).where(AllocationTransitionRow.run_id == run_id))).all()
    grouped: dict[int, list[AllocationTransitionRow]] = {}
    for row in rows:
        grouped.setdefault(row.allocation_id, []).append(row)
    tracked = [_tracked(run_id, group) for _, group in sorted(grouped.items(), reverse=True)]
    if status:
        tracked = [t for t in tracked if t.allocation.status == status]
    return tracked[:limit]


async def get_tracked_allocation(db: Database, run_id: str, allocation_id: int) -> TrackedAllocation | None:
    """What is the current status of allocation X? None if we never observed it."""
    async with db.sessions() as s:
        rows = (
            await s.scalars(
                select(AllocationTransitionRow).where(
                    AllocationTransitionRow.run_id == run_id, AllocationTransitionRow.allocation_id == allocation_id
                )
            )
        ).all()
    return _tracked(run_id, list(rows)) if rows else None


async def find_tracked_by_idempotency_key(db: Database, run_id: str, idempotency_key: str) -> TrackedAllocation | None:
    """What happened to the allocation submitted with key K? None if the simulator never reported it."""
    async with db.sessions() as s:
        allocation_id = await s.scalar(
            select(AllocationTransitionRow.allocation_id).where(
                AllocationTransitionRow.run_id == run_id, AllocationTransitionRow.idempotency_key == idempotency_key
            )
        )
    return None if allocation_id is None else await get_tracked_allocation(db, run_id, allocation_id)


async def load_snapshot(db: Database, run_id: str) -> NetworkState | None:
    """The last trusted snapshot saved for this run, or None."""
    async with db.sessions() as s:
        row = await s.get(SnapshotRow, run_id)
    return NetworkState.model_validate_json(row.payload) if row else None


async def load_demand_history(
    db: Database,
    run_id: str,
    *,
    station_id: str | None = None,
    fuel_type: str | None = None,
    since_tick: int | None = None,
    limit: int | None = None,
) -> list[DemandObservation]:
    """Stored demand observations for a run, oldest first. `limit` keeps the newest N rows."""
    query = select(DemandObservationRow).where(DemandObservationRow.run_id == run_id)
    if station_id:
        query = query.where(DemandObservationRow.station_id == station_id)
    if fuel_type:
        query = query.where(DemandObservationRow.fuel_type == fuel_type)
    if since_tick is not None:
        query = query.where(DemandObservationRow.tick >= since_tick)
    query = query.order_by(DemandObservationRow.tick.desc(), DemandObservationRow.station_id, DemandObservationRow.fuel_type)
    if limit is not None:
        query = query.limit(limit)
    async with db.sessions() as s:
        rows = (await s.scalars(query)).all()
    return [
        DemandObservation(
            station_id=r.station_id,
            fuel_type=r.fuel_type,  # type: ignore[arg-type]
            tick=r.tick,
            demand_liters=r.demand_liters,
            served_liters=r.served_liters,
            unmet_liters=r.unmet_liters,
        )
        for r in reversed(rows)
    ]


async def save_intent(
    db: Database, state: RecommendationState, *, run_id: str, idempotency_key: str | None = None, request_body: str | None = None
) -> None:
    """Upsert the durable lifecycle row. Keeps the stored key and body when a later update does not supply them."""
    async with db.sessions() as s:
        row = await s.get(ExecutionIntentRow, state.recommendation_id)
        if row is None:
            row = ExecutionIntentRow(recommendation_id=state.recommendation_id, run_id=run_id, status=state.status, updated_tick=state.updated_tick)
            s.add(row)
        row.run_id = run_id
        row.status = state.status
        row.updated_tick = state.updated_tick
        row.allocation_id = state.allocation_id
        row.message = state.message
        row.updated_at = datetime.now(UTC)
        if idempotency_key is not None:
            row.idempotency_key = idempotency_key
        if request_body is not None:
            row.request_body = request_body
        await s.commit()


async def load_intent_body(db: Database, recommendation_id: str) -> tuple[str, str] | None:
    """(idempotency_key, request_body) stored before the POST, for a verbatim replay. None if nothing was prepared."""
    async with db.sessions() as s:
        row = await s.get(ExecutionIntentRow, recommendation_id)
    if row is None or row.idempotency_key is None or row.request_body is None:
        return None
    return row.idempotency_key, row.request_body


async def load_recommendation_states(db: Database) -> dict[str, RecommendationState]:
    """Lifecycle rows of the most recently seen run.

    An EXECUTING row with no recorded outcome reloads as EXECUTION_UNKNOWN: the process may have died between the POST
    and its answer, so nothing else runs until that is confirmed.
    """
    async with db.sessions() as s:
        run_id = await s.scalar(select(SimulationRun.run_id).order_by(SimulationRun.last_seen_at.desc()).limit(1))
        if run_id is None:
            return {}
        rows = (await s.scalars(select(ExecutionIntentRow).where(ExecutionIntentRow.run_id == run_id))).all()
    out: dict[str, RecommendationState] = {}
    for r in rows:
        message = r.message
        if r.status == "EXECUTING" and not (message or "").startswith("EXECUTION_UNKNOWN"):
            message = "EXECUTION_UNKNOWN: the API restarted while this was executing. Confirm on the simulator, then approve again (same key)."
        out[r.recommendation_id] = RecommendationState(
            recommendation_id=r.recommendation_id, status=r.status, updated_tick=r.updated_tick, allocation_id=r.allocation_id, message=message
        )
    return out


async def save_automation(db: Database, automation: AutomationState) -> None:
    """Persist the operating mode and kill switch."""
    async with db.sessions() as s:
        row = await s.get(AutomationSettingRow, 1)
        if row is None:
            s.add(AutomationSettingRow(id=1, payload=automation.model_dump_json()))
        else:
            row.payload = automation.model_dump_json()
            row.updated_at = datetime.now(UTC)
        await s.commit()


async def load_automation(db: Database) -> AutomationState | None:
    """The persisted automation state, or None on first start."""
    async with db.sessions() as s:
        row = await s.get(AutomationSettingRow, 1)
    return AutomationState.model_validate_json(row.payload) if row else None

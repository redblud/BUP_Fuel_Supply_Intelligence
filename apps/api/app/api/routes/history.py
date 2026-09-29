"""History and lifecycle endpoints for the console and the benchmark. Heavy data lives here, not in /api/dashboard."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import AppContext, get_ctx
from app.decision import orchestrator
from app.domain.models import (
    AllocationStatus,
    DemandObservation,
    ErrorBody,
    FuelType,
    RecommendationState,
    RecommendationStatus,
    SimEvent,
    TrackedAllocation,
)
from app.persistence import repository

router = APIRouter(prefix="/api", tags=["history"])
NOT_READY = {503: {"model": ErrorBody}}


async def _run_id(ctx: AppContext) -> str:
    state = await ctx.state.snapshot()
    if state is None:
        err = ctx.state.last_error
        raise HTTPException(503, detail={"code": err.code if err else "UNAVAILABLE", "message": str(err or "No snapshot could be read.")})
    return state.meta.run_id


def _persistence_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(503, detail={"code": "PERSISTENCE_FAILURE", "message": f"{type(exc).__name__}: {exc}"})


@router.get("/allocations", response_model=list[TrackedAllocation], responses=NOT_READY)
async def list_allocations(
    status: AllocationStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ctx: AppContext = Depends(get_ctx),
) -> list[TrackedAllocation]:
    """Simulator allocations of the current run with their observed lifecycle (newest allocation first).

    `status` filters on the current status. Only allocations this API has observed are listed.
    """
    run_id = await _run_id(ctx)
    try:
        return await repository.list_tracked_allocations(ctx.db, run_id, status=status, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise _persistence_unavailable(exc) from exc


@router.get("/events", response_model=list[SimEvent], responses=NOT_READY)
async def list_events(
    status: Literal["SCHEDULED", "ACTIVE", "RESOLVED"] | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ctx: AppContext = Depends(get_ctx),
) -> list[SimEvent]:
    """Simulator events (demand spikes, disruptions, outages, ...) from the latest snapshot, newest start first."""
    await _run_id(ctx)
    events = (await ctx.state.snapshot()).events  # type: ignore[union-attr]
    if status:
        events = [e for e in events if e.status == status]
    return sorted(events, key=lambda e: (e.start_tick, e.id), reverse=True)[:limit]


@router.get("/demand-history", response_model=list[DemandObservation], responses=NOT_READY)
async def demand_history(
    station_id: str | None = None,
    fuel_type: FuelType | None = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 200,
    ctx: AppContext = Depends(get_ctx),
) -> list[DemandObservation]:
    """Stored demand observations of the current run, oldest first; `limit` keeps the newest rows.

    Reaches further back than the simulator's own 200-row window. Falls back to the snapshot's window if nothing is stored.
    """
    run_id = await _run_id(ctx)
    try:
        rows = await repository.load_demand_history(ctx.db, run_id, station_id=station_id, fuel_type=fuel_type, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise _persistence_unavailable(exc) from exc
    if rows:
        return rows
    snapshot = await ctx.state.snapshot()
    live = [o for o in snapshot.demand_history if (not station_id or o.station_id == station_id) and (not fuel_type or o.fuel_type == fuel_type)]  # type: ignore[union-attr]
    return live[-limit:]


@router.get("/decisions", response_model=list[RecommendationState])
async def list_decisions(
    status: RecommendationStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ctx: AppContext = Depends(get_ctx),
) -> list[RecommendationState]:
    """Recommendation decision history (approve, reject, execute, expire), newest first.

    Backed by the in-memory lifecycle until the execution gateway persists it (Dev 4).
    """
    await orchestrator.track_outcomes(ctx)
    states = [s for s in ctx.recommendation_states.values() if status is None or s.status == status]
    return sorted(states, key=lambda s: s.updated_tick, reverse=True)[:limit]

"""Operator-facing API. The frontend reads only these endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AppContext, get_ctx
from app.api.routes.health import system_health
from app.domain.models import (
    AutomationState,
    ComponentHealth,
    DashboardResponse,
    ErrorBody,
    NetworkState,
    Plan,
    RecommendationState,
    SimControlRequest,
)
from app.intelligence import build_plan
from app.safety import tripwire
from app.simulator.errors import SimulatorError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["operations"])
NOT_READY = {503: {"model": ErrorBody}}


@router.get("/state", response_model=NetworkState, responses=NOT_READY)
async def get_state(ctx: AppContext = Depends(get_ctx)) -> NetworkState:
    state = await ctx.state.refresh()
    if state is None:
        err = ctx.state.last_error
        raise HTTPException(503, detail={"code": err.code if err else "UNAVAILABLE", "message": str(err)})
    return state


@router.get("/plan", response_model=Plan, responses=NOT_READY)
async def get_plan(ctx: AppContext = Depends(get_ctx)) -> Plan:
    return build_plan(await get_state(ctx))


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(ctx: AppContext = Depends(get_ctx)) -> DashboardResponse:
    """One poll for the whole operator screen. Degrades instead of failing."""
    state = await ctx.state.refresh()
    health = await system_health(ctx)
    plan = None
    if state is not None:
        try:
            plan = build_plan(state)
            health.components["forecast"] = ComponentHealth(status="HEALTHY")
            health.components["planner"] = ComponentHealth(
                status="DEGRADED" if plan.planner_version == "fallback" else "HEALTHY", detail=plan.planner_version
            )
        except Exception as exc:  # keep showing state even if intelligence breaks
            log.exception("build_plan failed")
            health.components["forecast"] = ComponentHealth(status="DOWN", detail=f"{type(exc).__name__}: {exc}")
    trusted_snapshot = (
        state is not None
        and state.meta.freshness in ("FRESH", "FIXTURE")
        and not state.meta.stale
        and state.meta.consistent
    )
    health.components["snapshot"] = ComponentHealth(
        status="HEALTHY" if trusted_snapshot else "DOWN" if state is None else "DEGRADED",
        detail="STALE" if state and state.meta.stale else state.meta.freshness if state else "UNAVAILABLE",
    )
    trip = tripwire.evaluate(state, plan, str(ctx.state.last_error) if ctx.state.last_error else None)
    health.components["tripwire"] = ComponentHealth(status="HEALTHY" if trip.state == "CLEAR" else "DEGRADED", detail=trip.state)

    trusted = ctx.state.last_trusted
    return DashboardResponse(
        state=state,
        plan=plan,
        tripwire=trip,
        health=health,
        automation=ctx.automation,
        recommendation_states=list(ctx.recommendation_states.values()),
        last_trusted_tick=trusted.run.tick if trusted else None,
        last_trusted_age_seconds=ctx.state.last_trusted_age_seconds(),
    )


@router.get("/automation", response_model=AutomationState)
async def get_automation(ctx: AppContext = Depends(get_ctx)) -> AutomationState:
    return ctx.automation


@router.put("/automation", response_model=AutomationState)
async def set_automation(body: AutomationState, ctx: AppContext = Depends(get_ctx)) -> AutomationState:
    # TODO(dev4): persist + audit; GUARDED_AUTO must refuse while the Tripwire is TRIPPED.
    ctx.automation = body
    await ctx.db.record("automation", body.model_dump_json())
    return ctx.automation


@router.post(
    "/recommendations/{recommendation_id}/approve",
    response_model=RecommendationState,
    responses={501: {"model": ErrorBody}, 409: {"model": ErrorBody}},
)
async def approve(recommendation_id: str, ctx: AppContext = Depends(get_ctx)) -> RecommendationState:
    # TODO(dev4): revalidate against a fresh snapshot, check Tripwire + expiry, depot lock, idempotent POST.
    raise HTTPException(501, detail={"code": "NOT_IMPLEMENTED", "message": "Supervised execution is not built yet."})


@router.post("/recommendations/{recommendation_id}/reject", response_model=RecommendationState, responses={501: {"model": ErrorBody}})
async def reject(recommendation_id: str, ctx: AppContext = Depends(get_ctx)) -> RecommendationState:
    raise HTTPException(501, detail={"code": "NOT_IMPLEMENTED", "message": "Recommendation lifecycle is not built yet."})


@router.post("/sim/control", response_model=dict, responses={501: {"model": ErrorBody}, 502: {"model": ErrorBody}})
async def sim_control(body: SimControlRequest, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Demo controls proxied to simulator /admin/*. Never used by planning logic."""
    try:
        return await ctx.state.client.admin(body.action)
    except SimulatorError as exc:
        raise HTTPException(exc.status if exc.status == 501 else 502, detail={"code": exc.code, "message": exc.message}) from exc

"""Operator-facing API. The frontend reads only these endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AppContext, get_ctx
from app.api.routes.health import system_health
from app.decision import orchestrator
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
    state = await ctx.state.snapshot()
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
    state = await ctx.state.snapshot()
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
    health.components["snapshot"] = ComponentHealth(
        status="HEALTHY" if state and state.meta.freshness in ("FRESH", "FIXTURE") else "DOWN" if state is None else "DEGRADED",
        detail=state.meta.freshness if state else "UNAVAILABLE",
    )
    if plan is not None:
        ctx.recommendation_states = orchestrator.sync_lifecycle(ctx.recommendation_states, plan)
    unknown = sum(1 for st in ctx.recommendation_states.values() if orchestrator.is_unknown(st))
    trip = tripwire.evaluate(state, plan, str(ctx.state.last_error) if ctx.state.last_error else None, unknown_executions=unknown)
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


@router.put("/automation", response_model=AutomationState, responses={409: {"model": ErrorBody}})
async def set_automation(body: AutomationState, ctx: AppContext = Depends(get_ctx)) -> AutomationState:
    if body.mode == "GUARDED_AUTO":
        state = await ctx.state.snapshot()
        plan = build_plan(state) if state is not None else None
        trip = tripwire.evaluate(state, plan, str(ctx.state.last_error) if ctx.state.last_error else None)
        if trip.state == "TRIPPED":
            raise HTTPException(409, detail={"code": "TRIPWIRE_TRIPPED", "message": "GUARDED_AUTO is refused while the safety guard is tripped."})
    ctx.automation = body
    await ctx.db.record("automation", body.model_dump_json())
    return ctx.automation


def _refuse(exc: orchestrator.DecisionError) -> HTTPException:
    return HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})


@router.post(
    "/recommendations/{recommendation_id}/approve",
    response_model=RecommendationState,
    responses={404: {"model": ErrorBody}, 409: {"model": ErrorBody}, 502: {"model": ErrorBody}, 503: {"model": ErrorBody}},
)
async def approve(recommendation_id: str, ctx: AppContext = Depends(get_ctx)) -> RecommendationState:
    """Revalidate on a fresh snapshot, check kill switch, Tripwire and expiry, then execute once (idempotent)."""
    try:
        return await orchestrator.approve(ctx, recommendation_id)
    except orchestrator.DecisionError as exc:
        raise _refuse(exc) from exc


@router.post(
    "/recommendations/{recommendation_id}/reject",
    response_model=RecommendationState,
    responses={404: {"model": ErrorBody}, 409: {"model": ErrorBody}},
)
async def reject(recommendation_id: str, ctx: AppContext = Depends(get_ctx)) -> RecommendationState:
    """Decline a proposed recommendation."""
    try:
        return await orchestrator.reject(ctx, recommendation_id)
    except orchestrator.DecisionError as exc:
        raise _refuse(exc) from exc


@router.post("/sim/control", response_model=dict, responses={501: {"model": ErrorBody}, 502: {"model": ErrorBody}})
async def sim_control(body: SimControlRequest, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Demo controls proxied to simulator /admin/*. Never used by planning logic."""
    try:
        return await ctx.state.client.admin(body.action)
    except SimulatorError as exc:
        raise HTTPException(exc.status if exc.status == 501 else 502, detail={"code": exc.code, "message": exc.message}) from exc

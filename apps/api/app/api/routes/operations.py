"""Operator-facing API. The frontend reads only these endpoints."""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import audit, require_demo_controls, require_operator
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
    PlanApprovalItem,
    PlanApprovalResult,
    RecommendationState,
    SimControlRequest,
)
from app.intelligence import build_plan
from app.persistence import repository
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
            started = time.perf_counter()
            plan = build_plan(state)
            ctx.planner_seconds = time.perf_counter() - started
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
        orchestrator.sync_plan(ctx, plan)
    await orchestrator.track_outcomes(ctx)
    unknown = sum(1 for st in ctx.recommendation_states.values() if orchestrator.is_unknown(st))
    trip = tripwire.evaluate(state, plan, str(ctx.state.last_error) if ctx.state.last_error else None, unknown_executions=unknown,
                          persistence_error=ctx.state.persistence_error,
                          expired_recommendations=orchestrator.just_expired(ctx.recommendation_states, state.run.tick) if state else (),
                          review_required=sorted(ctx.review_required))
    if state is not None and (state.meta.stale or state.meta.freshness not in ("FRESH", "FIXTURE")):
        ctx.counters["snapshot_untrusted_polls"] += 1
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


@router.put("/automation", response_model=AutomationState, responses={401: {"model": ErrorBody}, 409: {"model": ErrorBody}})
async def set_automation(
    body: AutomationState, ctx: AppContext = Depends(get_ctx), actor: str = Depends(require_operator)
) -> AutomationState:
    """Set the operating mode and kill switch. Needs the operator token; the actor is recorded."""
    if body.mode == "GUARDED_AUTO":
        state = await ctx.state.snapshot()
        plan = build_plan(state) if state is not None else None
        err = str(ctx.state.last_error) if ctx.state.last_error else None
        trip = tripwire.evaluate(state, plan, err, persistence_error=ctx.state.persistence_error)
        if trip.state == "TRIPPED":
            raise HTTPException(409, detail={"code": "TRIPWIRE_TRIPPED", "message": "GUARDED_AUTO is refused while the safety guard is tripped."})
    ctx.automation = body
    await ctx.db.record("automation", body.model_dump_json())
    await repository.save_automation(ctx.db, body)
    await audit(ctx, actor, "set_automation", f"{body.mode} kill_switch={body.kill_switch}")
    return ctx.automation


SYSTEM_REFUSALS = ("TRIPWIRE_TRIPPED", "KILL_SWITCH", "SNAPSHOT_UNAVAILABLE", "EXECUTION_UNKNOWN", "PERSISTENCE_FAILURE")


@router.post(
    "/plan/approve",
    response_model=PlanApprovalResult,
    responses={401: {"model": ErrorBody}, 409: {"model": ErrorBody}},
)
async def approve_plan(ctx: AppContext = Depends(get_ctx), actor: str = Depends(require_operator)) -> PlanApprovalResult:
    """Manual Demo: approve every proposed recommendation of the current plan, one at a time.

    Each one is revalidated on a fresh snapshot just before its POST. A system-level refusal (kill switch, tripped guard,
    unavailable snapshot, unclear execution) stops the run; a refusal specific to one recommendation skips it.
    """
    if ctx.automation.mode != "MANUAL_DEMO":
        raise HTTPException(409, detail={"code": "NOT_MANUAL_DEMO", "message": "Approving a whole plan needs MANUAL_DEMO mode."})
    state = await ctx.state.refresh()
    if state is None:
        raise HTTPException(503, detail={"code": "SNAPSHOT_UNAVAILABLE", "message": "No fresh trusted snapshot; not executing."})
    plan = build_plan(state)
    orchestrator.sync_plan(ctx, plan)
    items: list[PlanApprovalItem] = []
    stopped = False
    for rec in plan.recommendations:
        st = ctx.recommendation_states.get(rec.id)
        if st is None or st.status != "PROPOSED":
            continue
        try:
            done = await orchestrator.approve(ctx, rec.id)
        except orchestrator.DecisionError as exc:
            await audit(ctx, actor, "approve_plan", rec.id, f"refused:{exc.code}")
            items.append(PlanApprovalItem(recommendation_id=rec.id, outcome="REFUSED", code=exc.code, message=exc.message))
            if exc.code in SYSTEM_REFUSALS:
                stopped = True
                break
            continue
        await audit(ctx, actor, "approve_plan", rec.id, done.status)
        items.append(PlanApprovalItem(recommendation_id=rec.id, outcome="EXECUTED", status=done.status, message=done.message))
    return PlanApprovalResult(items=items, stopped_early=stopped)


def _refuse(exc: orchestrator.DecisionError) -> HTTPException:
    return HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})


@router.post(
    "/recommendations/{recommendation_id}/approve",
    response_model=RecommendationState,
    responses={
        401: {"model": ErrorBody},
        404: {"model": ErrorBody},
        409: {"model": ErrorBody},
        502: {"model": ErrorBody},
        503: {"model": ErrorBody},
    },
)
async def approve(
    recommendation_id: str, ctx: AppContext = Depends(get_ctx), actor: str = Depends(require_operator)
) -> RecommendationState:
    """Revalidate on a fresh snapshot, check kill switch, Tripwire and expiry, then execute once (idempotent). Needs the operator token."""
    try:
        result = await orchestrator.approve(ctx, recommendation_id)
    except orchestrator.DecisionError as exc:
        await audit(ctx, actor, "approve", recommendation_id, f"refused:{exc.code}")
        raise _refuse(exc) from exc
    await audit(ctx, actor, "approve", recommendation_id, result.status)
    return result


@router.post(
    "/recommendations/{recommendation_id}/reject",
    response_model=RecommendationState,
    responses={401: {"model": ErrorBody}, 404: {"model": ErrorBody}, 409: {"model": ErrorBody}},
)
async def reject(
    recommendation_id: str, ctx: AppContext = Depends(get_ctx), actor: str = Depends(require_operator)
) -> RecommendationState:
    """Decline a proposed recommendation. Needs the operator token."""
    try:
        result = await orchestrator.reject(ctx, recommendation_id)
    except orchestrator.DecisionError as exc:
        await audit(ctx, actor, "reject", recommendation_id, f"refused:{exc.code}")
        raise _refuse(exc) from exc
    await audit(ctx, actor, "reject", recommendation_id, result.status)
    return result


@router.post(
    "/sim/control",
    response_model=dict,
    responses={401: {"model": ErrorBody}, 403: {"model": ErrorBody}, 501: {"model": ErrorBody}, 502: {"model": ErrorBody}},
)
async def sim_control(body: SimControlRequest, ctx: AppContext = Depends(get_ctx), actor: str = Depends(require_demo_controls)) -> dict:
    """Demo controls proxied to simulator /admin/*. Needs the operator token and DEMO_CONTROLS_ENABLED. Never used by planning logic."""
    await audit(ctx, actor, "sim_control", body.action)
    try:
        return await ctx.state.client.admin(body.action)
    except SimulatorError as exc:
        raise HTTPException(exc.status if exc.status == 501 else 502, detail={"code": exc.code, "message": exc.message}) from exc

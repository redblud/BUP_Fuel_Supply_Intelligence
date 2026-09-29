"""AI briefing endpoint. Advisory text only; it reads the same state and plan as the dashboard and changes nothing."""

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.ai import groq
from app.api.auth import audit, require_operator
from app.api.deps import AppContext, get_ctx
from app.domain.models import AiBrief, AiBriefRequest, ErrorBody
from app.intelligence import build_plan
from app.safety import tripwire

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post(
    "/brief",
    response_model=AiBrief,
    responses={
        401: {"model": ErrorBody},
        409: {"model": ErrorBody},
        502: {"model": ErrorBody},
        503: {"model": ErrorBody},
        504: {"model": ErrorBody},
    },
)
async def brief(body: AiBriefRequest, ctx: AppContext = Depends(get_ctx), actor: str = Depends(require_operator)) -> AiBrief:
    """Ask Groq for a short briefing (or an answer to one question) about the current situation.

    Needs the operator token because it sends derived plan data to an external service. The answer carries the data
    freshness it was written from, so a stale snapshot is never presented as live.
    """
    state = await ctx.state.snapshot()
    if state is None:
        raise HTTPException(409, detail={"code": "SNAPSHOT_UNAVAILABLE", "message": "There is no trusted snapshot to brief on."})
    plan = build_plan(state)
    err = str(ctx.state.last_error) if ctx.state.last_error else None
    trip = tripwire.evaluate(state, plan, err, persistence_error=ctx.state.persistence_error, review_required=sorted(ctx.review_required))
    if ctx.ai_client is None:
        ctx.ai_client = httpx.AsyncClient()
    try:
        text = await groq.complete(ctx.ai_client, ctx.settings, groq.build_messages(groq.build_context(state, plan, trip), body.question))
    except groq.AiError as exc:
        await audit(ctx, actor, "ai_brief", state.meta.run_id, f"refused:{exc.code}")
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message}) from exc
    await audit(ctx, actor, "ai_brief", state.meta.run_id, "ok")
    return AiBrief(text=text, model=ctx.settings.groq_model, run_id=state.meta.run_id, tick=state.run.tick, freshness=state.meta.freshness)

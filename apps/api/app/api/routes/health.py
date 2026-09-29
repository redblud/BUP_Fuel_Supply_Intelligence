from fastapi import APIRouter, Depends

from app.api.deps import AppContext, get_ctx
from app.domain.models import ComponentHealth, SystemHealth
from app.simulator.errors import SimulatorError

router = APIRouter(prefix="/api", tags=["health"])

RANK = {"HEALTHY": 0, "UNKNOWN": 1, "DEGRADED": 2, "DOWN": 3}


async def system_health(ctx: AppContext) -> SystemHealth:
    c: dict[str, ComponentHealth] = {"api": ComponentHealth(status="HEALTHY")}
    try:
        sim = await ctx.state.client.get_health()
        c["simulator"] = ComponentHealth(
            status="HEALTHY",
            detail=f"{ctx.settings.simulator_mode} · {sim['simulation']['status']} · tick {sim['simulation']['tick']}",
        )
    except SimulatorError as exc:
        c["simulator"] = ComponentHealth(status="DOWN", detail=str(exc))
    try:
        startups = await ctx.db.ping()
        c["database"] = ComponentHealth(status="HEALTHY", detail=f"sqlite · {startups} recorded startups")
    except Exception as exc:  # noqa: BLE001
        c["database"] = ComponentHealth(status="DOWN", detail=str(exc))
    c["sse"] = ComponentHealth(status="UNKNOWN", detail="not implemented")
    c["execution"] = ComponentHealth(status="UNKNOWN", detail="not implemented")
    worst = max(c.values(), key=lambda h: RANK[h.status]).status
    return SystemHealth(status="HEALTHY" if worst == "UNKNOWN" else worst, components=c)


@router.get("/health", response_model=SystemHealth)
async def health(ctx: AppContext = Depends(get_ctx)) -> SystemHealth:
    return await system_health(ctx)


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}

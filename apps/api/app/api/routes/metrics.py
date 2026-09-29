"""Prometheus text exposition at /metrics. Hand written to avoid a dependency; the format is stable."""

from collections import Counter
from collections.abc import Iterable

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.api.deps import AppContext, get_ctx
from app.domain.models import RecommendationState

router = APIRouter(tags=["metrics"])


def _line(name: str, value: float, labels: dict[str, str] | None = None) -> str:
    tags = "{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}" if labels else ""
    return f"{name}{tags} {value}"


def render_metrics(
    *,
    snapshot_age_seconds: float | None,
    tick: int | None,
    latest_sse_tick: int | None,
    counters: Counter[str],
    planner_seconds: float | None,
    recommendation_states: Iterable[RecommendationState],
    service_level: float | None,
) -> str:
    """Pure: values in, exposition text out. Series with no value yet are omitted rather than reported as zero."""
    out: list[str] = []
    if snapshot_age_seconds is not None:
        out += ["# TYPE fuelops_snapshot_age_seconds gauge", _line("fuelops_snapshot_age_seconds", round(snapshot_age_seconds, 3))]
    if tick is not None:
        out += ["# TYPE fuelops_simulator_tick gauge", _line("fuelops_simulator_tick", tick)]
        if latest_sse_tick is not None:
            out += ["# TYPE fuelops_tick_lag gauge", _line("fuelops_tick_lag", max(latest_sse_tick - tick, 0))]
    if planner_seconds is not None:
        out += ["# TYPE fuelops_planner_runtime_seconds gauge", _line("fuelops_planner_runtime_seconds", round(planner_seconds, 6))]
    if service_level is not None:
        out += ["# TYPE fuelops_service_level gauge", _line("fuelops_service_level", service_level)]
    by_status = Counter(s.status for s in recommendation_states)
    out.append("# TYPE fuelops_recommendations gauge")
    out += [_line("fuelops_recommendations", n, {"status": status}) for status, n in sorted(by_status.items())]
    for name in ("allocation_attempts", "allocation_conflicts", "allocation_failures", "snapshot_untrusted_polls"):
        out += [f"# TYPE fuelops_{name}_total counter", _line(f"fuelops_{name}_total", counters.get(name, 0))]
    return "\n".join(out) + "\n"


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
async def metrics(ctx: AppContext = Depends(get_ctx)) -> PlainTextResponse:
    """Prometheus scrape target. Reads what the API already holds; it never triggers a simulator read."""
    state = ctx.state.latest
    return PlainTextResponse(
        render_metrics(
            snapshot_age_seconds=ctx.state.last_trusted_age_seconds(),
            tick=state.run.tick if state else None,
            latest_sse_tick=ctx.state.latest_sse_tick,
            counters=ctx.counters,
            planner_seconds=ctx.planner_seconds,
            recommendation_states=ctx.recommendation_states.values(),
            service_level=state.metrics.service_level if state and state.metrics else None,
        ),
        media_type="text/plain; version=0.0.4",
    )

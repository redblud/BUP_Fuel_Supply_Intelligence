"""Pure forecast, topology, risk, and planning functions. No HTTP, database, clock, or FastAPI.

The API calls `build_plan(state, policy)` and nothing else. Keep that signature stable.
"""

from app.domain.models import NetworkState, Plan, Policy
from app.intelligence.forecast import forecast_demand, project_inventory
from app.intelligence.planner import PLANNER_VERSION, plan_replenishment
from app.intelligence.risk import assess_risk

__all__ = ["NetworkState", "Plan", "Policy", "build_plan"]


def build_plan(state: NetworkState, policy: Policy | None = None) -> Plan:
    """Forecast -> projection -> risk -> plan. Deterministic.

    If the primary planner raises, returns no recommendations with planner_version
    'fallback' and a warning, so Tripwire can raise PRIMARY_PLANNER_FAILED.
    """
    policy = policy or Policy()
    forecasts = forecast_demand(state, policy)
    projections = project_inventory(state, forecasts, policy)
    risks = assess_risk(state, projections)
    warnings: list[str] = []
    try:
        recommendations = plan_replenishment(state, forecasts, risks, projections, policy)
        version = PLANNER_VERSION
    except Exception as exc:
        recommendations, version = [], "fallback"
        warnings.append(f"PRIMARY_PLANNER_FAILED: {type(exc).__name__}: {exc}")
    return Plan(
        tick=state.run.tick,
        run_id=state.meta.run_id,
        planner_version=version,
        forecasts=forecasts,
        projections=projections,
        risks=risks,
        recommendations=recommendations,
        warnings=warnings,
    )
